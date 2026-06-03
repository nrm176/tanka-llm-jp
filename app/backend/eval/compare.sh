#!/usr/bin/env bash
# Phase 2: 2 つの eval 結果を比較してメトリクスの差分を表示する。
#
# 使い方:
#   ./compare.sh <before> <after>
#
#   <before> / <after> は以下のいずれか:
#     - results/ 内の variant 名 (最新のファイルが自動選択される)
#     - 結果 JSON ファイルへのパス
#
# 例:
#   ./compare.sh baseline no-self-critique
#   ./compare.sh results/20260105-120000-baseline.json results/20260105-130000-variant.json

set -euo pipefail

RESULTS_DIR="$(dirname "$0")/results"

resolve() {
  # 引数がファイルならそのまま、variant 名なら最新の一致ファイルを返す
  local arg="$1"
  if [ -f "$arg" ]; then
    echo "$arg"
  else
    local latest
    latest=$(ls -t "$RESULTS_DIR"/*-"$arg".json 2>/dev/null | head -1 || true)
    if [ -z "$latest" ]; then
      echo "ERROR: no result file found for '$arg'" >&2
      exit 1
    fi
    echo "$latest"
  fi
}

BEFORE=$(resolve "${1:?Usage: ./compare.sh <before> <after>}")
AFTER=$(resolve "${2:?Usage: ./compare.sh <before> <after>}")

python3 - "$BEFORE" "$AFTER" << 'PYEOF'
import json, sys

before_path, after_path = sys.argv[1], sys.argv[2]
with open(before_path) as f: b = json.load(f)
with open(after_path) as f: a = json.load(f)

def pct(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "n/a"
def num(x):
    return f"{x:.2f}" if isinstance(x, (int, float)) else "n/a"

print(f"\nBEFORE: {b.get('_variant','?')}  (n={b.get('total',0)}, {before_path})")
print(f"AFTER:  {a.get('_variant','?')}  (n={a.get('total',0)}, {after_path})")
print("=" * 72)

def diff_row(label, key, fmt, higher_better=True):
    bv = b.get(key)
    av = a.get(key)
    bs = fmt(bv)
    as_ = fmt(av)
    arrow = ""
    if isinstance(bv, (int, float)) and isinstance(av, (int, float)):
        d = av - bv
        if abs(d) < 1e-9:
            arrow = "  ="
        else:
            good = (d > 0) == higher_better
            sign = "+" if d > 0 else ""
            mark = "改善" if good else "劣化"
            arrow = f"  {sign}{fmt(d) if not callable(fmt) else d:.4f} [{mark}]" if False else f"  ({sign}{d:.4f}) {mark}"
    print(f"  {label:28s} {bs:>10s} -> {as_:>10s}{arrow}")

print("\n[ 品質指標 ]")
diff_row("初回合格率",        "first_attempt_pass_rate", pct, True)
diff_row("総合合格率",        "overall_pass_rate",       pct, True)
diff_row("平均 attempt 数",   "avg_attempts",            num, False)
diff_row("平均最終スコア",     "avg_final_score",         num, True)
diff_row("plateau 率",        "plateau_rate",            pct, False)
diff_row("max_refines 率",    "max_refines_rate",        pct, False)

print("\n[ スコア分布 ]")
bb = b.get("score_buckets", {})
ab = a.get("score_buckets", {})
for bucket in ["0-39", "40-59", "60-79", "80-89", "90-100"]:
    print(f"  {bucket:28s} {bb.get(bucket,0):>10d} -> {ab.get(bucket,0):>10d}")

print("\n[ 違反頻度 (上位; before -> after の総出現数) ]")
all_rules = set(b.get("violation_frequency", {})) | set(a.get("violation_frequency", {}))
rows = []
for r in all_rules:
    bv = b.get("violation_frequency", {}).get(r, 0)
    av = a.get("violation_frequency", {}).get(r, 0)
    rows.append((r, bv, av, av - bv))
rows.sort(key=lambda x: -(x[1] + x[2]))
for r, bv, av, d in rows[:12]:
    mark = ""
    if d != 0:
        mark = f"  ({'+' if d>0 else ''}{d}) {'悪化' if d>0 else '改善'}"
    print(f"  {r:28s} {bv:>10d} -> {av:>10d}{mark}")

print()
PYEOF
