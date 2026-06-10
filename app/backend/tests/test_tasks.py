"""tasks.apply_event_to_state (パイプラインイベント→state の純粋 reducer) のテスト。

副作用 (DB 記録 / SSE 送信) は呼び出し側にあるため、ここは LLM/DB 不要で回る。
最重要: max_refines_reached と plateau_reached が **対称に** best_score/score_history を
永続化すること (issue #1 の回帰テスト)。"""

from __future__ import annotations

import tasks


def _base_state() -> dict:
    return {
        "validations": [],
        "max_refines_reached": False,
        "plateau_reached": False,
        "best_score": None,
        "score_history": [],
        "rag_examples": [],
    }


def test_max_refines_persists_best_score_and_history():
    # 回帰 (issue #1): 以前は max_refines 経路で best_score/score_history を捨てていた
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "max_refines_reached", "best_score": 47, "history": [0, 44, 17, 47]})
    assert s["max_refines_reached"] is True
    assert s["best_score"] == 47
    assert s["score_history"] == [0, 44, 17, 47]


def test_plateau_persists_best_score_and_history():
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "plateau_reached", "best_score": 72, "history": [50, 60, 72]})
    assert s["plateau_reached"] is True
    assert s["best_score"] == 72
    assert s["score_history"] == [50, 60, 72]


def test_max_refines_and_plateau_are_symmetric():
    # 両終了経路が同じキーを populate する (非対称バグの再発防止)
    a, b = _base_state(), _base_state()
    tasks.apply_event_to_state(a, {"type": "max_refines_reached", "best_score": 30, "history": [30]})
    tasks.apply_event_to_state(b, {"type": "plateau_reached", "best_score": 30, "history": [30]})
    assert (a["best_score"], a["score_history"]) == (30, [30])
    assert (b["best_score"], b["score_history"]) == (30, [30])


def test_missing_history_defaults_to_empty():
    # history キーが無いイベントでも壊れない
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "max_refines_reached", "best_score": 10})
    assert s["best_score"] == 10
    assert s["score_history"] == []


def test_complete_populates_result_fields():
    s = _base_state()
    s.update({"plan": None, "tanka": None, "moras": [], "kigo": None, "season": None,
              "image": None, "emotion": None, "final_score": None})
    ev = {"type": "complete", "plan": "p", "tanka": "a\nb", "moras": [5, 7],
          "kigo": "蛍", "season": "夏", "image": "i", "emotion": "e", "score": 88}
    tasks.apply_event_to_state(s, ev)
    assert s["tanka"] == "a\nb"
    assert (s["kigo"], s["season"], s["final_score"]) == ("蛍", "夏", 88)


def test_validation_appends_entry():
    s = _base_state()
    ev = {"type": "validation", "attempt": 1, "score": 44, "errors": [], "warnings": [],
          "violations": [{"rule": "x"}], "resolved": False}
    tasks.apply_event_to_state(s, ev)
    assert len(s["validations"]) == 1
    assert s["validations"][0]["attempt"] == 1
    assert s["validations"][0]["score"] == 44
    assert s["validations"][0]["resolved"] is False


def test_rag_event_sets_examples():
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "rag", "examples": [{"text": "x"}]})
    assert s["rag_examples"] == [{"text": "x"}]
