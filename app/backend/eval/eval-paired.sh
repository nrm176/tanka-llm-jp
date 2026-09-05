#!/usr/bin/env bash
# Paired (interleaved) A/B 評価ハーネス。
#
# blocked A/B (arm A を全部 → arm B を全部) は LM Studio の sustained-load 劣化で後半が沈み、
# 効果の符号すら決まらなかった (FINDINGS §5.5)。本スクリプトは 2 つの arm を **お題ごとに交互実行**
# し、両 arm が同じ LM Studio 状態を経験するようにする (劣化が対称に作用)。位置効果を打ち消すため
# 実行順は ABBA で交互 (奇数番目のお題は A→B、偶数番目は B→A)。
#
# 使い方:
#   ./eval-paired.sh <variantA> '<jsonA>' <variantB> '<jsonB>' [themes.json] [backend-url]
#
#   <jsonX> は POST /api/tanka のボディに merge される per-request 上書き (JSON オブジェクト)。
#   例 (self-critique の ON/OFF):
#     caffeinate -dims ./eval-paired.sh sc-on '{"self_critique": true}' sc-off '{"self_critique": false}'
#
# 中断・再開:
#   Ctrl-C や停止 (kill $(cat results/eval-paired.pid)) 後、同じセッションで続きから走らせる:
#     RESUME_A=<sidA> RESUME_B=<sidB> ./eval-paired.sh sc-on '{...}' sc-off '{...}'
#   既に両 arm で完了しているお題はスキップ、片方だけ完了なら残る arm のみ実行する。
#
# 出力:
#   results/<ts>-<variantA>.json / results/<ts>-<variantB>.json — eval.sh と同形式 (compare.sh 互換)
#   results/<ts>-paired-<variantA>-vs-<variantB>.json — per-theme の paired difference と統計、
#   per-generation の事後検証 (phases に self_critique が期待どおり有る/無いか)
#
# 前提 (pre-flight で自動確認):
#   - backend health ok / configured_model が lms ps にロード済みで CONTEXT >= 32768 (§6.14 の JIT 罠防止)
#   - 実行中は backend の設定・コードを変えない / LM Studio に別の負荷を掛けない (skill Step 3 の禁則)

set -euo pipefail

VARIANT_A="${1:?Usage: ./eval-paired.sh <variantA> '<jsonA>' <variantB> '<jsonB>' [themes.json] [backend-url]}"
OVERRIDE_A="${2:?missing jsonA}"
VARIANT_B="${3:?missing variantB}"
OVERRIDE_B="${4:?missing jsonB}"
THEMES_FILE="${5:-$(dirname "$0")/eval_themes.json}"
BACKEND="${6:-http://localhost:8001}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="$HERE/results"
mkdir -p "$RESULTS_DIR"
echo $$ > "$RESULTS_DIR/eval-paired.pid"   # 停止は kill $(cat results/eval-paired.pid) (pkill は不一致で生き残る: §6.15)

TS=$(date +%Y%m%d-%H%M%S)
py() { python3 -c "$@"; }

echo "Arm A:   $VARIANT_A  override=$OVERRIDE_A"
echo "Arm B:   $VARIANT_B  override=$OVERRIDE_B"
echo "Themes:  $THEMES_FILE"
echo "Backend: $BACKEND"
echo ""

# ── pre-flight ──
for o in "$OVERRIDE_A" "$OVERRIDE_B"; do
  py "import json,sys; d=json.loads(sys.argv[1]); assert isinstance(d, dict), 'override must be a JSON object'" "$o" \
    || { echo "ERROR: override is not a JSON object: $o" >&2; exit 1; }
