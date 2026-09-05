"""API 単体利用 (#61): GET /api/tasks/{tid} の結果取得と POST /api/tanka の session_id 任意化。

エンドポイントは FastAPI TestClient で叩き、副作用境界 (db / tasks.start_tanka / llm) は
monkeypatch で差し替える。TestClient を `with` で使わないので lifespan は走らない
(Mongo/Redis 不要)。結果整形 (tasks.tanka_result_from_state) は純関数として直接テストする。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import db
import llm
import main
import tasks


@pytest.fixture
def client():
    return TestClient(main.app)


# ── 純関数: final_state → API result ──

def _state(**over) -> dict:
    base = {
        "kind": "tanka", "theme": "初秋の風", "plan": "季語: 秋風\n季節: 秋",
        "tanka": "a\nb\nc\nd\ne", "moras": [5, 7, 5, 7, 7],
        "kigo": "秋風", "season": "秋", "image": "img", "emotion": "emo",
        "final_score": 97, "model": "llm-jp-4-8b-thinking",
        "validations": [{"resolved": False}, {"resolved": True}],
        "plateau_reached": True, "max_refines_reached": False,
        "duration_seconds": 156.7,
        "phases": [{"raw": "x" * 100}], "rag_examples": [], "lessons": [],
    }
    base.update(over)
    return base


def test_tanka_result_mirrors_complete_event_fields():
    # complete イベント + 一覧レコードと同じ語彙 (score は final_score の別名、attempts は validation 数)
    r = tasks.tanka_result_from_state(_state())
    assert r == {
        "theme": "初秋の風", "tanka": "a\nb\nc\nd\ne", "plan": "季語: 秋風\n季節: 秋",
        "moras": [5, 7, 5, 7, 7], "kigo": "秋風", "season": "秋",
        "image": "img", "emotion": "emo", "score": 97, "model": "llm-jp-4-8b-thinking",
        "attempts": 2, "plateau_reached": True, "max_refines_reached": False,
        "duration_seconds": 156.7,
    }


def test_tanka_result_excludes_heavy_fields():
    # phases (thinking raw) / validations はセッションメッセージ側に残す。タスク文書は軽量に保つ
    r = tasks.tanka_result_from_state(_state())
    assert "phases" not in r
    assert "validations" not in r


def test_tanka_result_is_none_without_tanka():
    # complete 未到達 (schema_invalid 連発で fallback も無い) → 結果なし
    assert tasks.tanka_result_from_state(_state(tanka=None)) is None


# ── _finalize が result をタスク文書へ保存する ──

def test_finalize_persists_result(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(db, "update_task", lambda tid, **kw: calls.update(kw))

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(tasks.q, "add_event", noop)
    monkeypatch.setattr(tasks.q, "expire_stream", noop)

    asyncio.run(tasks._finalize("t1", "completed", result={"tanka": "x"}))
    assert calls["status"] == "completed"
    assert calls["result"] == {"tanka": "x"}


# ── GET /api/tasks/{tid} ──

def test_get_task_404_when_unknown(client, monkeypatch):
    monkeypatch.setattr(db, "get_task", lambda tid: None)
    res = client.get("/api/tasks/nope")
    assert res.status_code == 404
    assert res.json()["detail"] == "task not found"


def test_get_task_returns_status_and_result(client, monkeypatch):
    doc = {"id": "t1", "kind": "tanka", "session_id": "s1", "status": "completed",
           "input": {"theme": "月見"}, "error": None,
           "result": {"tanka": "…", "score": 97}}
    monkeypatch.setattr(db, "get_task", lambda tid: dict(doc) if tid == "t1" else None)
    body = client.get("/api/tasks/t1").json()
    assert body["id"] == "t1"
    assert body["status"] == "completed"
    assert body["result"] == {"tanka": "…", "score": 97}


def test_get_task_running_has_null_result(client, monkeypatch):
    # 実行中 / 旧タスク文書には result キー自体が無い → API は null に正規化して返す
    doc = {"id": "t1", "kind": "tanka", "session_id": "s1", "status": "running",
           "input": {}, "error": None}
    monkeypatch.setattr(db, "get_task", lambda tid: dict(doc))
    body = client.get("/api/tasks/t1").json()
    assert body["status"] == "running"
    assert body["result"] is None


# ── POST /api/tanka: session_id 任意化 ──

@pytest.fixture
def tanka_stub(monkeypatch):
    """POST /api/tanka の副作用境界を記録用スタブに差し替える。"""
    rec: dict = {"created_sessions": [], "messages": [], "tasks": [], "started": []}

    monkeypatch.setattr(db, "get_session",
                        lambda sid: {"id": sid, "messages": []} if sid == "existing" else None)

    def create_session(title=None, model=None):
        rec["created_sessions"].append({"title": title, "model": model})
        return {"id": "auto-sid", "title": "新規セッション", "messages": []}
    monkeypatch.setattr(db, "create_session", create_session)
    monkeypatch.setattr(db, "find_active_task", lambda sid: None)
    monkeypatch.setattr(db, "append_message", lambda sid, m: rec["messages"].append((sid, m)))

    def create_task(sid, kind, input_data=None):
        rec["tasks"].append((sid, kind, input_data))
        return {"id": "task-1"}
    monkeypatch.setattr(db, "create_task", create_task)
    monkeypatch.setattr(llm, "effective_model", lambda sess: "m")
    monkeypatch.setattr(tasks, "start_tanka", lambda *a, **k: rec["started"].append((a, k)))
    return rec


def test_post_tanka_without_session_creates_one(client, tanka_stub):
    res = client.post("/api/tanka", json={"theme": "月見"})
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "auto-sid"
    assert body["task_id"] == "task-1"
    assert len(tanka_stub["created_sessions"]) == 1
    # 以降の副作用 (user メッセージ / task 文書 / 起動) がすべて新セッションに紐づく
    assert tanka_stub["messages"][0][0] == "auto-sid"
    assert tanka_stub["tasks"][0][0] == "auto-sid"
    assert tanka_stub["started"][0][0][1] == "auto-sid"


def test_post_tanka_with_existing_session_does_not_create(client, tanka_stub):
    # 回帰ガード: 従来どおり session_id 指定は既存セッションを使う
    res = client.post("/api/tanka", json={"session_id": "existing", "theme": "月見"})
    assert res.status_code == 200
    assert res.json()["session_id"] == "existing"
    assert tanka_stub["created_sessions"] == []


def test_post_tanka_unknown_session_still_404(client, tanka_stub):
    # 回帰ガード: 明示した session_id が存在しないときは作らずに 404
    res = client.post("/api/tanka", json={"session_id": "ghost", "theme": "月見"})
    assert res.status_code == 404
    assert tanka_stub["created_sessions"] == []
