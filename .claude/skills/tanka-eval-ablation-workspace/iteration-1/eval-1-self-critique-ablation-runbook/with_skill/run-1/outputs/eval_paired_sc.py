#!/usr/bin/env python3
"""eval_paired_sc.py — TANKA_SELF_CRITIQUE の paired (お題ごと交互) A/B 実験ドライバ。

設置場所: app/backend/eval/ (eval.sh / eval-repeat.sh と同じ場所に置いて使う)

なぜ専用ドライバか:
  前回の self-critique 実験 (FINDINGS.md §5.5) は blocked A/B (ON を全部 → OFF を全部) で
  LM Studio の sustained-load 劣化が後半 run に交絡し、結論不能に終わった。
  教訓は「お題ごとに ON/OFF を交互実行 (paired) し、両群に同じ LM Studio 状態を経験させる」。
  eval.sh は 1 invocation = 1 variant なのでお題ごとの env 切替ができない。これを行うのが本ドライバ。

サブコマンド:
  run      実験を実行 (resume 可能: 中断後に再実行すると未完了分から続行する)
  status   進捗の確認 (state ファイルを読むだけ。実行中でも安全)
  analyze  事前登録済みの判定基準でレポートを出す (GET のみ。何度でも実行可)

設計 (事前登録 — RUNBOOK.md §2 と同一。実行後に変更しないこと):
  - お題: eval_themes.json の 50 題。各お題で ON / OFF の両方を生成 (計 100 生成)
  - ペア内の実行順は交互: pair 0 = ON→OFF, pair 1 = OFF→ON, ... (短期ドリフトの相殺)
  - 主要指標: 完了ペアの final_score 差 (ON−OFF) の平均
  - ノイズ下限 (repo 規約, FINDINGS.md §2): 2 × 9.6 / sqrt(n)。n=50 で ±2.7
  - |平均差| がノイズ下限以下なら「判定不能 (効果はノイズ以下)」と報告する。これも正当な結論
  - 失敗した生成は再試行しない。両腕が揃わないペアは解析から除外 (除外数は報告)

env 上書き (通常は不要):
  EVAL_BACKEND          backend URL (既定 http://localhost:8001)
  EVAL_THEMES           お題ファイル (既定 <script_dir>/eval_themes.json)
  EVAL_STATE            state ファイル (既定 <script_dir>/results/eval-paired-sc-state.json)
  EVAL_STOP_AFTER_MIN   タイムボックス分 (既定 420)。超過後は新規ペアを始めない (進行中ペアは完遂)
  EVAL_TASK_TIMEOUT_MIN 1 生成の watchdog 分 (既定 45)。超過したらタスクを cancel し failed 扱い
"""

from __future__ import annotations

import json
import math
import os
import signal
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# ── 定数 (事前登録の一部) ──
HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))  # app/ (docker compose のある場所)
BACKEND = os.environ.get("EVAL_BACKEND", "http://localhost:8001")
THEMES_FILE = os.environ.get("EVAL_THEMES", os.path.join(HERE, "eval_themes.json"))
STATE_FILE = os.environ.get("EVAL_STATE", os.path.join(HERE, "results", "eval-paired-sc-state.json"))
PID_FILE = os.path.join(HERE, "results", "eval-paired-sc.pid")
ENV_VAR = "TANKA_SELF_CRITIQUE"
ARM_VALUES = {"on": "1", "off": "0"}
VARIANT_NAMES = {"on": "sc-paired-on", "off": "sc-paired-off"}
EXPECTED_MODEL = "llm-jp-4-8b-thinking"
SIGMA = 9.6  # 同一お題・同一設定の score stdev 実測値 (FINDINGS.md §2)
STOP_AFTER_MIN = float(os.environ.get("EVAL_STOP_AFTER_MIN", "420"))
TASK_TIMEOUT_MIN = float(os.environ.get("EVAL_TASK_TIMEOUT_MIN", "45"))
POLL_SEC = 3

STOP = False  # SIGINT/SIGTERM で立つフラグ。安全な境界で run を畳む


class Fatal(Exception):
    """系統的障害 (docker 失敗 / env 適用検証失敗 など)。続行せず run を中断する。"""


# ────────────────────────── 小物 ──────────────────────────

