"""RAG framing の統制実験 (metadata-first の効果測定)。

問い: 動的 few-shot を ON にして春の主磁石を消した状態でも、RAG が注入する「春過ぎて…」型の
異季アンカーは残留ドリフトを起こすか? metadata-first 整形 (季・季語・狙いを主役にし、異季
トークンを無効化注釈で中和) はそれを減らすか?

統制: plan / plan_constraint / system / temperature を固定し、**dynamic_fewshot=True を全条件で固定**。
変数は RAG ブロックの整形だけ:
  - rag_off    : 注入なし (フロア。前回実験で 0% ドリフトを確認済の状態と同等)
  - rag_legacy : 旧 examples-first (歌が主役、春過ぎての冒頭「春」が裸で出る)
  - rag_meta   : 新 metadata-first (季=夏 を枠で示し、「春」を無効化注釈で中和)

対象は exact 季語一致が無く 夏 fallback で「春過ぎて…」が必ず注入される 夏テーマ。
公平性: 3条件を (theme,rep) ごとに隣接実行し rep-major。production と同一パース。
結果は results/rag_framing_isolation.{json,log} に逐次書き出す。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm
import prompts
import rag
import validator

REPS = int(os.environ.get("ISO_REPS", "3"))
OUT = Path(__file__).parent / "results" / "rag_framing_isolation.json"

# 夏テーマ。季語は意図的にコーパスに exact 一致が無いもの → 夏 fallback で「春過ぎて…」が注入される。
CASES = [
    {"theme": "夏の夕暮れ",       "season": "夏", "kigo": "蛍",
     "plan": "季語: 蛍\n季節: 夏\n情景: 川辺に蛍が舞う夏の夕暮れ\n心情: 切なくも温かな郷愁"},
    {"theme": "夕立のあとの虹",   "season": "夏", "kigo": "夕立",
     "plan": "季語: 夕立\n季節: 夏\n情景: 夕立が上がり空に虹がかかる\n心情: 雨後のすがすがしさと安堵"},
    {"theme": "蝉時雨の午後",     "season": "夏", "kigo": "蝉時雨",
     "plan": "季語: 蝉時雨\n季節: 夏\n情景: 真昼の木立に蝉時雨が降りそそぐ\n心情: 過ぎゆく夏への切迫した愛惜"},
]

CONDS = ("rag_off", "rag_legacy", "rag_meta")


def build_rag_block(case: dict, cond: str) -> tuple[str, bool]:
    """条件に応じた RAG ブロックを作る。戻り値は (block, 春過ぎてが含まれるか)。"""
    if cond == "rag_off":
        return "", False
    poems = rag.retrieve(case["season"], case["kigo"])
    block = rag._format_examples_legacy(poems) if cond == "rag_legacy" else rag._format_metadata_first(poems)
    has_haru = any(p.get("text", "").startswith("春過ぎて") for p in poems)
    return block, has_haru


async def compose_once(case: dict, cond: str) -> dict:
    rag_block, has_haru = build_rag_block(case, cond)
    msgs = prompts.build_compose_messages(
        case["theme"], case["plan"],
        plan_constraint=prompts.build_plan_constraint(case["season"], case["kigo"]),
        rag_block=rag_block, long_term_block="",
        season_hint=case["season"], dynamic_fewshot=True,   # 全条件で動的 few-shot ON 固定
    )
    raw = ""
    try:
        async for delta in llm.stream_completion(msgs):
            raw += delta
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "season": None, "kigo": None, "haru": has_haru}
    _, text = llm.split_harmony(raw)
    parsed = validator.parse_tanka_json(text)
    if isinstance(parsed, tuple):
        return {"ok": False, "error": f"parse: {parsed[1]}", "season": None, "kigo": None, "haru": has_haru}
    return {"ok": True, "error": None, "season": parsed.season, "kigo": parsed.kigo, "haru": has_haru}


async def main():
    records = []
    OUT.parent.mkdir(exist_ok=True)
    print(f"REPS={REPS}  themes={len(CASES)}  conds={len(CONDS)}  calls={REPS*len(CASES)*len(CONDS)}", flush=True)
    for rep in range(1, REPS + 1):
        for case in CASES:
            for cond in CONDS:
                r = await compose_once(case, cond)
                drift = r["ok"] and r["season"] != case["season"]
                to_spring = r["ok"] and r["season"] == "春"
                rec = {"rep": rep, "theme": case["theme"], "expect": case["season"],
                       "cond": cond, **r, "drift": drift, "to_spring": to_spring}
                records.append(rec)
                OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                mark = "✓" if r["ok"] else "✗"
                tag = "🌸春!" if to_spring else ("→" + str(r["season"]) if drift else "OK")
                print(f"  rep{rep} {case['theme'][:7]:7} {cond:10} {mark} "
                      f"season={r['season']} kigo={r['kigo']} {tag}  {r.get('error') or ''}", flush=True)

    def tally(cond):
        rows = [x for x in records if x["cond"] == cond]
        ok = [x for x in rows if x["ok"]]
        spring = [x for x in ok if x["to_spring"]]
        drift = [x for x in ok if x["drift"]]
        return len(rows), len(ok), len(drift), len(spring)
    print("\n===== 集計 (全条件 dynamic_fewshot=ON、春過ぎて注入あり) =====", flush=True)
    print(f"{'cond':12} {'計':>3} {'parse成功':>8} {'季ドリフト':>10} {'→春':>6}", flush=True)
    for cond in CONDS:
        n, ok, drift, spring = tally(cond)
        print(f"{cond:12} {n:>3} {ok:>8} {drift:>10} {spring:>6}", flush=True)
    print(f"\n結果: {OUT}\nDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
