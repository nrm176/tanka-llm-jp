#!/usr/bin/env bash
# run_ab.sh — self-critique アブレーションの paired-interleaved A/B ランナー (夜間無人実行用)
#
# 設計 (app/backend/eval/FINDINGS.md §5.5 の教訓を実装):
#   blocked A/B (16 テーマを ON で全部 → OFF で全部) は LM Studio の持続負荷劣化と交絡して
#   結論不能だった。本スクリプトは各 (ラウンド, テーマ) ごとに sc-ON / sc-OFF を「隣接ペア」で
#   実行し、ペア内の実行順をペアごとに反転する (ON→OFF, OFF→ON, ...)。
#   両アームがほぼ同じ LM Studio 状態を経験するため、環境ドリフトはペア差分から相殺される。
#
# アーム切替は `TANKA_SELF_CRITIQUE=<0|1> docker compose up -d backend` による backend
# コンテナ再作成で行う (env 変更は restart では効かない)。切替は必ず「タスクが走っていない」
# 区間で行うので実行中タスクを殺すことはない。終了時 (異常終了含む) は SELF_CRITIQUE=1
# (本番既定) に復元する。
#
# 使い方:
#   WORK=<作業dir> bash run_ab.sh                 # 本番 (RUNBOOK.md 参照)
#   DRY_RUN=1 WORK=/tmp/scab-dry bash run_ab.sh   # 状態変更ゼロの制御フロー確認
#
# 主な環境変数 (すべて上書き可):
#   WORK              作業ディレクトリ (必須)。manifest / ログ / スナップショットを書く
#   THEMES_FILE       テーマ JSON (既定: スクリプトと同じ場所の themes_ab16.json)
#   ROUNDS            テーマセットを何周するか (既定 2 → 16×2=32 ペア)
#   SLEEP_BETWEEN     生成と生成の間の休止秒 (既定 20。LM Studio の持続負荷を緩める)
#   TASK_TIMEOUT_SEC  1 生成あたりの上限秒 (既定 1800)。超えたら cancel して欠測扱い
#   DEADLINE_HOURS    全体上限 (既定 8)。超えたら新規ペアを開始せず後始末へ
#   CLEAR_FAILURES    1=開始時に長期失敗記憶をバックアップ後クリア (既定 1)
#   BACKEND           既定 http://localhost:8001
#   APP_DIR           docker compose の起点 (既定 /Users/nrm176p/GitHub2/LLM/app)
#   RESULTS_DIR       メトリクス JSON の保存先 (既定 $APP_DIR/backend/eval/results)
#   EXPECTED_MODEL    既定 llm-jp-4-8b-thinking
set -uo pipefail

# ───────────────────────── 設定 ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="${BACKEND:-http://localhost:8001}"
APP_DIR="${APP_DIR:-/Users/nrm176p/GitHub2/LLM/app}"
RESULTS_DIR="${RESULTS_DIR:-$APP_DIR/backend/eval/results}"
WORK="${WORK:?WORK=<作業ディレクトリ> を指定してください (例: WORK=~/tanka-eval-night/run1)}"
THEMES_FILE="${THEMES_FILE:-$SCRIPT_DIR/themes_ab16.json}"
ROUNDS="${ROUNDS:-2}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-20}"
TASK_TIMEOUT_SEC="${TASK_TIMEOUT_SEC:-1800}"
DEADLINE_HOURS="${DEADLINE_HOURS:-8}"
EXPECTED_MODEL="${EXPECTED_MODEL:-llm-jp-4-8b-thinking}"
CLEAR_FAILURES="${CLEAR_FAILURES:-1}"
DRY_RUN="${DRY_RUN:-0}"

TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$WORK"
MANIFEST="$WORK/manifest.jsonl"
RUN_DEADLINE=$(( $(date +%s) + DEADLINE_HOURS * 3600 ))

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
fatal() { log "FATAL: $*"; exit 1; }

