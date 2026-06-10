"""季語コピー副作用の end-to-end 実測 (issue #5)。

compose 単段では dynamic few-shot の単一例の季語 (夏→「夏の夜」) をコピーする傾向が見えたが、
実パイプラインでは kigo_matches_plan(-20) が発火して refine が矯正するはず。
本スクリプトは **フルパイプライン (plan→compose→self-critique→validate→refine)** を回し、
最終出力の季語が plan の季語を保てているか (refine 後の retention) を測る。

dynamic few-shot は既定 ON。非春テーマで「plan→final の季語/季節 一致率」を見る。
結果は results/kigo_retention_e2e.json に逐次書き出す。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tanka
import validator

REPS = int(os.environ.get("ISO_REPS", "2"))
MAX_REFINES = int(os.environ.get("ISO_MAX_REFINES", "5"))
OUT = Path(__file__).parent / "results" / "kigo_retention_e2e.json"

# few-shot 各季の例の季語 (final にこれが出たら「例コピー」シグナル)
FEWSHOT_KIGO = {"春": "花", "夏": "夏の夜", "秋": "秋風", "冬": "冬"}

THEMES = ["夏の夕暮れ", "蛍の舞う川辺", "紅葉の散る山道", "冬の寂寥"]


async def run_one(theme: str) -> dict | None:
    final = None
    try:
        async for ev in tanka.generate_tanka_pipeline(theme, max_refines=MAX_REFINES):
            if ev.get("type") == "complete":
                final = ev
    except Exception as e:
        return {"theme": theme, "error": f"{type(e).__name__}: {e}"}
    if not final:
        return {"theme": theme, "error": "no complete event"}
    plan = final.get("plan") or ""
    pk = validator.extract_kigo_from_plan(plan)
    ps = validator.extract_season_from_plan(plan)
    fk, fs = final.get("kigo"), final.get("season")
    return {
        "theme": theme, "plan_season": ps, "plan_kigo": pk,
        "final_season": fs, "final_kigo": fk, "score": final.get("score"),
        "season_match": fs == ps if ps else None,
        "kigo_match": fk == pk if pk else None,
        "copied_example_kigo": fk == FEWSHOT_KIGO.get(fs),
    }


async def main():
    records = []
    OUT.parent.mkdir(exist_ok=True)
    print(f"REPS={REPS} themes={len(THEMES)} max_refines={MAX_REFINES} runs={REPS*len(THEMES)}", flush=True)
    for rep in range(1, REPS + 1):
        for theme in THEMES:
            r = await run_one(theme)
            r["rep"] = rep
            records.append(r)
            OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            if r.get("error"):
                print(f"  rep{rep} {theme[:8]:8} ✗ {r['error']}", flush=True)
            else:
                km = "✓" if r["kigo_match"] else "✗"
                sm = "✓" if r["season_match"] else "✗"
                cp = " ★例コピー" if r["copied_example_kigo"] and not r["kigo_match"] else ""
                print(f"  rep{rep} {theme[:8]:8} 季{sm} 季語{km}  plan={r['plan_season']}/{r['plan_kigo']} "
                      f"→ {r['final_season']}/{r['final_kigo']} (score {r['score']}){cp}", flush=True)

    ok = [r for r in records if not r.get("error")]
    with_pk = [r for r in ok if r["plan_kigo"]]
    season_ok = [r for r in with_pk if r["season_match"]]
    kigo_ok_given_season = [r for r in season_ok if r["kigo_match"]]

    def pct(a, b):
        return f"{len(a)}/{len(b)} = {round(100*len(a)/len(b)) if b else 0}%"

    print("\n===== end-to-end 集計 (dynamic few-shot ON) =====", flush=True)
    print(f"  完了: {len(ok)}/{len(records)}  (parse/LLM 失敗 {len(records)-len(ok)})", flush=True)
    print(f"  季 一致 (plan→final): {pct([r for r in with_pk if r['season_match']], with_pk)}", flush=True)
    print(f"  季語 一致 (季正しい歌に限定): {pct(kigo_ok_given_season, season_ok)}  ← refine 後の retention", flush=True)
    copies = [r for r in season_ok if r["copied_example_kigo"] and not r["kigo_match"]]
    print(f"  季正しい × 例の季語へコピー (最終に残存): {len(copies)} 件", flush=True)
    print(f"\n結果: {OUT}\nDONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