def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def api(path: str, method: str = "GET", body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BACKEND + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def api_retry(path: str, method: str = "GET", body: dict | None = None, tries: int = 3):
    """一時的な接続断 (backend 再作成直後など) に備えた薄いリトライ。HTTP 4xx は即時 raise。"""
    last: Exception | None = None
    for i in range(tries):
        try:
            return api(path, method, body)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"API {method} {path} failed after {tries} tries: {last}")


def save_state(st: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def load_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ────────────────────────── backend / docker 操作 (run 用) ──────────────────────────

def docker(args: list[str], extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """docker compose を APP_DIR で実行する。

    1 実験 1 変数の規律: ホスト shell に他の TANKA_* / LM_STUDIO_MODEL が export
    されていても compose の `${VAR:-default}` 補間へ漏れないよう、env から落とす
    (落とすと compose 側の既定値が効く)。上書きするのは extra_env の変数だけ。"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("TANKA_") and k != "LM_STUDIO_MODEL"}
    env.update(extra_env or {})
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=APP_DIR, env=env, capture_output=True, text=True, timeout=300,
    )


def wait_healthy(timeout_s: int = 180) -> dict:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            h = api("/api/health", timeout=15)
            if h.get("status") == "ok":
                return h
            last = h.get("status", "?")
        except Exception as e:
            last = str(e)
        time.sleep(3)
    raise RuntimeError(f"backend が {timeout_s}s 以内に healthy になりません (last={last})")


def read_container_arm() -> str | None:
    """実行中コンテナの実際の env を読む (読み取り専用)。判別不能なら None。"""
    p = docker(["exec", "-T", "backend", "printenv", ENV_VAR])
    if p.returncode != 0:
        return None
    val = p.stdout.strip()
    for arm, v in ARM_VALUES.items():
        if val == v:
            return arm
    return None


CURRENT_ARM: str | None = None
SWITCHED = False  # この run が一度でも backend を再作成したか (finally の復元要否判定)


def ensure_arm(arm: str) -> None:
    """backend を指定 arm の env で再作成する。既に一致していれば何もしない。

    env は `docker compose restart` では効かない (up 時に固定される) ため、
    値が変わるときは container 再作成 (`up -d --no-deps backend`) が必要。SKILL.md §Step 2。
    適用後は printenv で必ず検証する (「同じものを 2 回測る」事故の防止)。"""
    global CURRENT_ARM, SWITCHED
    if CURRENT_ARM == arm:
        return
    log(f"  [switch] {ENV_VAR}={ARM_VALUES[arm]} で backend を再作成 ...")
    CURRENT_ARM = None  # 検証完了まで「不明」扱い (途中失敗時に finally が必ず復元を試みる)
    p = docker(["up", "-d", "--no-deps", "backend"], extra_env={ENV_VAR: ARM_VALUES[arm]})
    SWITCHED = True
    if p.returncode != 0:
        raise Fatal(f"docker compose up failed: {p.stderr.strip()[:500]}")
    try:
        h = wait_healthy()
    except RuntimeError as e:
        raise Fatal(str(e))
    if h.get("configured_model") != EXPECTED_MODEL:
        raise Fatal(f"configured_model が想定外: {h.get('configured_model')} (期待 {EXPECTED_MODEL})")
    got = None
    for _ in range(5):
        got = read_container_arm()
        if got is not None:
            break
        time.sleep(2)
    if got != arm:
        raise Fatal(f"env 適用検証に失敗: printenv {ENV_VAR} -> {got!r} (期待 {arm!r})。実験を中断します")
    CURRENT_ARM = arm
    log(f"  [switch] ok ({ENV_VAR}={ARM_VALUES[arm]} を printenv で確認)")


def active_task(sid: str) -> str | None:
    return api_retry(f"/api/sessions/{sid}/active-task").get("task_id")


def wait_idle(sid: str, timeout_min: float) -> None:
    deadline = time.time() + timeout_min * 60
    while active_task(sid):
        if time.time() > deadline:
            raise RuntimeError(f"session {sid} の先行タスクが {timeout_min} 分待っても終わりません")
        time.sleep(POLL_SEC)


def count_theme(sid: str, theme: str) -> int:
    s = api_retry(f"/api/sessions/{sid}")
    return sum(1 for m in s.get("messages", [])
               if m.get("kind") == "tanka" and m.get("theme") == theme)


# ────────────────────────── run ──────────────────────────

def _on_signal(signum, frame):
    global STOP
    STOP = True
    log(f"[signal] {signal.Signals(signum).name} 受信。安全な境界で停止します (再実行で resume 可能)")


def acquire_pid_lock() -> None:
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read().strip())
            os.kill(pid, 0)
            raise SystemExit(f"既に実行中です (pid={pid})。複数 eval の並行実行は禁止 (SKILL.md §Step 3)。"
                             f" 止めるには: kill {pid}")
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale pid file
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def init_state() -> dict:
    with open(THEMES_FILE) as f:
        themes = [t["theme"] for t in json.load(f)["themes"]]
    if len(set(themes)) != len(themes):
        raise SystemExit("お題ファイルに重複があります (theme でペア照合するため一意が必要)")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    st = {
        "experiment": "self-critique paired ablation",
        "var": ENV_VAR,
        "expected_model": EXPECTED_MODEL,
        "themes_file": THEMES_FILE,
        "created": ts,
        "started_epoch": time.time(),
        "sessions": {},
        "pairs": [
            {"idx": i, "theme": th,
             "order": ["on", "off"] if i % 2 == 0 else ["off", "on"],
             "arms": {"on": {"status": "pending"}, "off": {"status": "pending"}}}
            for i, th in enumerate(themes)
        ],
    }
    for arm in ("on", "off"):
        sess = api_retry("/api/sessions", "POST", {"title": f"[eval] {VARIANT_NAMES[arm]} {ts}"})
        st["sessions"][arm] = sess["id"]
        log(f"session ({arm}): {sess['id']}  title={sess['title']}")
    save_state(st)
    return st


def preflight() -> None:
    h = wait_healthy(timeout_s=30)
    problems = []
    if h.get("configured_model") != EXPECTED_MODEL:
        problems.append(f"configured_model={h.get('configured_model')} (期待 {EXPECTED_MODEL})。"
                        f" UI からの切替が残っている可能性")
    if not h.get("model_loaded"):
        problems.append("model_loaded=false。LM Studio でモデルをロードしてから実行")
    running = [s for s in api_retry("/api/sessions") if s.get("active_task")]
    if running:
        problems.append(f"他のタスクが実行中: {[s['id'] for s in running]} — 終了を待つか cancel してから実行")
    p = docker(["version"])
    if p.returncode != 0:
        problems.append(f"docker compose が使えません: {p.stderr.strip()[:200]}")
    if problems:
        raise SystemExit("preflight 失敗:\n  - " + "\n  - ".join(problems))
    try:
        n_fail = api("/api/failures?limit=1").get("count")
        log(f"preflight ok (failures 蓄積: {n_fail} 件 — リセットするなら run 開始前に。RUNBOOK §4)")
    except Exception:
        log("preflight ok")


def run_one(st: dict, pair: dict, arm: str) -> None:
    a = pair["arms"][arm]
    if a["status"] in ("done", "failed", "skipped"):
        return
    sid = st["sessions"][arm]
    theme = pair["theme"]
    label = f"[{pair['idx'] + 1:2d}/{len(st['pairs'])}] {arm:3s} {theme[:24]}"

    wait_idle(sid, TASK_TIMEOUT_MIN)  # 前回中断時の走り残しタスクがあれば待つ
    if count_theme(sid, theme) >= 1:
        a["status"] = "done"
        a["note"] = "recovered-on-resume"
        save_state(st)
        log(f"{label} ... resume 回復 (生成済みを検出)")
        return
    if a["status"] == "attempted":
        # 前回 POST までは行ったが結果が無い → 事前登録どおり再試行しない
        a["status"] = "failed"
        a["note"] = "no-result-after-attempt"
        save_state(st)
        log(f"{label} ... failed (前回 attempt の結果なし。再試行しない)")
        return

    ensure_arm(arm)
    a["status"] = "attempted"
    a["started"] = datetime.now().isoformat(timespec="seconds")
    t0 = time.time()
    save_state(st)

    try:
        res = api_retry("/api/tanka", "POST", {"session_id": sid, "theme": theme})
        a["task_id"] = res.get("task_id")
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise
        # 409 = タスクは既に走っている (リトライ時に初回 POST が実は通っていた等)。polling に進む
        log(f"{label} ... 409 (タスクは既に走行中とみなして待機)")
    save_state(st)
    log(f"{label} ... task={a.get('task_id')}")

    deadline = time.time() + TASK_TIMEOUT_MIN * 60
    while True:
        if not active_task(sid):
            break
        if time.time() > deadline:
            log(f"{label} ... watchdog ({TASK_TIMEOUT_MIN:.0f}分) 超過 → cancel")
            try:
                tid = a.get("task_id") or active_task(sid)
                if tid:
                    api_retry(f"/api/tasks/{tid}/cancel", "POST", {})
            except Exception as e:
                log(f"  cancel 失敗 (続行): {e}")
            a["note"] = "watchdog-cancelled"
            time.sleep(POLL_SEC * 2)
            break
        if STOP:
            raise KeyboardInterrupt  # 走行中タスクは backend 側で完走する。resume で回収される
        time.sleep(POLL_SEC)

    ok = count_theme(sid, theme) >= 1
    a["status"] = "done" if ok else "failed"
    a["ended"] = datetime.now().isoformat(timespec="seconds")
    a["duration_s"] = round(time.time() - t0, 1)
    save_state(st)
    log(f"{label} ... {a['status']} ({a['duration_s']:.0f}s)")


def cmd_run() -> None:
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    acquire_pid_lock()
    global CURRENT_ARM
    try:
        preflight()
        st = load_state()
        if st is None:
            st = init_state()
            log(f"新規実験を開始: {len(st['pairs'])} ペア (= {2 * len(st['pairs'])} 生成)")
        else:
            if st.get("themes_file") != THEMES_FILE:
                raise SystemExit(f"state のお題ファイル ({st.get('themes_file')}) と現在の指定 "
                                 f"({THEMES_FILE}) が不一致。before/after は同一ファイルが必須")
            done = sum(1 for p in st["pairs"] for x in p["arms"].values() if x["status"] == "done")
            log(f"既存 state から resume: {done}/{2 * len(st['pairs'])} 生成が完了済み")
        detected = read_container_arm()
        log(f"現在の backend arm: {detected} — 信頼せず、最初の生成前に必ず再作成して env を既定に揃える")
        CURRENT_ARM = None  # 走っていたコンテナの他 TANKA_* 混入を防ぐ (初回 ensure_arm が必ず再作成)
        start = st.get("started_epoch") or time.time()

        consec_fail = 0
        for pair in st["pairs"]:
            statuses = {a: pair["arms"][a]["status"] for a in ("on", "off")}
            if all(s in ("done", "failed", "skipped") for s in statuses.values()):
                continue
            if STOP:
                break
            # タイムボックス: 未着手ペアのみ打ち切る (片腕済みのペアは完遂してペアを守る)
            elapsed_min = (time.time() - start) / 60
            untouched = all(s == "pending" for s in statuses.values())
            if untouched and elapsed_min > STOP_AFTER_MIN:
                for a in ("on", "off"):
                    if pair["arms"][a]["status"] == "pending":
                        pair["arms"][a]["status"] = "skipped"
                        pair["arms"][a]["note"] = "timebox"
                save_state(st)
                continue
            for arm in pair["order"]:
                if STOP:
                    break
                try:
                    run_one(st, pair, arm)
                except (KeyboardInterrupt, Fatal):
                    raise
                except Exception as e:
                    pair["arms"][arm]["status"] = "failed"
                    pair["arms"][arm]["note"] = f"error: {e}"[:300]
                    save_state(st)
                    log(f"  !! arm error ({arm} / {pair['theme'][:20]}): {e}")
                if pair["arms"][arm]["status"] == "failed":
                    consec_fail += 1
                elif pair["arms"][arm]["status"] == "done":
                    consec_fail = 0
                if consec_fail >= 12:
                    raise Fatal("12 連続失敗 — 系統的障害 (LM Studio 停止等) の疑い。run を中断 (resume 可)")
                if consec_fail and consec_fail % 4 == 0:
                    log(f"  {consec_fail} 連続失敗 → LM Studio 回復待ちで 10 分クールダウン"
                        f" (FINDINGS §5: idle 後に回復する)")
                    for _ in range(60):
                        if STOP:
                            break
                        time.sleep(10)

        done = sum(1 for p in st["pairs"] for x in p["arms"].values() if x["status"] == "done")
        failed = sum(1 for p in st["pairs"] for x in p["arms"].values() if x["status"] == "failed")
        skipped = sum(1 for p in st["pairs"] for x in p["arms"].values() if x["status"] == "skipped")
        log(f"run 終了: done={done} failed={failed} skipped={skipped} (全 {2 * len(st['pairs'])})")
        log(f"次: python3 {os.path.basename(__file__)} analyze")
    except KeyboardInterrupt:
        log("中断しました。再実行すると未完了分から resume します")
    except Fatal as e:
        log(f"!! run 中断 (系統的障害): {e}")
        log("!! 原因を解消後、再実行すると未完了分から resume します")
        sys.exit(1)  # finally の復元は走る
    finally:
        # 後片付け: この run が backend を再作成した場合のみ、既定 (self-critique ON) に戻す。
        # 一度も switch していない (preflight 失敗等) のに再作成すると、走行中の他タスクを
        # 巻き込みかねないので何もしない。
        if SWITCHED and CURRENT_ARM != "on":
            try:
                ensure_arm("on")
                log("backend を既定 (TANKA_SELF_CRITIQUE=1) に復元しました")
            except Exception as e:
                log(f"!! backend の復元に失敗: {e}")
                log(f"!! 手動で復元してください: cd {APP_DIR} && {ENV_VAR}=1 docker compose up -d --no-deps backend")
        elif SWITCHED:
            log("backend は既定 (TANKA_SELF_CRITIQUE=1) のままです")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)


# ────────────────────────── status ──────────────────────────

def cmd_status() -> None:
    st = load_state()
    if st is None:
        print(f"state がありません: {STATE_FILE}")
        return
    counts: dict[str, int] = {}
    for p in st["pairs"]:
        for a in ("on", "off"):
            s = p["arms"][a]["status"]
            counts[s] = counts.get(s, 0) + 1
    total = 2 * len(st["pairs"])
    print(f"experiment: {st.get('experiment')}  (created {st.get('created')})")
    print(f"sessions: on={st['sessions'].get('on')}  off={st['sessions'].get('off')}")
    print(f"progress: {counts.get('done', 0)}/{total} done, "
          f"{counts.get('failed', 0)} failed, {counts.get('skipped', 0)} skipped, "
          f"{counts.get('attempted', 0)} in-flight, {counts.get('pending', 0)} pending")
    if st.get("started_epoch"):
        print(f"elapsed: {(time.time() - st['started_epoch']) / 60:.0f} min")
    for p in st["pairs"]:
        marks = {"pending": "·", "attempted": "…", "done": "o", "failed": "x", "skipped": "-"}
        on_m = marks.get(p["arms"]["on"]["status"], "?")
        off_m = marks.get(p["arms"]["off"]["status"], "?")
        dur = "/".join(str(int(p["arms"][a].get("duration_s") or 0)) for a in ("on", "off"))
        print(f"  [{p['idx'] + 1:2d}] on:{on_m} off:{off_m}  ({dur}s)  {p['theme'][:30]}")


# ────────────────────────── analyze ──────────────────────────

def _fetch_arm(sid: str) -> tuple[dict, list[str]]:
    """セッションの tanka メッセージを theme → message の dict に。重複は最初の 1 件を採用
    (resume 由来の二重生成は交互実行の順序を守った方 = 先に完了した方を使う)。"""
    s = api_retry(f"/api/sessions/{sid}")
    msgs = s.get("messages", [])
    by_theme: dict[str, dict] = {}
    dups: list[str] = []
    prev_user_at = None
    for m in msgs:
        if m.get("kind") == "user":
            prev_user_at = parse_iso(m.get("created_at"))
            continue
        if m.get("kind") != "tanka":
            continue
        th = m.get("theme")
        end = parse_iso(m.get("created_at"))
        if prev_user_at and end:
            m["_wall_s"] = (end - prev_user_at).total_seconds()
        if th in by_theme:
            dups.append(th)
        else:
            by_theme[th] = m
    return by_theme, dups


def _first_pass(m: dict) -> bool:
    v = m.get("validations") or []
    return bool(v and v[0].get("resolved"))


def _overall_pass(m: dict) -> bool:
    return any(v.get("resolved") for v in (m.get("validations") or []))


def _sc_phase(m: dict) -> bool | None:
    phases = m.get("phases")
    if not phases:
        return None  # 旧データ (phases 未永続化) は判別不能
    return any(p.get("phase") == "self_critique" for p in phases)


def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def cmd_analyze(state_path: str, save: bool) -> None:
    with open(state_path) as f:
        st = json.load(f)
    sid_on, sid_off = st["sessions"]["on"], st["sessions"]["off"]
    on_map, on_dups = _fetch_arm(sid_on)
    off_map, off_dups = _fetch_arm(sid_off)

    print("=" * 74)
    print("self-critique paired ablation — 解析レポート")
    print(f"  state:    {state_path}")
    print(f"  themes:   {st.get('themes_file')}")
    print(f"  sessions: on={sid_on}  off={sid_off}")
    print("=" * 74)
    print("""
【事前登録済みの判定基準 (動かさない)】
  主要指標: 完了ペアの final_score 差 (ON−OFF) の平均
  ノイズ下限: ±2×9.6/sqrt(n)  (FINDINGS.md §2 の規約。n=50 で ±2.7)
  1. arm 整合性違反 (phases/model の不一致) > 0     → run 無効 (env 適用ミス)。再実行
  2. 完了ペア < 80% (50 題なら 40 ペア)             → 結果は参考値。再測定を推奨
  3. 前半/後半の平均差が逆符号で双方ノイズ下限超え   → 時間交絡。判定不能
  4. |平均差| > ノイズ下限                          → 有意 (＋なら ON 維持 / −なら OFF 化を提案)
  5. |平均差| ≤ ノイズ下限                          → 「効果はノイズ以下」(判定不能も正当な結論)
  副次 (方向の参考のみ): 初回合格率 / 平均 attempt / plateau 率 / 生成時間 (コスト)
""")

    # ── 1. arm 整合性 ──
    problems = []
    unknown_phase = 0
    for arm, mp, want in (("on", on_map, True), ("off", off_map, False)):
        for th, m in mp.items():
            sc = _sc_phase(m)
            if sc is None:
                unknown_phase += 1
            elif sc != want:
                problems.append(f"arm={arm} theme={th!r}: self_critique phase {'有' if sc else '無'} (期待と逆)")
            mdl = m.get("model")
            if mdl is not None and mdl != st.get("expected_model"):
                problems.append(f"arm={arm} theme={th!r}: model={mdl} (期待 {st.get('expected_model')})")
    print("[1] arm 整合性 (phases に self_critique があるのは ON 群のみ / model 均一)")
    if problems:
        for p in problems[:20]:
            print(f"    NG  {p}")
        print(f"    → 整合性違反 {len(problems)} 件。** run 無効 (env 適用ミスの疑い) — 判定に使わないこと **")
    else:
        print("    ok (違反 0 件)")
    if unknown_phase:
        print(f"    note: phases 未記録のメッセージ {unknown_phase} 件 (旧データ?) は判別対象外")
    if on_dups or off_dups:
        print(f"    note: theme 重複 (resume 由来, 先勝ち採用): on={on_dups} off={off_dups}")

    # ── 2. ペア構築 ──
    themes_in_order = [p["theme"] for p in st["pairs"]]
    pairs = []
    for i, th in enumerate(themes_in_order):
        a, b = on_map.get(th), off_map.get(th)
        sa = a.get("final_score") if a else None
        sb = b.get("final_score") if b else None
        if isinstance(sa, int) and isinstance(sb, int):
            pairs.append({"idx": i, "theme": th, "on": sa, "off": sb, "diff": sa - sb})
    n_total = len(themes_in_order)
    n = len(pairs)
    print(f"\n[2] ペア完成度: {n}/{n_total} ペア完了 ({n / n_total * 100:.0f}%)"
          + ("  ** 80% 未満 → 結果は参考値。再測定を推奨 **" if n < 0.8 * n_total else "  ok"))
    llm_err_on = sum(1 for m in on_map.values() if m.get("llm_error"))
    llm_err_off = sum(1 for m in off_map.values() if m.get("llm_error"))
    if llm_err_on or llm_err_off:
        print(f"    note: llm_error を含む生成 on={llm_err_on} off={llm_err_off} (LM Studio 劣化の指標)")
    if n == 0:
        print("\n完了ペアがありません。解析を終了します")
        return

    # ── 3. 主要指標 ──
    diffs = [p["diff"] for p in pairs]
    mean_d = statistics.mean(diffs)
    sd_d = statistics.stdev(diffs) if n > 1 else 0.0
    se_d = sd_d / math.sqrt(n) if n > 1 else float("nan")
    floor = 2 * SIGMA / math.sqrt(n)
    mean_on = statistics.mean(p["on"] for p in pairs)
    mean_off = statistics.mean(p["off"] for p in pairs)
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    zero = n - pos - neg
    print(f"\n[3] 主要指標: final_score (完了ペアのみ, n={n})")
    print(f"    ON 平均  : {mean_on:6.2f}")
    print(f"    OFF 平均 : {mean_off:6.2f}")
    print(f"    平均差 (ON−OFF): {mean_d:+.2f}   [ノイズ下限 ±{floor:.2f}]")
    print(f"    ペア差の分布: +{pos} / 0:{zero} / −{neg}   sd={sd_d:.1f}  se={fmt(se_d)}")
    if n > 1 and sd_d > 0:
        print(f"    参考 (paired): mean/se = {mean_d / se_d:+.2f} (|値|>2 で paired 基準でも有意の目安)")

    # ── 4. 前半/後半の交絡チェック ──
    h = n // 2
    d1, d2 = diffs[:h], diffs[h:]
    m1 = statistics.mean(d1) if d1 else None
    m2 = statistics.mean(d2) if d2 else None
    f1 = 2 * SIGMA / math.sqrt(len(d1)) if d1 else None
    f2 = 2 * SIGMA / math.sqrt(len(d2)) if d2 else None
    on1 = statistics.mean(p["on"] for p in pairs[:h]) if h else None
    on2 = statistics.mean(p["on"] for p in pairs[h:]) if h else None
    off1 = statistics.mean(p["off"] for p in pairs[:h]) if h else None
    off2 = statistics.mean(p["off"] for p in pairs[h:]) if h else None
    print(f"\n[4] 前半/後半 (実行順) の交絡チェック")
    print(f"    前半 n={len(d1)}: ON {fmt(on1)} / OFF {fmt(off1)} / 差 {fmt(m1)} (下限 ±{fmt(f1)})")
    print(f"    後半 n={len(d2)}: ON {fmt(on2)} / OFF {fmt(off2)} / 差 {fmt(m2)} (下限 ±{fmt(f2)})")
    sign_mismatch = (m1 is not None and m2 is not None and len(d1) > 1 and len(d2) > 1
                     and (m1 > 0) != (m2 > 0))
    confounded = bool(sign_mismatch and abs(m1) > f1 and abs(m2) > f2)
    weak_signal = bool(sign_mismatch and not confounded and (abs(m1) > f1 or abs(m2) > f2))
    if confounded:
        print("    → 前半と後半で有意かつ逆符号 = 時間交絡の疑い。判定不能扱い")
    elif weak_signal:
        print("    → 注意: 符号不一致 (片側のみ有意)。FINDINGS §5.5 型の時間不安定の弱いシグナル。"
              "per-arm 半区間と llm_error 件数を確認のうえ判定を読むこと")
    else:
        print("    → 交絡シグナルなし (符号一致 or 半区間差はノイズ内)")

    # ── 5. 副次指標 ──
    def arm_stats(mp):
        ms = [mp[t] for t in themes_in_order if t in mp]
        if not ms:
            return None
        wall = [m["_wall_s"] for m in ms if m.get("_wall_s")]
        return {
            "n": len(ms),
            "first_pass": sum(_first_pass(m) for m in ms) / len(ms),
            "overall_pass": sum(_overall_pass(m) for m in ms) / len(ms),
            "avg_attempts": statistics.mean(len(m.get("validations") or []) for m in ms),
            "plateau": sum(bool(m.get("plateau_reached")) for m in ms) / len(ms),
            "wall_s": statistics.mean(wall) if wall else None,
        }
    a_on, a_off = arm_stats(on_map), arm_stats(off_map)
    if a_on and a_off:
        p_pool = (a_on["first_pass"] * a_on["n"] + a_off["first_pass"] * a_off["n"]) / (a_on["n"] + a_off["n"])
        se_p = math.sqrt(max(p_pool * (1 - p_pool), 1e-9) * (1 / a_on["n"] + 1 / a_off["n"]))
        print(f"\n[5] 副次指標 (方向の参考。単独で採否を決めない)")
        print(f"    {'指標':<18} {'ON':>8} {'OFF':>8}")
        print(f"    {'初回合格率':<17} {a_on['first_pass'] * 100:7.1f}% {a_off['first_pass'] * 100:7.1f}%"
              f"   (差のノイズ目安 ±{2 * se_p * 100:.0f}pp)")
        print(f"    {'総合合格率':<17} {a_on['overall_pass'] * 100:7.1f}% {a_off['overall_pass'] * 100:7.1f}%")
        print(f"    {'平均 attempt':<16} {a_on['avg_attempts']:8.2f} {a_off['avg_attempts']:8.2f}")
        print(f"    {'plateau 率':<16} {a_on['plateau'] * 100:7.1f}% {a_off['plateau'] * 100:7.1f}%")
        if a_on["wall_s"] and a_off["wall_s"]:
            print(f"    {'平均生成時間':<16} {a_on['wall_s']:7.0f}s {a_off['wall_s']:7.0f}s"
                  f"   (差 = self-critique のコスト)")

    # ── 6. 判定 ──
    print("\n" + "=" * 74)
    print("[判定] (事前登録基準の機械適用)")
    if problems:
        verdict = "RUN 無効 — arm 整合性違反 (env 適用ミス)。数値を判定に使わず再実行する"
    elif confounded:
        verdict = "判定不能 — 前半/後半で有意な逆符号 (時間交絡)。再測定 (ペース/fresh reload を見直す)"
    elif abs(mean_d) > floor:
        direction = "ON 優位 → self-critique を維持" if mean_d > 0 else \
            "OFF 優位 → self-critique の既定 OFF 化 (config.py) を提案"
        verdict = f"有意差あり: {mean_d:+.2f} (下限 ±{floor:.2f} 超え) — {direction}"
        if n < 0.8 * n_total:
            verdict += " ※ただし完了ペア<80% のため参考値。再測定で確認を推奨"
    else:
        verdict = (f"判定不能 — 差 {mean_d:+.2f} はノイズ下限 ±{floor:.2f} 以内。"
                   f" self-critique の score への効果は ±{floor:.1f} 点より小さい (これ自体が知見)。"
                   f" コスト (生成時間差・+1 LLM call) と副次指標を添えて採否はユーザー判断へ")
    print(f"  {verdict}")
    print("=" * 74)

    # ── 7. eval.sh 互換の結果 JSON (compare.sh 用) ──
    if save:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        for arm, sid in (("on", sid_on), ("off", sid_off)):
            m = api_retry(f"/api/metrics?session_id={sid}")
            m["_variant"] = VARIANT_NAMES[arm]
            m["_session_id"] = sid
            m["_timestamp"] = ts
            m["_themes_file"] = st.get("themes_file")
            m["_note"] = "paired run: 集計は全生成 (未成ペア含む)。判定は analyze のペア解析が正"
            out = os.path.join(HERE, "results", f"{ts}-{VARIANT_NAMES[arm]}.json")
            with open(out, "w") as f:
                json.dump(m, f, ensure_ascii=False, indent=2)
            print(f"saved: {out}")
        print(f"compare.sh でも見られます: ./compare.sh {VARIANT_NAMES['on']} {VARIANT_NAMES['off']}")


# ────────────────────────── entry ──────────────────────────

def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "run":
        cmd_run()
    elif cmd == "status":
        cmd_status()
    elif cmd == "analyze":
        state_path = STATE_FILE
        save = True
        rest = args[1:]
        while rest:
            tok = rest.pop(0)
            if tok == "--state":
                state_path = rest.pop(0)
            elif tok == "--no-save":
                save = False
            else:
                raise SystemExit(f"unknown option: {tok}")
        cmd_analyze(state_path, save)
    else:
        print(__doc__)
        raise SystemExit("usage: eval_paired_sc.py {run|status|analyze [--state FILE] [--no-save]}")


if __name__ == "__main__":
    main()
