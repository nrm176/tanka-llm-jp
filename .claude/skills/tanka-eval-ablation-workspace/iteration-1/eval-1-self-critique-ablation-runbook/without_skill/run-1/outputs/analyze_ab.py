#!/usr/bin/env python3
"""analyze_ab.py — self-critique paired A/B の解析と機械判定 (読み取り専用; GET のみ)

使い方:
  python3 analyze_ab.py --work <WORKdir>             # run_ab.sh の manifest から解析 (推奨)
  python3 analyze_ab.py --on <sid> --off <sid>       # セッション id 直接指定 (過去 run の再解析)

オプション:
  --backend URL          (既定 http://localhost:8001)
  --expected-model NAME  (既定 llm-jp-4-8b-thinking)
  --json PATH            機械可読サマリを JSON で保存

判定基準 (事前登録; RUNBOOK.md §6 と同一):
  ゲート G1 用量   : OFF アームに self_critique phase が 1 件でもあれば FAIL。
                     ON アームの self_critique 実行率 < 90% なら FAIL (underdose)。
  ゲート G2 モデル : 全生成の model が expected と一致しなければ FAIL。
  ゲート G3 充足率 : 成立ペア数 / 計画ペア数 < 87.5% なら FAIL。
  ゲート G4 環境   : configured_model が実験前後で expected のままであること (warn 扱いあり)。
  一次判定        : D = mean(score_ON - score_OFF)。|D| > 2SE なら有意。
                    D > +2SE → sc 維持 / D < -2SE → sc OFF 化。
  コスト tiebreak : |D| <= 2SE のとき、平均 LLM call 数 (len(phases)) で判断。
                    calls_ON <= calls_OFF → 現状維持 (ON) / calls_ON > calls_OFF → OFF 化推奨。
  ドリフトガード   : ペア列の前半/後半で D の符号が逆転し、かつ両半が |D_half| > 2SE_half
                    なら「環境交絡の疑い → 判定は暫定、再測定」。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import urllib.request
from pathlib import Path


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def mean_sd_se(xs: list[float]) -> tuple[float, float, float]:
    m = statistics.mean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    se = sd / math.sqrt(len(xs)) if xs else 0.0
    return m, sd, se


def binom_two_sided(k: int, n: int) -> float:
    """両側二項検定 (p=0.5)。sign test / McNemar 用。"""
    if n == 0:
        return 1.0
    lo = min(k, n - k)
    p = sum(math.comb(n, i) for i in range(0, lo + 1)) / (2 ** n) * 2
    return min(1.0, p)


def load_manifest(work: Path) -> tuple[dict, list[dict]]:
    meta, cells = {}, []
    with open(work / "manifest.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "run_meta":
                meta = rec
            elif rec.get("type") == "cell":
                cells.append(rec)
    return meta, cells


def tanka_messages(session: dict) -> list[dict]:
    return [m for m in session.get("messages", []) if m.get("kind") == "tanka"]


def sc_phase_count(msg: dict) -> int:
    return sum(1 for p in (msg.get("phases") or []) if p.get("phase") == "self_critique")


def bind_cells_to_messages(cells: list[dict], msgs: list[dict]) -> tuple[dict, set]:
    """status=ok のセル (実行順) とセッションの tanka メッセージ (追記順) をテーマ単位で照合。
    テーマごとに件数が一致した場合のみ in-order で対応付ける (round 帰属が正確になる)。
    件数不一致のテーマは保守的に全ペア除外 (integrity_dropped)。
    返り値: {(round, theme): message}, dropped_themes"""
    ok_cells = [c for c in cells if c.get("status") == "ok"]
    cells_by_theme: dict[str, list[dict]] = {}
    for c in ok_cells:
        cells_by_theme.setdefault(c["theme"], []).append(c)
    msgs_by_theme: dict[str, list[dict]] = {}
    for m in msgs:
        msgs_by_theme.setdefault(m.get("theme", ""), []).append(m)

    bound: dict = {}
    dropped: set = set()
    for theme, tcells in cells_by_theme.items():
        tmsgs = msgs_by_theme.get(theme, [])
        if len(tcells) != len(tmsgs):
            dropped.add(theme)
            continue
        for c, m in zip(tcells, tmsgs):
            bound[(c["round"], theme)] = {"msg": m, "cell": c}
    return bound, dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work")
    ap.add_argument("--on")
    ap.add_argument("--off")
    ap.add_argument("--backend", default="http://localhost:8001")
    ap.add_argument("--expected-model", default="llm-jp-4-8b-thinking")
    ap.add_argument("--json")
    args = ap.parse_args()

    meta: dict = {}
    cells: list[dict] = []
    if args.work:
        work = Path(args.work).expanduser()
        meta, cells = load_manifest(work)
        sid_on, sid_off = meta.get("sid_on"), meta.get("sid_off")
        if not sid_on or not sid_off:
            print("ERROR: manifest に sid_on / sid_off がありません", file=sys.stderr)
            return 1
    elif args.on and args.off:
        sid_on, sid_off = args.on, args.off
    else:
        ap.error("--work か --on/--off を指定してください")
        return 1

    try:
        s_on = get_json(f"{args.backend}/api/sessions/{sid_on}")
        s_off = get_json(f"{args.backend}/api/sessions/{sid_off}")
    except Exception as e:
        print(f"ERROR: セッション取得に失敗: {e}", file=sys.stderr)
        return 1

    m_on, m_off = tanka_messages(s_on), tanka_messages(s_off)
    print("=" * 78)
    print("self-critique paired A/B 解析")
    print("=" * 78)
    print(f"  ON  session: {sid_on}  ({s_on.get('title')!r})  tanka={len(m_on)}")
    print(f"  OFF session: {sid_off}  ({s_off.get('title')!r})  tanka={len(m_off)}")

    # ── ペア構築 ──
    dropped_themes: set = set()
    pairs: list[dict] = []  # {round, theme, on, off, t}
    if cells:
        on_cells = [c for c in cells if c.get("arm") == "on"]
        off_cells = [c for c in cells if c.get("arm") == "off"]
        b_on, d1 = bind_cells_to_messages(on_cells, m_on)
        b_off, d2 = bind_cells_to_messages(off_cells, m_off)
        dropped_themes = d1 | d2
        for key in sorted(set(b_on) & set(b_off), key=lambda k: (k[0], k[1])):
            if key[1] in dropped_themes:
                continue
            on, off = b_on[key], b_off[key]
            pairs.append({
                "round": key[0], "theme": key[1],
                "on": on["msg"], "off": off["msg"],
                "on_cell": on["cell"], "off_cell": off["cell"],
                "t": min(on["cell"].get("started_at") or "", off["cell"].get("started_at") or ""),
            })
        planned = (meta.get("n_themes") or 0) * (meta.get("rounds") or 0)
    else:
        # legacy モード: manifest 無し → テーマの出現順で対応付け (round 帰属は近似)
        print("  (manifest 無し: テーマ出現順でペアリングします — round 帰属は近似)")
        occ_on: dict[str, list[dict]] = {}
        for m in m_on:
            occ_on.setdefault(m.get("theme", ""), []).append(m)
        occ_off: dict[str, list[dict]] = {}
        for m in m_off:
            occ_off.setdefault(m.get("theme", ""), []).append(m)
        for theme in occ_on:
            for i, (a, b) in enumerate(zip(occ_on[theme], occ_off.get(theme, []))):
                pairs.append({"round": i + 1, "theme": theme, "on": a, "off": b,
                              "on_cell": None, "off_cell": None,
                              "t": min(a.get("created_at") or "", b.get("created_at") or "")})
        planned = max(len(m_on), len(m_off))
    pairs.sort(key=lambda p: p["t"])  # 時系列順 (前半/後半分割に使う)

    # スコアが両方 int のペアのみ統計対象
    scored = [p for p in pairs
              if isinstance(p["on"].get("final_score"), int)
              and isinstance(p["off"].get("final_score"), int)]
    n = len(scored)

    # ── ゲート ──
    print("\n[ 整合性ゲート ]")
    gates: dict[str, bool] = {}

    # G1: 用量 (self_critique phase の有無で env 切替の実効を per-generation 検証)
    has_phases = any((m.get("phases") for m in m_on + m_off))
    if not has_phases:
        gates["G1"] = False
        print("  G1 用量      : FAIL — phases が未記録 (phase 永続化以前の旧データ?)。"
              "self-critique の実行有無を検証できません")
    else:
        on_dose = [sc_phase_count(m) for m in m_on]
        off_dose = [sc_phase_count(m) for m in m_off]
        on_rate = (sum(1 for d in on_dose if d >= 1) / len(on_dose)) if on_dose else 0.0
        off_contam = sum(1 for d in off_dose if d > 0)
        gates["G1"] = (off_contam == 0) and (on_rate >= 0.9)
        print(f"  G1 用量      : {'PASS' if gates['G1'] else 'FAIL'} — "
              f"ON アーム sc 実行率 {on_rate:.0%} (>=90% 必要) / "
              f"OFF アーム混入 {off_contam} 件 (0 必要)")

    # G2: モデル
    models = {m.get("model") for m in m_on + m_off}
    gates["G2"] = models == {args.expected_model}
    print(f"  G2 モデル    : {'PASS' if gates['G2'] else 'FAIL'} — 観測モデル: {sorted(str(x) for x in models)}"
          + ("" if gates["G2"] else f" (期待: {args.expected_model})"))

    # G3: 充足率
    if planned:
        rate = n / planned
        gates["G3"] = rate >= 0.875
        print(f"  G3 充足率    : {'PASS' if gates['G3'] else 'FAIL'} — 成立ペア {n}/{planned} ({rate:.0%}, >=87.5% 必要)"
              + (f" / integrity_drop テーマ: {sorted(dropped_themes)}" if dropped_themes else ""))
    else:
        gates["G3"] = n >= 14
        print(f"  G3 充足率    : {'PASS' if gates['G3'] else 'FAIL'} — 成立ペア {n} (計画数不明; >=14 を要求)")

    # G4: 環境 (configured_model の前後一致; --work のみ)
    g4_note = "SKIP (work 指定なし)"
    gates["G4"] = True
    if args.work:
        try:
            hb = json.loads((Path(args.work).expanduser() / "health_before.json").read_text())
            ha = json.loads((Path(args.work).expanduser() / "health_after.json").read_text())
            ok = hb.get("configured_model") == args.expected_model == ha.get("configured_model")
            gates["G4"] = ok
            g4_note = (f"{'PASS' if ok else 'FAIL'} — configured_model 前={hb.get('configured_model')} "
                       f"後={ha.get('configured_model')}")
        except Exception as e:
            g4_note = f"SKIP (スナップショット読めず: {e})"
    print(f"  G4 環境      : {g4_note}")

    # ── ペア表 ──
    print("\n[ ペア別スコア (時系列順) ]")
    print(f"  {'r':>2} {'theme':<24} {'ON':>4} {'OFF':>4} {'diff':>5}")
    for p in scored:
        d = p["on"]["final_score"] - p["off"]["final_score"]
        print(f"  {p['round']:>2} {p['theme']:<24} {p['on']['final_score']:>4} "
              f"{p['off']['final_score']:>4} {d:>+5}")

    if n < 2:
        print("\nERROR: 統計に足るペアがありません (n<2)")
        return 1

    # ── 一次統計 ──
    diffs = [p["on"]["final_score"] - p["off"]["final_score"] for p in scored]
    D, sd, se = mean_sd_se([float(d) for d in diffs])
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    ties = len(diffs) - pos - neg
    p_sign = binom_two_sided(pos, pos + neg)

    half = n // 2
    d1h = [float(x) for x in diffs[:half]]
    d2h = [float(x) for x in diffs[half:]]
    m1, _, se1 = mean_sd_se(d1h)
    m2, _, se2 = mean_sd_se(d2h)
    drift_suspect = (m1 * m2 < 0) and (abs(m1) > 2 * se1 > 0) and (abs(m2) > 2 * se2 > 0)

    print("\n[ 一次判定: ペア差分 D = score_ON - score_OFF ]")
    print(f"  n ペア            : {n}")
    print(f"  D (平均差)        : {D:+.2f} 点")
    print(f"  sd / SE           : {sd:.2f} / {se:.2f}")
    print(f"  2SE (ノイズ下限)  : {2 * se:.2f}  → 有意条件 |D| > {2 * se:.2f}")
    print(f"  95%CI (≈D±2SE)    : [{D - 2 * se:+.2f}, {D + 2 * se:+.2f}]")
    print(f"  符号検定          : ON 勝ち {pos} / OFF 勝ち {neg} / 引分 {ties}  (両側 p={p_sign:.3f})")
    print(f"  前半/後半の D     : {m1:+.2f} / {m2:+.2f}"
          + ("  ← 符号逆転かつ両半有意: 環境ドリフト疑い" if drift_suspect else ""))

    # ── 副次指標 ──
    def arm_stats(arm: str) -> dict:
        cells_key = f"{arm}_cell"
        scores = [p[arm]["final_score"] for p in scored]
        msgs = [p[arm] for p in scored]
        first_pass = sum(1 for m in msgs
                         if (m.get("validations") or [{}])[0].get("resolved"))
        attempts = [len(m.get("validations") or []) for m in msgs]
        calls = [len(m.get("phases") or []) for m in msgs if m.get("phases")]
        zeros = sum(1 for s in scores if s == 0)
        durs = [p[cells_key].get("duration_sec") for p in scored
                if p.get(cells_key) and isinstance(p[cells_key].get("duration_sec"), int)]
        return {
            "mean_score": statistics.mean(scores),
            "median_score": statistics.median(scores),
            "first_pass_rate": first_pass / len(msgs),
            "mean_attempts": statistics.mean(attempts),
            "mean_llm_calls": statistics.mean(calls) if calls else None,
            "zero_scores": zeros,
            "mean_duration": statistics.mean(durs) if durs else None,
        }

    a_on = arm_stats("on")
    a_off = arm_stats("off")
    fp_on_only = sum(1 for p in scored
                     if (p["on"].get("validations") or [{}])[0].get("resolved")
                     and not (p["off"].get("validations") or [{}])[0].get("resolved"))
    fp_off_only = sum(1 for p in scored
                      if (p["off"].get("validations") or [{}])[0].get("resolved")
                      and not (p["on"].get("validations") or [{}])[0].get("resolved"))
    p_mcnemar = binom_two_sided(fp_on_only, fp_on_only + fp_off_only)

    def fmt(v, spec=".2f"):
        return ("n/a" if v is None else format(v, spec))

    print("\n[ 副次指標 (成立ペアのみで集計) ]")
    print(f"  {'指標':<26} {'sc-ON':>10} {'sc-OFF':>10}")
    print(f"  {'平均スコア':<26} {a_on['mean_score']:>10.2f} {a_off['mean_score']:>10.2f}")
    print(f"  {'中央値':<26} {a_on['median_score']:>10.1f} {a_off['median_score']:>10.1f}")
    print(f"  {'初回合格率':<26} {a_on['first_pass_rate']:>10.1%} {a_off['first_pass_rate']:>10.1%}")
    print(f"  {'平均 attempt 数':<26} {a_on['mean_attempts']:>10.2f} {a_off['mean_attempts']:>10.2f}")
    print(f"  {'平均 LLM call 数/首':<26} {fmt(a_on['mean_llm_calls']):>10} {fmt(a_off['mean_llm_calls']):>10}")
    print(f"  {'score=0 件数':<26} {a_on['zero_scores']:>10d} {a_off['zero_scores']:>10d}")
    print(f"  {'平均所要秒/首':<26} {fmt(a_on['mean_duration'], '.0f'):>10} {fmt(a_off['mean_duration'], '.0f'):>10}")
    print(f"  初回合格 McNemar  : ON のみ合格 {fp_on_only} / OFF のみ合格 {fp_off_only} (両側 p={p_mcnemar:.3f})")

    # ── 機械判定 ──
    print("\n[ 機械判定 (参考 — 最終判断は RUNBOOK.md §6 の判定基準で) ]")
    verdict: str
    if not (gates["G1"] and gates["G2"] and gates["G3"]):
        failed = [g for g in ("G1", "G2", "G3") if not gates[g]]
        verdict = f"判定不能 — ゲート不成立: {', '.join(failed)}。測定をやり直すこと"
    elif D > 2 * se:
        verdict = (f"sc は品質を上げている (D={D:+.2f} > 2SE={2 * se:.2f}) → self-critique を維持 (ON のまま)")
    elif D < -2 * se:
        verdict = (f"sc は品質を下げている (D={D:+.2f} < -2SE={-2 * se:.2f}) → 既定 OFF 化を推奨")
    else:
        c_on, c_off = a_on["mean_llm_calls"], a_off["mean_llm_calls"]
        if c_on is None or c_off is None:
            verdict = (f"品質差はノイズ下限内 (|D|={abs(D):.2f} <= 2SE={2 * se:.2f})。"
                       "コスト指標が取れないため判定保留")
        elif c_on <= c_off:
            verdict = (f"品質差はノイズ下限内 (|D|={abs(D):.2f} <= 2SE={2 * se:.2f}) かつ"
                       f"コスト中立以下 (calls {c_on:.2f} vs {c_off:.2f}) → 現状維持 (ON) が妥当。"
                       f"『効果があるとしても ±{2 * se:.1f} 点未満』と記録すること")
        else:
            verdict = (f"品質差はノイズ下限内 (|D|={abs(D):.2f} <= 2SE={2 * se:.2f}) だが"
                       f"コスト増 (calls {c_on:.2f} vs {c_off:.2f}, +{c_on - c_off:.2f}/首) → "
                       f"既定 OFF 化を推奨 (フラグは残し将来再測定可能に)")
    if drift_suspect:
        verdict += " 【暫定: 前半/後半で符号逆転 — 環境ドリフト疑い。fresh reload + SLEEP_BETWEEN 増で再測定を推奨】"
    if not gates["G4"]:
        verdict += " 【注意: G4 (configured_model) が不一致 — 実行中にモデルが切替った可能性。要確認】"
    print(f"  {verdict}")

    if args.json:
        out = {
            "n_pairs": n, "mean_diff": D, "sd": sd, "se": se, "two_se": 2 * se,
            "sign": {"pos": pos, "neg": neg, "ties": ties, "p": p_sign},
            "half_means": [m1, m2], "drift_suspect": drift_suspect,
            "gates": gates, "dropped_themes": sorted(dropped_themes),
            "arm_on": a_on, "arm_off": a_off,
            "mcnemar_first_pass": {"on_only": fp_on_only, "off_only": fp_off_only, "p": p_mcnemar},
            "verdict": verdict,
            "pairs": [{"round": p["round"], "theme": p["theme"],
                       "on": p["on"]["final_score"], "off": p["off"]["final_score"]}
                      for p in scored],
        }
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\nJSON サマリ: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