# ───────────────────────── API ヘルパー (python3/urllib; curl の quoting 問題を回避) ─────────────────────────
api() { # api <GET|POST|DELETE> <path> [json-body] -> stdout に応答 JSON ("" on error)
  python3 - "$BACKEND" "$@" <<'PY'
import json, sys, urllib.request
backend, method, path = sys.argv[1], sys.argv[2], sys.argv[3]
body = sys.argv[4].encode() if len(sys.argv) > 4 else None
req = urllib.request.Request(backend + path, data=body, method=method,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        sys.stdout.write(r.read().decode())
except Exception:
    sys.stdout.write("")
PY
}

jfield() { # jfield <json> <field> -> 値 (なければ "")
  python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1]); v = d.get(sys.argv[2])
    print("" if v is None else v)
except Exception:
    print("")
' "$1" "$2"
}

# ───────────────────────── backend のアーム切替 ─────────────────────────
CURRENT_SC="unknown"
FLIP_COUNT=0
flip_backend() { # flip_backend <0|1>
  local want="$1"
  [[ "$CURRENT_SC" == "$want" ]] && return 0
  if [[ "$DRY_RUN" == "1" ]]; then
    log "[dry] flip backend: TANKA_SELF_CRITIQUE=$want"
    CURRENT_SC="$want"; return 0
  fi
  log "flip backend: TANKA_SELF_CRITIQUE=$want (docker compose up -d backend)"
  # SELF_CRITIQUE 以外の TANKA_* は本番既定に明示固定 (シェルに紛れた export の混入防止。
  # 意図的に変えたい場合は起動シェルで export してあれば ${VAR:-default} で尊重される)
  ( cd "$APP_DIR" && \
    TANKA_SELF_CRITIQUE="$want" \
    TANKA_DYNAMIC_FEWSHOT="${TANKA_DYNAMIC_FEWSHOT:-1}" \
    TANKA_RAG="${TANKA_RAG:-1}" \
    TANKA_HARD_CAP="${TANKA_HARD_CAP:-50}" \
    TANKA_PLATEAU_WINDOW="${TANKA_PLATEAU_WINDOW:-3}" \
    docker compose up -d backend ) >>"$WORK/compose.log" 2>&1 \
    || { log "ERROR: docker compose up failed (see $WORK/compose.log)"; return 1; }
  wait_health 180 || return 1
  CURRENT_SC="$want"
  FLIP_COUNT=$((FLIP_COUNT + 1))
  return 0
}

wait_health() { # wait_health <timeout-sec>
  local deadline=$(( $(date +%s) + $1 ))
  while (( $(date +%s) < deadline )); do
    local h; h="$(api GET /api/health)"
    if [[ -n "$h" ]]; then
      local st ml cm
      st="$(jfield "$h" status)"; ml="$(jfield "$h" model_loaded)"; cm="$(jfield "$h" configured_model)"
      if [[ "$st" == "ok" && "$ml" == "True" && "$cm" == "$EXPECTED_MODEL" ]]; then
        return 0
      fi
    fi
    sleep 3
  done
  log "ERROR: backend が ${1}s 以内に healthy になりません"
  return 1
}

# ───────────────────────── manifest 出力 ─────────────────────────
emit_json() { # emit_json key=value... (値はそのまま文字列。__int_ プレフィックスで int 化)
  python3 -c '
import json, sys
rec = {}
for kv in sys.argv[1:]:
    k, _, v = kv.partition("=")
    if k.startswith("__int_"):
        k = k[6:]
        try: v = int(v)
        except ValueError: pass
    rec[k] = v
print(json.dumps(rec, ensure_ascii=False))
' "$@" >> "$MANIFEST"
}

