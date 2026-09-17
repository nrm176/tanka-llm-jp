"""run 2 の全 attempt を、mora_count_disputed の救済条件 (#76) の新旧で再採点する。

尺度を変えるルール変更なので、生成 eval でなく既存出力の再採点で効果を測る (skill Step 1 特例)。
「旧」は救済条件を無条件 True にモンキーパッチして再現する。

使い方:
  cd app && docker compose exec -T mongo mongosh --quiet tanka_chat --eval '
    const ids = ["6aa2bdb4dcbfff96fb3a5110","6aa2bdb4dcbfff96fb3a510f"];   // run 2 の sc-off / sc-on
    const out = [];
    ids.forEach(id => { const s = db.sessions.findOne({_id: ObjectId(id)});
      s.messages.filter(m=>m.kind==="tanka").forEach(m => out.push({arm: s.title.includes("sc-on")?"sc-on":"sc-off",
        theme: m.theme, plan: m.plan, phases: m.phases.map(p=>({phase:p.phase, attempt:p.attempt, raw:p.raw})),
        validations: m.validations.map(v=>({attempt:v.attempt, score:v.score}))})); });
    print(JSON.stringify(out));' > /path/to/run2_full.json
  cd backend && uv run python eval/experiments/self-critique-paired/rescore-disputed.py /path/to/run2_full.json
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
logging.disable(logging.CRITICAL)
import llm  # noqa: E402
import validator  # noqa: E402

msgs = json.load(open(sys.argv[1], encoding="utf-8"))
real_plausible = validator._reading_plausible


def parse(raw):
    """pipeline と同じ順で JSON を取り出す: 回答部 → 全文 → 思考末尾からの救済 (#30)。"""
    think, ans = llm.split_harmony(raw)
    for cand in (ans, raw, llm.rescue_json_from_text(think or "") if think else None):
        if not cand:
            continue
        t = validator.parse_tanka_json(cand)
        if not isinstance(t, tuple):
            return t
    return None


def score(t, plan, theme, rule):
    validator._reading_plausible = real_plausible if rule == "new" else (lambda b, m, c: (True, ""))
    r = validator.evaluate(t, expected_season=validator.extract_season_from_plan(plan),
                           expected_kigo=validator.extract_kigo_from_plan(plan), theme=theme)
    return r.score, [v.rule for v in r.violations]


mismatch, changed_best = [], []
n_att = 0
tot = {"old": 0, "new": 0}
disp = {"old": 0, "new": 0}
fdisp = {"old": 0, "new": 0}
fall = {"off_by_one": 0, "mora_count": 0}
for m in msgs:
    stored = {v["attempt"]: v["score"] for v in m["validations"]}
    attempts = {}
    for p in m["phases"]:
        t = parse(p["raw"])
        if p["phase"] == "compose":
            attempts[0] = t
        elif p["phase"] == "self_critique" and t is not None:  # parse 不能なら compose 出力のまま (pipeline と同じ)
            attempts[0] = t
        elif p["phase"] == "refine":
            attempts[p["attempt"]] = t
    res = {}
    for a, t in attempts.items():
        if t is None:
            res[a] = (0, 0, [], [])
            continue
        so, vo = score(t, m["plan"], m["theme"], "old")
        sn, vn = score(t, m["plan"], m["theme"], "new")
        res[a] = (so, sn, vo, vn)
        n_att += 1
        if a in stored and stored[a] != so:
            mismatch.append((m["theme"], a, stored[a], so))
        disp["old"] += vo.count("mora_count_disputed")
        disp["new"] += vn.count("mora_count_disputed")
        fall["off_by_one"] += vn.count("mora_count_off_by_one") - vo.count("mora_count_off_by_one")
        fall["mora_count"] += vn.count("mora_count") - vo.count("mora_count")
    bo = max(res, key=lambda a: (res[a][0], -a))
    bn = max(res, key=lambda a: (res[a][1], -a))
    tot["old"] += res[bo][0]
    tot["new"] += res[bn][1]
    fdisp["old"] += "mora_count_disputed" in res[bo][2]
    fdisp["new"] += "mora_count_disputed" in res[bn][3]
    if bo != bn:
        changed_best.append((m["arm"], m["theme"], bo, bn))

n = len(msgs)
print(f"再採点 {n_att} attempt / {n} 首。旧ルール再現の不一致 {len(mismatch)} 件 (kogo_yomi 辞書更新の影響分を除き 0 のはず):")
for x in mismatch:
    print("   ", x)
print(f"全 attempt の disputed: 旧 {disp['old']} → 新 {disp['new']}"
      f"  (棄却 {disp['old'] - disp['new']}: off_by_one へ {fall['off_by_one']}、mora_count へ {fall['mora_count']})")
print(f"最終短歌に disputed が付く数 /{n}: 旧 {fdisp['old']} → 新 {fdisp['new']}")
print(f"最終短歌の平均: 旧 {tot['old'] / n:.2f} → 新 {tot['new'] / n:.2f} (Δ {(tot['new'] - tot['old']) / n:+.2f})")
print(f"best-of-N の選択が変わった題: {len(changed_best)} / {n}", changed_best)
