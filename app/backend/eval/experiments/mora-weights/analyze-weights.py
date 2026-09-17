"""拍数重み実験 (base 3/3/10 vs heavy 12/12/25) の判定。RUNBOOK.md §0 の指標を出す。

使い方: uv run python eval/experiments/mora-weights/analyze-weights.py <sid_base> <sid_heavy> [backend]
主指標: (1) 最終短歌の 5-7-5-7-7 完全一致率 (pykakasi 基準) の paired Δ、(2) 拍数逸脱句数の paired Δ。
score の arm 間直接比較は尺度が違うので無効 → 共通重み (base) で再採点した値を副指標に出す。"""
import json
import logging
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logging.disable(logging.CRITICAL)
import llm  # noqa: E402
import reading  # noqa: E402
import validator  # noqa: E402

EXPECTED = [5, 7, 5, 7, 7]
HEAVY = {"mora_count_off_by_one": 12, "mora_count_disputed": 12, "mora_count": 25}
sid_a, sid_b = sys.argv[1], sys.argv[2]
backend = sys.argv[3] if len(sys.argv) > 3 else "http://localhost:8001"


def fetch(sid):
    with urllib.request.urlopen(f"{backend}/api/sessions/{sid}") as r:
        s = json.load(r)
    return {m["theme"]: m for m in s.get("messages", []) if m.get("kind") == "tanka" and m.get("tanka")}


def deviations(tanka_text):
    lines = tanka_text.split("\n")
    return sum(1 for i, ln in enumerate(lines[:5]) if reading.count_moras(reading.kanji_to_hira(ln)) != EXPECTED[i])


def final_obj(m):
    best = None
    for p in m.get("phases") or []:
        if p.get("phase") not in ("compose", "refine", "self_critique"):
            continue
        think, ans = llm.split_harmony(p.get("raw") or "")
        t = validator.parse_tanka_json(ans or p.get("raw") or "")
        if isinstance(t, tuple):
            continue
        if "\n".join(l.body for l in t.lines) == m["tanka"]:
            best = t
    return best


def paired(xs):
    n = len(xs)
    mean = sum(xs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return mean, sd, 2 * sd / math.sqrt(n)


def verdict(mean, se2, better_when_negative=True):
    if abs(mean) <= se2:
        return "判定不能 (ノイズ内)"
    good = (mean < 0) == better_when_negative
    return "**有意** (heavy が" + ("改善" if good else "悪化") + ")"


A, B = fetch(sid_a), fetch(sid_b)
themes = [t for t in A if t in B]
n = len(themes)
print(f"paired n = {n}  (base {len(A)} / heavy {len(B)} 完了)")

dev_a = [deviations(A[t]["tanka"]) for t in themes]
dev_b = [deviations(B[t]["tanka"]) for t in themes]
ex_a = [int(d == 0) for d in dev_a]
ex_b = [int(d == 0) for d in dev_b]
m1, _, s1 = paired([b - a for a, b in zip(ex_a, ex_b)])
print(f"\n【主 1】5-7-5-7-7 完全一致率  base {sum(ex_a)/n:.0%} / heavy {sum(ex_b)/n:.0%}   paired Δ {m1:+.3f}  2SE {s1:.3f}  → "
      + verdict(m1, s1, better_when_negative=False))
m2, _, s2 = paired([b - a for a, b in zip(dev_a, dev_b)])
print(f"【主 2】拍数逸脱句数 / 首      base {sum(dev_a)/n:.2f} / heavy {sum(dev_b)/n:.2f}   paired Δ {m2:+.3f}  2SE {s2:.3f}  → "
      + verdict(m2, s2))
d = [b - a for a, b in zip(dev_a, dev_b)]
print(f"        wins/ties/losses (heavy が少ない/同じ/多い): {sum(x<0 for x in d)}/{sum(x==0 for x in d)}/{sum(x>0 for x in d)}")


def common_score(m):
    t = final_obj(m)
    if t is None:
        return None
    return validator.evaluate(t, expected_season=validator.extract_season_from_plan(m.get("plan") or ""),
                              expected_kigo=validator.extract_kigo_from_plan(m.get("plan") or ""),
                              theme=m["theme"]).score  # 共通重み = base (上書きなし)
cs = [(common_score(A[t]), common_score(B[t])) for t in themes]
cs_ok = [(a, b) for a, b in cs if a is not None and b is not None]
if cs_ok:
    m3, _, s3 = paired([b - a for a, b in cs_ok])
    print(f"\n【副】共通重み (base) 再採点の最終スコア  base {sum(a for a,_ in cs_ok)/len(cs_ok):.2f} / heavy {sum(b for _,b in cs_ok)/len(cs_ok):.2f}"
          f"   Δ {m3:+.2f}  2SE {s3:.2f}  (n={len(cs_ok)})")
att = lambda m: len(m.get("validations") or [])
dur = lambda m: m.get("duration_seconds") or 0
print(f"【副・コスト】平均 attempt  base {sum(att(A[t]) for t in themes)/n:.2f} / heavy {sum(att(B[t]) for t in themes)/n:.2f}")
print(f"【副・コスト】平均所要秒    base {sum(dur(A[t]) for t in themes)/n:.0f} / heavy {sum(dur(B[t]) for t in themes)/n:.0f}")
first_pass = lambda m: (m.get("validations") or [{}])[0].get("score", 0) >= validator.PASS_THRESHOLD
print(f"【副】初回合格率            base {sum(first_pass(A[t]) for t in themes)/n:.0%} / heavy {sum(first_pass(B[t]) for t in themes)/n:.0%}")
plateau = lambda m: bool(m.get("plateau_reached"))
print(f"【副】plateau 打ち切り      base {sum(plateau(A[t]) for t in themes)} / heavy {sum(plateau(B[t]) for t in themes)}")
# refine が逸脱を実際に減らしたか: attempt 0 の拍数系違反句数 → 最終
def first_dev(m):
    v = (m.get("validations") or [{}])[0]
    return sum(1 for x in v.get("violations") or [] if x.get("rule", "").startswith("mora_count"))
print(f"【副】拍数系違反句数 初回→最終  base {sum(first_dev(A[t]) for t in themes)/n:.2f}→{sum(dev_a)/n:.2f} / "
      f"heavy {sum(first_dev(B[t]) for t in themes)/n:.2f}→{sum(dev_b)/n:.2f}")

# 事後検証: 重みが届いたか (off_by_one の weight)
def weights_seen(m):
    return {x["weight"] for val in m.get("validations") or [] for x in val.get("violations") or [] if x.get("rule") == "mora_count_off_by_one"}
wa = set().union(*(weights_seen(A[t]) for t in themes)); wb = set().union(*(weights_seen(B[t]) for t in themes))
sc = lambda m: any(p.get("phase") == "self_critique" for p in m.get("phases") or [])
print(f"\n【事後検証】off_by_one の weight  base {sorted(wa)} ({{3}} のはず) / heavy {sorted(wb)} ({{12}} のはず)   "
      f"self_critique: {sum(sc(A[t]) for t in themes)}/{sum(sc(B[t]) for t in themes)} (0 のはず)")
h = n // 2
print(f"【交絡】前半/後半の Δ(逸脱句数): {sum(d[:h])/h:+.2f} / {sum(d[h:])/(n-h):+.2f}")
