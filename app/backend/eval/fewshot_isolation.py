"""動的 few-shot の統制実験 (compose 段の単離 A/B)。

仮説: 非春 plan に対し、四季全部の few-shot を見せる (OFF) と #1=散る桜/春 へ regress する。
お題の季の例だけを見せる (ON) と春ドリフトが減る。

統制: plan / plan_constraint / system / temperature を固定し、few-shot の dynamic フラグ
だけを OFF/ON で切り替える。RAG・長期失敗ブロックは空にして交絡を除く (plan_constraint は
production 同様に常時付与し、両条件で同一 = バイアスにならない)。

公平性: OFF/ON をペアで隣接実行し、rep-major 順 (劣化しても全テーマを最低 1 反復カバー)。
パースは production と同一経路 (stream_completion → split_harmony → parse_tanka_json)。

結果は results/fewshot_isolation.json に逐次書き出す (途中で LM が落ちても部分結果が残る)。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# eval/ から実行されるため、backend ルート (llm.py 等がある) を import path に足す。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm
import prompts
import validator

REPS = int(os.environ.get("ISO_REPS", "3"))
OUT = Path(__file__).parent / "results" / "fewshot_isolation.json"

# 非春・ドリフト多発テーマ。plan はモデルの plan 出力スタイル (テキスト形式) に合わせる。
CASES = [
    {"theme": "夏の夕暮れ",     "season": "夏", "kigo": "蛍",
     "plan": "季語: 蛍\n季節: 夏\n情景: 川辺に蛍が舞う夏の夕暮れ\n心情: 切なくも温かな郷愁"},
    {"theme": "紅葉の散る山道", "season": "秋", "kigo": "紅葉",
     "plan": "季語: 紅葉\n季節: 秋\n情景: 山道に紅葉が散り敷く午後\n心情: 移ろいゆくものへの寂寥"},
    {"theme": "名月を仰ぐ宵",   "season": "秋", "kigo": "名月",
     "plan": "季語: 名月\n季節: 秋\n情景: 澄んだ夜空に名月が皓々と輝く\n心情: 清かな静けさと感慨"},
    {"theme": "冬の寂寥",       "season": "冬", "kigo": "雪",
     "plan": "季語: 雪\n季節: 冬\n情景: 音もなく雪が降り積もる夜更け\n心情: 静かな孤独と受容"},
]


async def compose_once(case: dict, dynamic: bool) -> dict:
    """compose を 1 回実行し、最終 season/kigo を返す (production と同一パース)。"""
    plan_constraint = prompts.build_plan_constraint(case["season"], case["kigo"])
    msgs = prompts.build_compose_messages(
        case["theme"], case["plan"],
        plan_constraint=plan_constraint, rag_block="", long_term_block="",
        season_hint=case["season"], dynamic_fewshot=dynamic,
    )
    raw = ""
    try:
        async for delta in llm.stream_completion(msgs):
            raw += delta
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "season": None, "kigo": None, "line1": None}
    _, text = llm.split_harmony(raw)
    parsed = validator.parse_tanka_json(text)
    if isinstance(parsed, tuple):
        return {"ok": False, "error": f"parse: {parsed[1]}", "season": None, "kigo": None, "line1": None}
    return {"ok": True, "error": None, "season": parsed.season, "kigo": parsed.kigo,
            "line1": parsed.lines[0].body if parsed.lines else None}


async def main():
    records = []
    OUT.parent.mkdir(exist_ok=True)
    print(f"REPS={REPS}  themes={len(CASES)}  calls={REPS*len(CASES)*2}", flush=True)
    for rep in range(1, REPS + 1):
        for case in CASES:
            for dynamic in (False, True):           # OFF, ON をペアで隣接
                r = await compose_once(case, dynamic)
                drift = r["ok"] and r["season"] != case["season"]
                to_spring = r["ok"] and r["season"] == "春"
                rec = {"rep": rep, "theme": case["theme"], "expect": case["season"],
                       "cond": "ON" if dynamic else "OFF", **r,
                       "drift": drift, "to_spring": to_spring}
                records.append(rec)
                OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                mark = "✓" if r["ok"] else "✗"
                tag = "🌸春!" if to_spring else ("→" + str(r["season"]) if drift else "OK")
                print(f"  rep{rep} {case['theme'][:8]:8} {rec['cond']:3} {mark} "
                      f"season={r['season']} kigo={r['kigo']} {tag}  {r.get('error') or ''}", flush=True)

    # 集計
    def rate(cond):
        rows = [r for r in records if r["cond"] == cond]
        ok = [r for r in rows if r["ok"]]
        spring = [r for r in ok if r["to_spring"]]
        drift = [r for r in ok if r["drift"]]
        return len(rows), len(ok), len(drift), len(spring)
    print("\n===== 集計 (compose 単離) =====", flush=True)
    print(f"{'cond':5} {'計':>3} {'parse成功':>8} {'季ドリフト':>10} {'→春':>6}", flush=True)
    for cond in ("OFF", "ON"):
        n, ok, drift, spring = rate(cond)
        print(f"{cond:5} {n:>3} {ok:>8} {drift:>10} {spring:>6}", flush=True)
    print(f"\n結果: {OUT}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