done
HEALTH=$(curl -sf "$BACKEND/api/health") || { echo "ERROR: backend not reachable at $BACKEND" >&2; exit 1; }
MODEL=$(py "import json,sys; h=json.loads(sys.argv[1]); assert h['status']=='ok', h; print(h['configured_model'])" "$HEALTH")
echo "Model:   $MODEL"
if command -v lms >/dev/null 2>&1; then
  # health.model_loaded は「一覧に存在する」だけで true になる (未ロードでも)。実ロードと context は lms ps で見る
  # lms ps の SIZE 列は "5.08 GB" のように空白を含むため列番号は不安定。モデル行の中で
  # 最初に現れる 1024 以上の整数フィールドを CONTEXT として読む
  CTX=$(lms ps 2>/dev/null | awk -v m="$MODEL" '$1==m { for (i=2;i<=NF;i++) if ($i ~ /^[0-9]+$/ && $i+0 >= 1024) { print $i; exit } }')
  if [ -z "$CTX" ]; then
    echo "ERROR: $MODEL is not loaded in LM Studio (or CONTEXT unreadable). Run: lms load $MODEL --context-length 32768" >&2; exit 1
  fi
  if [ "$CTX" -lt 32768 ]; then
    echo "ERROR: $MODEL is loaded with CONTEXT=$CTX (< 32768). JIT-load trap (§6.14). Reload: lms load $MODEL --context-length 32768" >&2; exit 1
  fi
  echo "LM Studio: $MODEL loaded, CONTEXT=$CTX"
else
  echo "WARN: lms CLI not found; cannot verify context length (§6.14)."
fi
echo ""

