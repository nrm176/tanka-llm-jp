"""#76 paired A/B (loose vs strict 救済) の判定スクリプト。RUNBOOK-76-strict-disputed.md §0 の指標を出す。

使い方: uv run python eval/experiments/self-critique-paired/analyze-76.py <sid_loose> <sid_strict> [backend]
主指標 = 最終短歌 5 句のうち pykakasi 計算が規定拍でない句の数 (救済ルール非依存)。Δ = strict − loose。"""
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
    """最終採用 attempt の Tanka (共通尺度での再採点用)。phases から best attempt を探す。"""
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


A, B = fetch(sid_a), fetch(sid_b)
themes = [t for t in A if t in B]
print(f"paired n = {len(themes)}  (loose {len(A)} / strict {len(B)} 完了)")

dev_a = [deviations(A[t]["tanka"]) for t in themes]
dev_b = [deviations(B[t]["tanka"]) for t in themes]
d = [b - a for a, b in zip(dev_a, dev_b)]
mean, sd, se2 = paired(d)
print(f"\n【主指標】拍数逸脱句数 (0-5, pykakasi 基準)  loose {sum(dev_a)/len(d):.2f} / strict {sum(dev_b)/len(d):.2f}")
print(f"  paired Δ (strict−loose) = {mean:+.3f}  2SE = {se2:.3f}  → "
      + ("**有意** (strict が" + ("改善" if mean < 0 else "悪化") + ")" if abs(mean) > se2 else "判定不能 (ノイズ内)"))
print(f"  wins/ties/losses (strict が少ない/同じ/多い): {sum(x<0 for x in d)}/{sum(x==0 for x in d)}/{sum(x>0 for x in d)}")

# 副指標
def common_score(m):
    t = final_obj(m)
    if t is None:
        return None
    return validator.evaluate(t, expected_season=validator.extract_season_from_plan(m.get("plan") or ""),
                              expected_kigo=validator.extract_kigo_from_plan(m.get("plan") or ""),
                              theme=m["theme"], strict_disputed=True).score
cs = [(common_score(A[t]), common_score(B[t])) for t in themes]
cs_ok = [(a, b) for a, b in cs if a is not None and b is not None]
if cs_ok:
    m2, _, se2b = paired([b - a for a, b in cs_ok])
    print(f"\n【副】共通尺度 (strict ルール) 再採点の最終スコア  loose {sum(a for a,_ in cs_ok)/len(cs_ok):.2f} / "
          f"strict {sum(b for _,b in cs_ok)/len(cs_ok):.2f}  Δ {m2:+.2f} 2SE {se2b:.2f}  (n={len(cs_ok)})")
att = lambda m: len(m.get("validations") or [])
print(f"【副】平均 attempt  loose {sum(att(A[t]) for t in themes)/len(themes):.2f} / strict {sum(att(B[t]) for t in themes)/len(themes):.2f}")
dur = lambda m: m.get("duration_seconds") or 0
print(f"【副】平均所要秒    loose {sum(dur(A[t]) for t in themes)/len(themes):.0f} / strict {sum(dur(B[t]) for t in themes)/len(themes):.0f}")
def final_has(m, rule):
    vals = m.get("validations") or []
    best = max(vals, key=lambda v: (v.get("score", 0), -v.get("attempt", 0))) if vals else None
    return bool(best and any(v.get("rule") == rule for v in best.get("violations") or []))
for rule in ("mora_count_disputed", "mora_count_off_by_one", "mora_count"):
    print(f"【副】最終短歌に {rule:22s} loose {sum(final_has(A[t], rule) for t in themes)} / strict {sum(final_has(B[t], rule) for t in themes)}")

# 事後検証 (上書きが届いたか)
def n_rejected(m):
    return sum("採用しません" in (v.get("message") or "") for val in m.get("validations") or [] for v in val.get("violations") or [])
ra, rb = sum(n_rejected(A[t]) for t in themes), sum(n_rejected(B[t]) for t in themes)
sc = lambda m: any(p.get("phase") == "self_critique" for p in m.get("phases") or [])
print(f"\n【事後検証】「採用しません」 loose {ra} 件 (0 のはず) / strict {rb} 件 (≥1 のはず)   "
      f"self_critique phase: loose {sum(sc(A[t]) for t in themes)} / strict {sum(sc(B[t]) for t in themes)} (0 のはず)")
h = len(themes) // 2
print(f"【交絡】前半/後半の Δ: {sum(d[:h])/h:+.2f} / {sum(d[h:])/(len(d)-h):+.2f}")