# ───────────────────────── 後始末 (正常/異常共通) ─────────────────────────
SID_ON=""; SID_OFF=""
FINALIZED=0
PAIRS_DONE=0
collect_metrics() { # collect_metrics <sid> <variant>
  local sid="$1" variant="$2"
  [[ -z "$sid" ]] && return 0
  local m; m="$(api GET "/api/metrics?session_id=$sid")"
  [[ -z "$m" ]] && { log "WARN: metrics 取得失敗 ($variant)"; return 0; }
  local outdir="$RESULTS_DIR"
  [[ "$DRY_RUN" == "1" ]] && outdir="$WORK"   # dry-run ではリポジトリ内に書かない
  local out="$outdir/${TS}-${variant}.json"
  python3 -c '
import json, sys
m = json.loads(sys.argv[1])
m["_variant"] = sys.argv[2]; m["_session_id"] = sys.argv[3]
m["_timestamp"] = sys.argv[4]; m["_themes_file"] = sys.argv[5]
m["_manifest"] = sys.argv[6]; m["_design"] = "paired-interleaved"
with open(sys.argv[7], "w") as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
' "$m" "$variant" "$sid" "$TS" "$THEMES_FILE" "$MANIFEST" "$out" \
    && log "metrics saved: $out"
}

finalize() { # finalize <status>
  local status="$1"
  (( FINALIZED )) && return 0
  FINALIZED=1
  log "finalize (status=$status): backend を本番既定 (SELF_CRITIQUE=1) に復元します"
  flip_backend 1 || log "WARN: 復元 flip 失敗 — 手動で復元してください: (cd $APP_DIR && TANKA_SELF_CRITIQUE=1 docker compose up -d backend)"
  collect_metrics "$SID_ON" "sc-on-paired"
  collect_metrics "$SID_OFF" "sc-off-paired"
  if [[ "$DRY_RUN" != "1" ]]; then
    docker inspect app-backend-1 --format '{{json .Config.Env}}' >"$WORK/backend_env_after.json" 2>/dev/null || true
    api GET /api/health >"$WORK/health_after.json" || true
  fi
  emit_json type=run_end status="$status" ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    __int_pairs_done="$PAIRS_DONE" __int_flip_count="$FLIP_COUNT"
  log "done. pairs_done=$PAIRS_DONE flips=$FLIP_COUNT"
  log "次: python3 $SCRIPT_DIR/analyze_ab.py --work $WORK"
}

