"""構想下書き (#63 Phase 1): POST /api/plan → kind=plan タスク → result に情景候補・心情・警告。

対象:
- prompts.build_plan_draft_messages (純関数): 季語固定 / 季節のみ / 指定なし の 3 形態と候補数
- validator.parse_plan_draft_json / finalize_plan_draft (純関数): JSON 解析、季重なり警告、
  季語固定の上書き、辞書由来の季節確定
- tanka.plan_draft_stream: stream_completion をスタブし、イベント列・再試行・失敗時の例外
- tasks._run_plan: 結果がタスク文書へ保存されること (db / bus をスタブ)
- POST /api/plan: 422 (辞書外季語 / 季節不整合)、session 自動作成、start_plan への引き渡し
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import db
import llm
import main
import prompts
import tanka
import tasks
import validator


def _blob(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


# ── prompts ──

def test_plan_draft_prompt_keeps_system_static():
    msgs = prompts.build_plan_draft_messages("初秋の風", kigo="萩", season="秋", n_candidates=3)
    assert msgs[0] == {"role": "system", "content": prompts.TANKA_SYSTEM_PROMPT}
    assert msgs[-1]["role"] == "user"


def test_plan_draft_prompt_fixes_given_kigo():
    blob = _blob(prompts.build_plan_draft_messages("初秋の風", kigo="萩", season="秋", n_candidates=3))
    assert "初秋の風" in blob
    assert "季語: 萩" in blob
    assert "変更しない" in blob or "変えない" in blob
    # 出力スキーマのキーを明示している
    for key in ("kigo", "season", "image_candidates", "emotion", "background"):
        assert f'"{key}"' in blob


def test_plan_draft_prompt_season_only_asks_llm_to_pick_kigo():
    blob = _blob(prompts.build_plan_draft_messages("夕暮れ", kigo=None, season="夏", n_candidates=2))
    assert "季語: " not in blob          # 季語は固定しない
    assert "夏" in blob and "選" in blob  # 夏の季語を選ばせる


def test_plan_draft_prompt_candidate_count():
    blob2 = _blob(prompts.build_plan_draft_messages("月見", n_candidates=2))
    blob4 = _blob(prompts.build_plan_draft_messages("月見", n_candidates=4))
    assert "2 案" in blob2 or "2案" in blob2
    assert "4 案" in blob4 or "4案" in blob4


# ── validator: JSON 解析 ──

def _draft_json(**over) -> str:
    base = {
        "kigo": "蝉", "season": "夏",
        "image_candidates": ["夕立あがりの路地に蝉の声が戻る", "縁側で蝉しぐれを聞きながら団扇を止める"],
        "emotion": "去りゆく夏への静かな惜別", "background": "夏の午後、雨上がり。",
    }
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


def test_parse_plan_draft_json_ok_with_fence():
    d = validator.parse_plan_draft_json("```json\n" + _draft_json() + "\n```")
    assert isinstance(d, validator.PlanDraft)
    assert d.kigo == "蝉" and len(d.image_candidates) == 2


def test_parse_plan_draft_json_schema_error_is_tuple():
    r = validator.parse_plan_draft_json(_draft_json(image_candidates=[]))
    assert isinstance(r, tuple) and r[0] is None
    assert "image_candidates" in r[1]


def test_parse_plan_draft_json_no_object():
    r = validator.parse_plan_draft_json("構想は…")
    assert isinstance(r, tuple) and r[0] is None


# ── validator: 確定 + 警告 ──

def test_finalize_warns_other_kigo_in_candidate():
    d = validator.parse_plan_draft_json(_draft_json(
        image_candidates=["月の下で蝉が鳴く", "縁側で蝉しぐれ"]))
    final, warnings = validator.finalize_plan_draft(d, fixed_kigo="蝉")
    others = [w for w in warnings if w["type"] == "other_kigo"]
    assert others == [{"type": "other_kigo", "candidate": 0, "kigo": "月", "season": "秋"}]


def test_finalize_ignores_declared_kigo_and_its_variants():
    # 宣言季語そのもの (蝉) と、それを含む長い辞書語 (蝉しぐれ) は季重なりではない
    d = validator.parse_plan_draft_json(_draft_json(image_candidates=["蝉しぐれの午後", "蝉の声"]))
    _, warnings = validator.finalize_plan_draft(d, fixed_kigo="蝉")
    assert [w for w in warnings if w["type"] == "other_kigo"] == []


def test_finalize_overrides_llm_kigo_when_fixed():
    d = validator.parse_plan_draft_json(_draft_json(kigo="蛍", season="夏"))
    final, warnings = validator.finalize_plan_draft(d, fixed_kigo="蝉")
    assert final.kigo == "蝉"
    assert any(w["type"] == "kigo_overridden" and w["kigo"] == "蛍" for w in warnings)


def test_finalize_season_comes_from_dictionary():
    # LLM が季節を間違えても辞書が正 (萩は秋)
    d = validator.parse_plan_draft_json(_draft_json(kigo="萩", season="夏", image_candidates=["萩の影"]))
    final, warnings = validator.finalize_plan_draft(d)
    assert final.season == "秋"
    assert any(w["type"] == "season_corrected" for w in warnings)


def test_finalize_warns_kigo_not_in_dictionary():
    d = validator.parse_plan_draft_json(_draft_json(kigo="宇宙船", image_candidates=["宇宙船が飛ぶ"]))
    final, warnings = validator.finalize_plan_draft(d)
    assert final.kigo == "宇宙船"  # 上書きはしない (人がレビューで直す)
    assert any(w["type"] == "kigo_not_in_dictionary" for w in warnings)


# ── tanka.plan_draft_stream ──

def _stub_stream_seq(outputs: list[str], captured: dict | None = None):
    """呼び出しごとに outputs を順に返す stream_completion スタブ。"""
    calls = {"n": 0}

    async def stub(messages, temperature=0.3, model=None, max_tokens=None, meta=None):
        i = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        if captured is not None:
            captured.setdefault("messages", []).append(messages)
            captured["model"] = model
        yield outputs[i]
    return stub, calls


def _drain(agen):
    async def run():
        return [ev async for ev in agen]
    return asyncio.run(run())


def test_plan_draft_stream_emits_phase_events_and_complete(monkeypatch):
    captured: dict = {}
    stub, calls = _stub_stream_seq([_draft_json(image_candidates=["月の下で蝉が鳴く"])], captured)
    monkeypatch.setattr(llm, "stream_completion", stub)

    events = _drain(tanka.plan_draft_stream("夏の夕暮れ", kigo="蝉", season="夏", n_candidates=1, model="m-x"))
    types = [e["type"] for e in events]
    assert types[:2] == ["phase_start", "chunk"]
    assert "phase_end" in types
    done = events[-1]
    assert done["type"] == "complete"
    assert done["kigo"] == "蝉" and done["season"] == "夏"
    assert done["image_candidates"] == ["月の下で蝉が鳴く"]
    assert done["warnings"][0]["type"] == "other_kigo"
    assert done["attempts"] == 1
    assert done["model"] == "m-x"
    assert captured["model"] == "m-x"
    assert calls["n"] == 1


def test_plan_draft_stream_retries_once_with_schema_critique(monkeypatch):
    captured: dict = {}
    stub, calls = _stub_stream_seq(["これは JSON ではありません", _draft_json()], captured)
    monkeypatch.setattr(llm, "stream_completion", stub)

    events = _drain(tanka.plan_draft_stream("夏の夕暮れ", kigo="蝉", season="夏"))
    assert events[-1]["type"] == "complete"
    assert events[-1]["attempts"] == 2
    assert calls["n"] == 2
    # 2 回目は 1 回目の出力 + schema critique を足して投げる (固定ベース + 直近 1 ラウンド)
    second = captured["messages"][1]
    assert second[-2]["role"] == "assistant" and "JSON ではありません" in second[-2]["content"]
    assert second[-1]["role"] == "user" and "JSON" in second[-1]["content"]


def test_plan_draft_stream_raises_after_max_attempts(monkeypatch):
    stub, calls = _stub_stream_seq(["だめ", "まだだめ"])
    monkeypatch.setattr(llm, "stream_completion", stub)
    with pytest.raises(tanka.PlanDraftError):
        _drain(tanka.plan_draft_stream("夏の夕暮れ", kigo="蝉", season="夏", max_attempts=2))
    assert calls["n"] == 2


# ── tasks._run_plan ──

@pytest.fixture
def task_stub(monkeypatch):
    rec: dict = {"update": [], "events": []}
    monkeypatch.setattr(db, "update_task", lambda tid, **kw: rec["update"].append((tid, kw)))

    async def add_event(tid, ev):
        rec["events"].append(ev)

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(tasks.q, "add_event", add_event)
    monkeypatch.setattr(tasks.q, "expire_stream", noop)
    return rec


def test_run_plan_persists_result(task_stub, monkeypatch):
    async def fake_stream(theme, **kw):
        yield {"type": "phase_start", "phase": "plan_draft", "attempt": 0}
        yield {"type": "phase_end", "phase": "plan_draft", "attempt": 0, "text": "{}", "raw": "…"}
        yield {"type": "complete", "kigo": "蝉", "season": "夏", "image_candidates": ["a"],
               "emotion": "e", "background": "b", "warnings": [], "attempts": 1, "model": "m"}
    monkeypatch.setattr(tanka, "plan_draft_stream", fake_stream)

    asyncio.run(tasks._run_plan("t1", "s1", "夏の夕暮れ", kigo="蝉", season="夏", n_candidates=1, model="m"))
    tid, kw = task_stub["update"][-1]
    assert (tid, kw["status"]) == ("t1", "completed")
    assert kw["result"]["kigo"] == "蝉"
    assert kw["result"]["image_candidates"] == ["a"]
    assert "type" not in kw["result"]
    # SSE には phase イベントと complete と done が流れる
    types = [e["type"] for e in task_stub["events"]]
    assert types == ["phase_start", "phase_end", "complete", "done"]


def test_run_plan_failure_marks_task_failed(task_stub, monkeypatch):
    async def fake_stream(theme, **kw):
        yield {"type": "phase_start", "phase": "plan_draft", "attempt": 0}
        raise tanka.PlanDraftError("構想の JSON 解析に失敗")
    monkeypatch.setattr(tanka, "plan_draft_stream", fake_stream)

    asyncio.run(tasks._run_plan("t1", "s1", "夏", kigo=None, season=None, n_candidates=3, model="m"))
    tid, kw = task_stub["update"][-1]
    assert kw["status"] == "failed"
    assert "JSON" in kw["error"]
    assert [e["type"] for e in task_stub["events"]][-2:] == ["error", "done"]


# ── POST /api/plan ──

@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def plan_stub(monkeypatch):
    rec: dict = {"created_sessions": 0, "tasks": [], "started": []}
    monkeypatch.setattr(db, "get_session",
                        lambda sid: {"id": sid, "messages": []} if sid == "existing" else None)

    def create_session(title=None, model=None):
        rec["created_sessions"] += 1
        return {"id": "auto-sid", "title": "新規セッション", "messages": []}
    monkeypatch.setattr(db, "create_session", create_session)
    monkeypatch.setattr(db, "find_active_task", lambda sid: None)

    def create_task(sid, kind, input_data=None):
        rec["tasks"].append((sid, kind, input_data))
        return {"id": "task-1"}
    monkeypatch.setattr(db, "create_task", create_task)
    monkeypatch.setattr(llm, "effective_model", lambda sess: "m")
    monkeypatch.setattr(tasks, "start_plan", lambda *a, **k: rec["started"].append((a, k)))
    return rec


def test_post_plan_rejects_kigo_not_in_dictionary(client, plan_stub):
    res = client.post("/api/plan", json={"theme": "夏", "kigo": "宇宙船"})
    assert res.status_code == 422
    assert "宇宙船" in res.json()["detail"]
    assert plan_stub["started"] == []


def test_post_plan_rejects_season_mismatch(client, plan_stub):
    res = client.post("/api/plan", json={"theme": "夏", "kigo": "萩", "season": "夏"})
    assert res.status_code == 422
    assert "秋" in res.json()["detail"]


def test_post_plan_creates_session_and_starts_task(client, plan_stub):
    res = client.post("/api/plan", json={"theme": "初秋の風", "kigo": "萩", "n_candidates": 2})
    assert res.status_code == 200
    body = res.json()
    assert body == {"task_id": "task-1", "session_id": "auto-sid", "kind": "plan"}
    assert plan_stub["created_sessions"] == 1
    sid, kind, input_data = plan_stub["tasks"][0]
    assert (sid, kind) == ("auto-sid", "plan")
    # 季節は辞書から確定して記録・引き渡し
    assert input_data["season"] == "秋" and input_data["kigo"] == "萩" and input_data["n_candidates"] == 2
    args, kwargs = plan_stub["started"][0]
    assert args[:3] == ("task-1", "auto-sid", "初秋の風")
    assert kwargs["kigo"] == "萩" and kwargs["season"] == "秋" and kwargs["n_candidates"] == 2 and kwargs["model"] == "m"


def test_post_plan_with_existing_session_and_no_kigo(client, plan_stub):
    res = client.post("/api/plan", json={"session_id": "existing", "theme": "夕暮れ", "season": "夏"})
    assert res.status_code == 200
    assert res.json()["session_id"] == "existing"
    assert plan_stub["created_sessions"] == 0
    _, kwargs = plan_stub["started"][0]
    assert kwargs["kigo"] is None and kwargs["season"] == "夏"


def test_post_plan_unknown_session_404(client, plan_stub):
    res = client.post("/api/plan", json={"session_id": "ghost", "theme": "夏"})
    assert res.status_code == 404
