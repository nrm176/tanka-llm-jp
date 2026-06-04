#!/usr/bin/env bash
# Variance harness: 同一お題を N 回生成し、スコアの mean / std / min / max を出す。
#
# 目的: 短歌生成は「反復改善」ではなく best-of-N サンプリングなので、score は
# 確率変数。単一 run の avg_final_score は 1 サンプルにすぎない。このツールで
# 「同じお題がどれだけぶれるか」を定量化し、ベースライン比較に必要な
# ノイズ下限 (この差以下は誤差) を把握する。
#
# 使い方:
#   ./eval-repeat.sh "<お題>" [N] [backend-url]
#
# 例:
#   ./eval-repeat.sh "散りゆく桜" 5
#
# 出力: results/<ts>-repeat-<theme>.json  (全 run の score 配列 + 統計量)

set -euo pipefail

THEME="${1:?Usage: ./eval-repeat.sh \"<theme>\" [N] [backend-url]}"
N="${2:-5}"
BACKEND="${3:-http://localhost:8001}"
RESULTS_DIR="$(dirname "$0")/results"
mkdir -p "$RESULTS_DIR"
TS=$(date +%Y%m%d-%H%M%S)
# ファイル名スラッグ: 日本語は [:alnum:] (C locale) で消えるので、英数があればそれを使い、
# 無ければお題のバイトから短いハッシュを作る (日本語お題でも一意なファイル名になる)。
SAFE_THEME=$(printf '%s' "$THEME" | LC_ALL=C tr -cd '[:alnum:]' | cut -c1-20)
if [ -z "$SAFE_THEME" ]; then
  SAFE_THEME="t$(printf '%s' "$THEME" | cksum | cut -d' ' -f1)"
fi
OUT="$RESULTS_DIR/${TS}-repeat-${SAFE_THEME}.json"

echo "Theme:   $THEME"
echo "Runs:    $N"
echo "Backend: $BACKEND"
echo "Output:  $OUT"
echo ""

curl -sf "$BACKEND/api/health" >/dev/null || { echo "backend unreachable" >&2; exit 1; }

# 各 run は独立セッションで実行 (long-term failure 記憶の汚染を避け、純粋な variance を測る)
SCORES=()
for run in $(seq 1 "$N"); do
  printf "[run %d/%d] generating ... " "$run" "$N"
  SID=$(curl -s -X POST "$BACKEND/api/sessions" -H 'Content-Type: application/json' \
    -d "{\"title\":\"[repeat $run] $SAFE_THEME\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
  curl -s -X POST "$BACKEND/api/tanka" -H 'Content-Type: application/json' \
    -d "$(python3 -c "import json,sys;print(json.dumps({'session_id':'$SID','theme':sys.argv[1]}))" "$THEME")" >/dev/null

  # 完了待ち
  while true; do
    A=$(curl -s "$BACKEND/api/sessions/$SID/active-task" | python3 -c "import sys,json;print(json.load(sys.stdin).get('task_id') or '')")
    [ -z "$A" ] && break
    sleep 3
  done

  SCORE=$(curl -s "$BACKEND/api/sessions/$SID" | python3 -c "
import sys,json
s=json.load(sys.stdin)
ts=[m for m in s.get('messages',[]) if m.get('kind')=='tanka']
print(ts[-1].get('final_score') if ts and ts[-1].get('final_score') is not None else 'null')
")
  ATT=$(curl -s "$BACKEND/api/sessions/$SID" | python3 -c "
import sys,json
s=json.load(sys.stdin)
ts=[m for m in s.get('messages',[]) if m.get('kind')=='tanka']
print(len(ts[-1].get('validations',[])) if ts else 0)
")
  echo "score=$SCORE attempts=$ATT"
  SCORES+=("$SCORE")
  # 後始末: 一時セッション削除
  curl -s -X DELETE "$BACKEND/api/sessions/$SID" >/dev/null
done

echo ""
python3 - "$THEME" "$TS" "$OUT" "${SCORES[@]}" << 'PYEOF'
import sys, json, statistics
theme, ts, out = sys.argv[1], sys.argv[2], sys.argv[3]
scores = [int(x) for x in sys.argv[4:] if x != 'null']
res = {
    "theme": theme, "timestamp": ts, "n": len(scores), "scores": scores,
    "mean": round(statistics.mean(scores), 2) if scores else None,
    "stdev": round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
    "min": min(scores) if scores else None,
    "max": max(scores) if scores else None,
    "range": (max(scores) - min(scores)) if scores else None,
}
with open(out, "w") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print(f"=== variance: お題「{theme}」 (n={res['n']}) ===")
print(f"  scores: {scores}")
print(f"  mean:   {res['mean']}")
print(f"  stdev:  {res['stdev']}   (母標準偏差)")
print(f"  min-max: {res['min']} - {res['max']}  (range {res['range']})")
print()
print(f"  → 同一お題で ±{res['stdev']:.0f} ぶれる。ベースライン比較で")
print(f"    この幅以下のスコア差は sampling noise とみなすべき。")
print(f"\nSaved: {out}")
PYEOF
