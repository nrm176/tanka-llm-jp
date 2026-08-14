"""tasks.apply_event_to_state (パイプラインイベント→state の純粋 reducer) のテスト。

副作用 (DB 記録 / SSE 送信) は呼び出し側にあるため、ここは LLM/DB 不要で回る。
最重要: max_refines_reached と plateau_reached が **対称に** best_score/score_history を
永続化すること (issue #1 の回帰テスト)。"""

from __future__ import annotations

import tasks


def _base_state() -> dict:
    return {
        "phases": [],
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


def test_phase_end_accumulates_generation_process():
    # 生成過程 (thinking 込み raw) はセッション再訪時の再生用に phase 毎へ永続化する (issue #16)。
    # chunk / phase_start は蓄積しない (raw に全文が載るため)
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "phase_start", "phase": "plan"})
    tasks.apply_event_to_state(s, {"type": "chunk", "phase": "plan", "text": "ignored"})
    tasks.apply_event_to_state(
        s, {"type": "phase_end", "phase": "plan", "text": "answer",
            "raw": "thinking…<|channel|>final<|message|>answer"}
    )
    tasks.apply_event_to_state(
        s, {"type": "phase_end", "phase": "refine", "attempt": 2, "text": "a2", "raw": "r2"}
    )
    assert [p["phase"] for p in s["phases"]] == ["plan", "refine"]
    assert s["phases"][0]["raw"].startswith("thinking…")
    assert s["phases"][0]["attempt"] is None
    assert s["phases"][1]["attempt"] == 2


def test_phase_end_raw_is_capped():
    s = _base_state()
    tasks.apply_event_to_state(
        s, {"type": "phase_end", "phase": "compose", "text": "t", "raw": "x" * (tasks.PHASE_RAW_CAP + 500)}
    )
    raw = s["phases"][0]["raw"]
    assert len(raw) <= tasks.PHASE_RAW_CAP + 20  # キャップ + 省略マーカー分
    assert raw.endswith("…(長いため省略)")


def test_phase_end_missing_raw_defaults_to_empty():
    # 旧バージョンのイベント (raw なし) でも壊れない
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "phase_end", "phase": "plan", "text": "t"})
    assert s["phases"][0]["raw"] == ""


def test_phase_end_carries_duration_seconds():
    # フェーズ所要秒 (#27) を phases エントリに永続化する
    s = _base_state()
    tasks.apply_event_to_state(
        s, {"type": "phase_end", "phase": "compose", "text": "t", "raw": "r",
            "duration_seconds": 42.5}
    )
    assert s["phases"][0]["duration_seconds"] == 42.5


def test_phase_end_missing_duration_defaults_to_none():
    # 手動 Plan (LLM 呼び出しなし) や旧イベントは duration を持たない
    s = _base_state()
    tasks.apply_event_to_state(s, {"type": "phase_end", "phase": "plan", "text": "t"})
    assert s["phases"][0]["duration_seconds"] is None
