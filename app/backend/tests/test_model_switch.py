"""モデル runtime 切替 (#15) の純関数テスト (LLM/DB 不要)。

切替の心臓部は 3 つ:
1. llm.get_model/set_model — runtime 状態の roundtrip
2. パイプラインのモデルスナップショット — complete/validation イベントに model が載り、
   reducer (tasks.apply_event_to_state) が state へ落とす
3. 失敗記憶のモデル分離 — _load_long_term_failures が db.recent_failures に model を渡す
"""

from __future__ import annotations

import llm
import tanka
import tasks


# ── llm: runtime モデル状態 ──

def test_get_set_model_roundtrip():
    original = llm.get_model()
    try:
        llm.set_model("test-model-x")
        assert llm.get_model() == "test-model-x"
    finally:
        llm.set_model(original)  # 他テストへ漏らさない
    assert llm.get_model() == original


def test_default_model_is_initial_current():
    # 起動直後 (set_model 前) の現在値は env 既定。このテストは roundtrip 後でも成立する
    assert llm.DEFAULT_MODEL == llm.MODEL


def test_is_chat_model_filters_embeddings():
    assert llm.is_chat_model("llm-jp-4-8b-thinking")
    assert llm.is_chat_model("google/gemma-4-12b")
    assert not llm.is_chat_model("text-embedding-nomic-embed-text-v1.5")
    assert not llm.is_chat_model("Nomic-Embed-v2")  # 大文字でも除外


# ── reducer: complete イベントの model が state に落ちる ──

def test_complete_event_carries_model_into_state():
    s = {"validations": [], "model": None}
    tasks.apply_event_to_state(s, {"type": "complete", "tanka": "x", "score": 90, "model": "m1"})
    assert s["model"] == "m1"


def test_complete_event_builder_includes_model():
    ev = tanka._complete_event("plan", None, 0, "fallback", model="m2")
    assert ev["model"] == "m2"
    assert ev["type"] == "complete"


# ── 失敗記憶のモデル分離: db 呼び出しに model が伝播する ──

class _StubDB:
    def __init__(self):
        self.calls = []

    def recent_failures(self, *, limit, season=None, model=None):
        self.calls.append({"limit": limit, "season": season, "model": model})
        return []


def test_long_term_failures_filtered_by_model():
    stub = _StubDB()
    tanka._load_long_term_failures(stub, "夏", model="m3")
    # season 指定 → グローバル fallback の両方とも model が付く
    assert len(stub.calls) == 2
    assert all(c["model"] == "m3" for c in stub.calls)
    assert stub.calls[0]["season"] == "夏"
    assert stub.calls[1]["season"] is None