on_exit() {
  local rc=$?
  trap - EXIT
  if (( ! FINALIZED )); then
    log "abnormal exit (rc=$rc) — 後始末を実行します"
    finalize "aborted-rc$rc" || true
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT TERM

# ───────────────────────── preflight ─────────────────────────
log "=== self-critique paired A/B runner (TS=$TS, DRY_RUN=$DRY_RUN) ==="
log "WORK=$WORK ROUNDS=$ROUNDS SLEEP_BETWEEN=${SLEEP_BETWEEN}s TASK_TIMEOUT=${TASK_TIMEOUT_SEC}s DEADLINE=${DEADLINE_HOURS}h"

for c in python3 curl docker; do
  command -v "$c" >/dev/null || fatal "コマンドが見つかりません: $c"
done
[[ -f "$THEMES_FILE" ]] || fatal "テーマファイルがありません: $THEMES_FILE"
[[ -d "$APP_DIR" ]] || fatal "APP_DIR がありません: $APP_DIR"

# テーマ読み込み (id<TAB>theme)
THEME_LINES=()
while IFS= read -r line; do THEME_LINES+=("$line"); done < <(python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
for t in data["themes"]:
    print(t["id"] + "\t" + t["theme"])
' "$THEMES_FILE")
N=${#THEME_LINES[@]}
(( N > 0 )) || fatal "テーマが 0 件です"
log "themes: $N 件 × $ROUNDS ラウンド = $(( N * ROUNDS )) ペア ($(( N * ROUNDS * 2 )) 生成)"

# backend 健康チェック (モデル一致まで確認)
H="$(api GET /api/health)"
[[ -n "$H" ]] || fatal "backend に到達できません: $BACKEND"
[[ "$(jfield "$H" status)" == "ok" ]] || fatal "backend status != ok: $(jfield "$H" status) (LM Studio / mongo / redis を確認)"
[[ "$(jfield "$H" configured_model)" == "$EXPECTED_MODEL" ]] || fatal "configured_model が $EXPECTED_MODEL ではありません: $(jfield "$H" configured_model) (UI で切替えたまま?)"
[[ "$(jfield "$H" model_loaded)" == "True" ]] || fatal "モデルが LM Studio にロードされていません"
printf '%s' "$H" >"$WORK/health_before.json"

# 他のタスクが走っていないこと
ACTIVE_COUNT="$(api GET /api/sessions | python3 -c '
import json, sys
try:
    ss = json.load(sys.stdin)
    print(sum(1 for s in ss if s.get("active_task")))
except Exception:
    print(-1)
')"
[[ "$ACTIVE_COUNT" == "0" ]] || fatal "実行中タスクのあるセッションが $ACTIVE_COUNT 件あります。全て完了/cancel してから実行してください"

# スナップショット (provenance)
if [[ "$DRY_RUN" != "1" ]]; then
  docker inspect app-backend-1 --format '{{json .Config.Env}}' >"$WORK/backend_env_before.json" 2>/dev/null || true
  lms ps >"$WORK/lms_ps_before.txt" 2>/dev/null || true
fi
GIT_REV="$(cd "$APP_DIR/.." && git rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_DIRTY="$(cd "$APP_DIR/.." && git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
[[ "$GIT_DIRTY" != "0" ]] && log "WARN: working tree に未コミット変更が $GIT_DIRTY 件あります (結果の解釈時に注意)"

# 長期失敗記憶: バックアップ → クリア (両アームを同一の初期状態から始めるため)
if [[ "$CLEAR_FAILURES" == "1" && "$DRY_RUN" != "1" ]]; then
  api GET "/api/failures?limit=500" >"$WORK/failures_backup.json" || true
  CLEARED="$(jfield "$(api DELETE /api/failures)" cleared)"
  log "長期失敗記憶をクリアしました (cleared=$CLEARED, バックアップ: $WORK/failures_backup.json)"
fi

# セッション作成 (モデルをセッションに固定 #20 — 実行中の UI 切替から保護)
if [[ "$DRY_RUN" == "1" ]]; then
  SID_ON="dry-on"; SID_OFF="dry-off"
else
  SID_ON="$(jfield "$(api POST /api/sessions "{\"title\": \"[eval] sc-on-paired $TS\", \"model\": \"$EXPECTED_MODEL\"}")" id)"
  SID_OFF="$(jfield "$(api POST /api/sessions "{\"title\": \"[eval] sc-off-paired $TS\", \"model\": \"$EXPECTED_MODEL\"}")" id)"
  [[ -n "$SID_ON" && -n "$SID_OFF" ]] || fatal "セッション作成に失敗しました"
fi
log "sessions: on=$SID_ON off=$SID_OFF"

emit_json type=run_meta ts="$TS" sid_on="$SID_ON" sid_off="$SID_OFF" \
  themes_file="$THEMES_FILE" __int_n_themes="$N" __int_rounds="$ROUNDS" \
  __int_sleep_between="$SLEEP_BETWEEN" __int_task_timeout_sec="$TASK_TIMEOUT_SEC" \
  expected_model="$EXPECTED_MODEL" git_rev="$GIT_REV" git_dirty="$GIT_DIRTY" \
  backend="$BACKEND" clear_failures="$CLEAR_FAILURES" dry_run="$DRY_RUN" \
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ───────────────────────── 1 セルの実行 ─────────────────────────
CONSEC_SPAWN_FAIL=0
run_cell() { # run_cell <pair_idx> <round> <theme_id> <theme> <on|off>
  local pair_idx="$1" round="$2" tid_label="$3" theme="$4" arm="$5"
  local sc sid
  if [[ "$arm" == "on" ]]; then sc=1; sid="$SID_ON"; else sc=0; sid="$SID_OFF"; fi

  flip_backend "$sc" || fatal "アーム切替に失敗 (pair=$pair_idx arm=$arm)"

  local started ended status="ok" task_id="" poll_errors=0
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local t0; t0=$(date +%s)

  if [[ "$DRY_RUN" == "1" ]]; then
    task_id="dry-task-$pair_idx-$arm"
    log "[dry] [pair $pair_idx r$round] $tid_label $arm: スキップ (生成しない)"
  else
    local body resp
    body="$(python3 -c 'import json,sys; print(json.dumps({"session_id": sys.argv[1], "theme": sys.argv[2]}))' "$sid" "$theme")"
    resp="$(api POST /api/tanka "$body")"
    task_id="$(jfield "$resp" task_id)"
    if [[ -z "$task_id" ]]; then
      status="spawn_failed"
      CONSEC_SPAWN_FAIL=$((CONSEC_SPAWN_FAIL + 1))
      (( CONSEC_SPAWN_FAIL >= 3 )) && fatal "タスク作成が 3 回連続で失敗 — backend 異常の可能性"
    else
      CONSEC_SPAWN_FAIL=0
      # 完了待ち (eval.sh と同じ active-task ポーリング + watchdog)
      while :; do
        local el=$(( $(date +%s) - t0 ))
        if (( el > TASK_TIMEOUT_SEC )); then
          log "TIMEOUT: pair=$pair_idx $arm ($theme) ${el}s — cancel します"
          api POST "/api/tasks/$task_id/cancel" >/dev/null || true
          local cd=$(( $(date +%s) + 60 ))
          while (( $(date +%s) < cd )); do
            [[ -z "$(jfield "$(api GET "/api/sessions/$sid/active-task")" task_id)" ]] && break
            sleep 3
          done
          [[ -n "$(jfield "$(api GET "/api/sessions/$sid/active-task")" task_id)" ]] \
            && fatal "cancel 後もタスクが終了しません — セッションが固着 (要手動確認)"
          status="timeout"
          break
        fi
        local at; at="$(api GET "/api/sessions/$sid/active-task")"
        if [[ -z "$at" ]]; then
          poll_errors=$((poll_errors + 1))
          (( poll_errors > 60 )) && fatal "active-task ポーリングが 60 回連続失敗 — backend 異常"
        elif [[ -z "$(jfield "$at" task_id)" ]]; then
          break  # 完了
        fi
        sleep 3
      done
    fi
  fi

  ended="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local dur=$(( $(date +%s) - t0 ))
  emit_json type=cell __int_pair_idx="$pair_idx" __int_round="$round" \
    theme_id="$tid_label" theme="$theme" arm="$arm" sc_env="$sc" session_id="$sid" \
    task_id="$task_id" started_at="$started" ended_at="$ended" \
    __int_duration_sec="$dur" status="$status" __int_poll_errors="$poll_errors"
  log "[pair $pair_idx r$round] $tid_label $arm: $status (${dur}s)"
  [[ "$DRY_RUN" != "1" ]] && sleep "$SLEEP_BETWEEN"
  return 0
}

# ───────────────────────── メインループ ─────────────────────────
pair_idx=0
deadline_hit=0
for round in $(seq 1 "$ROUNDS"); do
  for line in "${THEME_LINES[@]}"; do
    if (( $(date +%s) > RUN_DEADLINE )); then
      log "DEADLINE (${DEADLINE_HOURS}h) 到達 — 新規ペアを開始しません"
      deadline_hit=1
      break 2
    fi
    tid_label="${line%%$'\t'*}"
    theme="${line#*$'\t'}"
    # ペアごとに実行順を反転: 偶数ペア ON→OFF / 奇数ペア OFF→ON (順序効果の相殺)
    if (( pair_idx % 2 == 0 )); then order=(on off); else order=(off on); fi
    for arm in "${order[@]}"; do
      run_cell "$pair_idx" "$round" "$tid_label" "$theme" "$arm"
    done
    pair_idx=$((pair_idx + 1))
    PAIRS_DONE=$pair_idx
  done
done

if (( deadline_hit )); then finalize "deadline"; else finalize "completed"; fi
