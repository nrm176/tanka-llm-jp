#!/usr/bin/env bash
# Phase 2 評価ハーネス: 固定お題セットを 1 セッションで順次生成し、メトリクスを保存する。
#
# 使い方:
#   ./eval.sh <variant-name> [themes.json] [backend-url]
#
# 例:
#   ./eval.sh baseline
#   TANKA_SELF_CRITIQUE=0 docker compose up -d backend && ./eval.sh no-self-critique
#
# 注意:
#   - variant の設定 (env var) は backend コンテナ側に効かせる必要がある。
#     つまり「docker compose up -d で backend を変えた状態」を作ってから eval.sh を回す。
#     eval.sh 自身は backend の挙動を変えない (HTTP client にすぎない)。
#   - 1 セッション = 1 variant の集計単位。お題は順次実行 (同一セッションで並行不可)。

set -euo pipefail

VARIANT="${1:?Usage: ./eval.sh <variant-name> [themes.json] [backend-url]}"
THEMES_FILE="${2:-$(dirname "$0")/eval_themes.json}"
BACKEND="${3:-http://localhost:8001}"
RESULTS_DIR="$(dirname "$0")/results"
mkdir -p "$RESULTS_DIR"

TS=$(date +%Y%m%d-%H%M%S)
OUT="$RESULTS_DIR/${TS}-${VARIANT}.json"

echo "Variant:   $VARIANT"
echo "Themes:    $THEMES_FILE"
echo "Backend:   $BACKEND"
echo "Output:    $OUT"
echo ""

# backend 到達確認
if ! curl -sf "$BACKEND/api/health" >/dev/null; then
  echo "ERROR: backend not reachable at $BACKEND" >&2
  exit 1
fi

# お題リストを抽出 (python3 で JSON パース)
mapfile -t THEME_LINES < <(python3 -c "
import json, sys
with open('$THEMES_FILE') as f:
    data = json.load(f)
for t in data['themes']:
    print(t['id'] + '\t' + t['theme'])
")
N=${#THEME_LINES[@]}
echo "Loaded $N themes."
echo ""

# 専用セッション作成
SID=$(curl -s -X POST "$BACKEND/api/sessions" -H 'Content-Type: application/json' \
  -d "{\"title\": \"[eval] $VARIANT $TS\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Session: $SID"
echo ""

i=0
for line in "${THEME_LINES[@]}"; do
  i=$((i + 1))
  tid_label="${line%%$'\t'*}"
  theme="${line#*$'\t'}"
  printf "[%2d/%2d] %-8s %s ... " "$i" "$N" "$tid_label" "$(echo "$theme" | cut -c1-30)"

  # タスク作成 (max_refines は backend デフォルト = 無制限 plateau)
  TID=$(curl -s -X POST "$BACKEND/api/tanka" -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys; print(json.dumps({'session_id':'$SID','theme':sys.argv[1]}))" "$theme")" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))")

  if [ -z "$TID" ]; then
    echo "FAILED (no task_id)"
    continue
  fi

  # 完了待ち (active-task が null になるまで)
  while true; do
    ACTIVE=$(curl -s "$BACKEND/api/sessions/$SID/active-task" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id') or '')")
    [ -z "$ACTIVE" ] && break
    sleep 3
  done
  echo "done"
done

echo ""
echo "Collecting metrics for session $SID ..."
curl -s "$BACKEND/api/metrics?session_id=$SID" | python3 -c "
import sys, json
m = json.load(sys.stdin)
m['_variant'] = '$VARIANT'
m['_session_id'] = '$SID'
m['_timestamp'] = '$TS'
m['_themes_file'] = '$THEMES_FILE'
with open('$OUT', 'w') as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
print(json.dumps(m, ensure_ascii=False, indent=2))
"

echo ""
echo "Saved: $OUT"