# ── お題 ──
[ -f "$THEMES_FILE" ] || { echo "ERROR: themes file not found: $THEMES_FILE" >&2; exit 1; }
mapfile -t THEME_LINES < <(py "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
for t in data['themes']:
    print(t['id'] + '\t' + t['theme'])
" "$THEMES_FILE")
N=${#THEME_LINES[@]}
[ "$N" -gt 0 ] || { echo "ERROR: no themes loaded from $THEMES_FILE" >&2; exit 1; }
echo "Loaded $N themes."

# ── セッション (新規 or resume) ──
mk_session() {  # $1=variant
  curl -s -X POST "$BACKEND/api/sessions" -H 'Content-Type: application/json' \
    -d "$(py "import json,sys; print(json.dumps({'title': '[eval] ' + sys.argv[1] + ' ' + sys.argv[2] + ' (paired)'}))" "$1" "$TS")" \
    | py "import sys,json; print(json.load(sys.stdin)['id'])"
}
SID_A="${RESUME_A:-$(mk_session "$VARIANT_A")}"
SID_B="${RESUME_B:-$(mk_session "$VARIANT_B")}"
echo "Session A: $SID_A"
echo "Session B: $SID_B"
echo "(resume later with: RESUME_A=$SID_A RESUME_B=$SID_B $0 ...)"
echo ""

done_themes() {  # $1=sid → 完了済み (tanka 本文あり) のお題を 1 行ずつ
  curl -s "$BACKEND/api/sessions/$1" | py "
import sys, json
for m in json.load(sys.stdin).get('messages', []):
    if m.get('kind') == 'tanka' and m.get('tanka'):
        print(m.get('theme', ''))
"
}
DONE_A="$(done_themes "$SID_A")"
DONE_B="$(done_themes "$SID_B")"

run_one() {  # $1=sid $2=theme $3=override-json $4=label
  local body tid active
  body=$(py "import json,sys; d=json.loads(sys.argv[3]); d.update({'session_id': sys.argv[1], 'theme': sys.argv[2]}); print(json.dumps(d, ensure_ascii=False))" "$1" "$2" "$3")
  tid=$(curl -s -X POST "$BACKEND/api/tanka" -H 'Content-Type: application/json' -d "$body" \
    | py "import sys,json; print(json.load(sys.stdin).get('task_id',''))")
  if [ -z "$tid" ]; then echo "    $4: FAILED (no task_id)"; return; fi
  local t0=$SECONDS
  while true; do
    active=$(curl -s "$BACKEND/api/sessions/$1/active-task" | py "import sys,json; print(json.load(sys.stdin).get('task_id') or '')")
    [ -z "$active" ] && break
    sleep 3
  done
  # 完了 ≠ 成功: 失敗 (LLM エラー / backend 再起動で orphan 化) した生成は tanka を残さない。
  # その場で検知して報告する (末尾の paired 集計は完成した pair だけを数える)
  local got
  got=$(curl -s "$BACKEND/api/sessions/$1" | py "
import sys, json
theme = sys.argv[1]
ms = [m for m in json.load(sys.stdin).get('messages', []) if m.get('kind') == 'tanka' and m.get('tanka') and m.get('theme') == theme]
print(ms[-1].get('final_score') if ms else '')
" "$2")
  if [ -n "$got" ]; then
    echo "    $4: done ($((SECONDS - t0))s, score $got)"
  else
    echo "    $4: NO RESULT after $((SECONDS - t0))s — task failed or backend restarted mid-run (再開: RESUME_A/RESUME_B で再実行される)"
  fi
}

i=0
for line in "${THEME_LINES[@]}"; do
  i=$((i + 1))
  tid_label="${line%%$'\t'*}"; theme="${line#*$'\t'}"
  printf "[%2d/%2d] %-8s %s\n" "$i" "$N" "$tid_label" "$(echo "$theme" | cut -c1-40)"
  need_a=1; need_b=1
  grep -qxF "$theme" <<<"$DONE_A" && need_a=0
  grep -qxF "$theme" <<<"$DONE_B" && need_b=0
  if [ $need_a = 0 ] && [ $need_b = 0 ]; then echo "    skip (both arms done)"; continue; fi
  # ABBA: 奇数番目は A→B、偶数番目は B→A (位置効果の打ち消し)
  if [ $((i % 2)) = 1 ]; then order="A B"; else order="B A"; fi
  for arm in $order; do
    if [ "$arm" = A ] && [ $need_a = 1 ]; then run_one "$SID_A" "$theme" "$OVERRIDE_A" "$VARIANT_A"; fi
    if [ "$arm" = B ] && [ $need_b = 1 ]; then run_one "$SID_B" "$theme" "$OVERRIDE_B" "$VARIANT_B"; fi
  done
done

# ── メトリクス (eval.sh 互換) + paired 統計 + 事後検証 ──
echo ""
echo "Collecting metrics ..."
save_metrics() {  # $1=sid $2=variant $3=override $4=paired-with → results file path
  local out="$RESULTS_DIR/${TS}-$2.json"
  curl -s "$BACKEND/api/metrics?session_id=$1" | py "
import sys, json
m = json.load(sys.stdin)
m.update({'_variant': sys.argv[2], '_session_id': sys.argv[1], '_timestamp': sys.argv[5], '_themes_file': sys.argv[6],
          '_override': json.loads(sys.argv[3]), '_paired_with': sys.argv[4], '_design': 'paired-ABBA'})
json.dump(m, open(sys.argv[7], 'w'), ensure_ascii=False, indent=2)
" "$1" "$2" "$3" "$4" "$TS" "$THEMES_FILE" "$out"
  echo "$out"
}
OUT_A=$(save_metrics "$SID_A" "$VARIANT_A" "$OVERRIDE_A" "$VARIANT_B")
OUT_B=$(save_metrics "$SID_B" "$VARIANT_B" "$OVERRIDE_B" "$VARIANT_A")
echo "Saved: $OUT_A"
echo "Saved: $OUT_B"

PAIRED_OUT="$RESULTS_DIR/${TS}-paired-${VARIANT_A}-vs-${VARIANT_B}.json"
curl -s "$BACKEND/api/sessions/$SID_A" > "$RESULTS_DIR/.sess_a.json"
curl -s "$BACKEND/api/sessions/$SID_B" > "$RESULTS_DIR/.sess_b.json"
py "$(cat <<'PYEOF'
import json, sys, statistics as st
sa, sb, out, va, vb, oa, ob = sys.argv[1:8]
oa, ob = json.loads(oa), json.loads(ob)
def tankas(path):
    d = json.load(open(path))
    return {m['theme']: m for m in d.get('messages', []) if m.get('kind') == 'tanka' and m.get('tanka')}
A, B = tankas(sa), tankas(sb)
common = [t for t in A if t in B]
incomplete = sorted((set(A) | set(B)) - set(common))
rows = []
for t in common:
    a, b = A[t], B[t]
    rows.append({'theme': t, 'score_a': a.get('final_score'), 'score_b': b.get('final_score'),
                 'diff': (a.get('final_score') or 0) - (b.get('final_score') or 0),
                 'dur_a': a.get('duration_seconds'), 'dur_b': b.get('duration_seconds'),
                 'attempts_a': len(a.get('validations') or []), 'attempts_b': len(b.get('validations') or [])})
diffs = [r['diff'] for r in rows]
n = len(diffs)
mean = st.mean(diffs) if n else None
sd = st.stdev(diffs) if n > 1 else None
se = sd / n ** 0.5 if sd is not None else None
# 事後検証: self_critique の上書きが phases に反映されているか (skill Step 2)
def check(msgs, override, label):
    exp = override.get('self_critique')
    if exp is None: return {'arm': label, 'checked': 0, 'mismatch': 0, 'note': 'no self_critique override'}
    mism = 0
    for m in msgs.values():
        has = any(p.get('phase') == 'self_critique' for p in (m.get('phases') or []))
        if has != exp: mism += 1
    return {'arm': label, 'expected_self_critique': exp, 'checked': len(msgs), 'mismatch': mism}
verif = [check(A, oa, va), check(B, ob, vb)]
def mean_of(key):
    xs = [r[key] for r in rows if isinstance(r[key], (int, float))]
    return round(st.mean(xs), 1) if xs else None
summary = {
    'design': 'paired-ABBA', 'variant_a': va, 'variant_b': vb, 'n_pairs': n,
    'mean_score_a': mean_of('score_a'), 'mean_score_b': mean_of('score_b'),
    'mean_diff_a_minus_b': round(mean, 2) if mean is not None else None,
    'sd_diff': round(sd, 2) if sd is not None else None,
    'se_diff': round(se, 2) if se is not None else None,
    'two_se': round(2 * se, 2) if se is not None else None,
    'wins_a': sum(1 for d in diffs if d > 0), 'wins_b': sum(1 for d in diffs if d < 0), 'ties': sum(1 for d in diffs if d == 0),
    'mean_duration_a': mean_of('dur_a'), 'mean_duration_b': mean_of('dur_b'),
    'mean_attempts_a': mean_of('attempts_a'), 'mean_attempts_b': mean_of('attempts_b'),
    'verification': verif,
    'incomplete_themes': incomplete,  # 片方の arm しか完了していないお題 (paired 集計から除外)
}
json.dump({'summary': summary, 'pairs': rows}, open(out, 'w'), ensure_ascii=False, indent=2)
print()
print(f"=== Paired result: {va} (A) vs {vb} (B), n={n} pairs ===")
print(f"  mean score      A {summary['mean_score_a']}  vs  B {summary['mean_score_b']}")
print(f"  mean diff (A-B) {summary['mean_diff_a_minus_b']}   sd {summary['sd_diff']}   2SE {summary['two_se']}")
print(f"  wins            A {summary['wins_a']} / B {summary['wins_b']} / ties {summary['ties']}")
print(f"  mean duration   A {summary['mean_duration_a']}s  vs  B {summary['mean_duration_b']}s   (cost side)")
print(f"  mean attempts   A {summary['mean_attempts_a']}  vs  B {summary['mean_attempts_b']}")
for v in verif:
    print(f"  verification    {v}")
if incomplete:
    print(f"  ⚠ incomplete    {len(incomplete)} theme(s) missing in one arm (excluded from pairs): {incomplete}")
if summary['two_se'] is not None:
    verdict = "有意 (|mean diff| > 2SE)" if abs(mean) > 2 * se else "判定不能 (|mean diff| <= 2SE、ノイズ内)"
    print(f"  → {verdict}  ※ 事前登録した判定基準と照合すること (runbook)")
print(f"Saved: {out}")
PYEOF
)" "$RESULTS_DIR/.sess_a.json" "$RESULTS_DIR/.sess_b.json" "$PAIRED_OUT" "$VARIANT_A" "$VARIANT_B" "$OVERRIDE_A" "$OVERRIDE_B"
rm -f "$RESULTS_DIR/.sess_a.json" "$RESULTS_DIR/.sess_b.json" "$RESULTS_DIR/eval-paired.pid"
