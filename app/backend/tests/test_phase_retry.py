"""plan / compose の LLM エラーに対する 1 回リトライ (#67) のテスト。

背景: refine / self_critique には best-so-far / 初稿へのフォールバックがあるが、plan / compose には
無く、LM Studio の一過性エラー (paired A/B run 1 で compose 中の 500 を 98 生成中 1 件観測) で
タスク全体が失敗していた。ここでは ①正常時は素通し ②1 回目失敗→2 回目成功で回復 ③2 回失敗で raise
④context 超過は再試行しない ⑤reducer が回数を永続化 ⑥パイプライン統合 を固定する。
"""

from __future__ import annotations

import asyncio
import json

import config
import db
import llm
import rag
import tanka
import tasks


def _flaky_phase(fail_times: int, error: Exception | None = None, calls: list | None = None):
    """最初の fail_times 回は途中で例外、その後は正常に phase_start → chunk → phase_end を yield する stub。"""
    error = error or RuntimeError("Engine protocol predict stream returned an error: 500")
    state = {"n": 0}

    def stub(phase, messages, *, attempt=None, model=None, rescue_json=True):
        async def gen():
            state["n"] += 1
            if calls is not None:
                calls.append(state["n"])
            yield {"type": "phase_start", "phase": phase}
            yield {"type": "chunk", "phase": phase, "text": "途中"}
            if state["n"] <= fail_times:
                raise error
            yield {"type": "phase_end", "phase": phase, "text": f"ok#{state['n']}", "raw": "r",
                   "duration_seconds": 0.0}
        return gen()
    return stub


def _drain(monkeypatch, stub, **kw):
    monkeypatch.setattr(tanka, "_run_llm_phase", stub)
    monkeypatch.setattr(tanka, "PHASE_RETRY_DELAY", 0.0)

    async def run():
        return [ev async for ev in tanka._run_llm_phase_with_retry("compose", [], **kw)]
    return asyncio.run(run())


# ─── ラッパ単体 ───

def test_success_passes_through_without_retry_event(monkeypatch):
    calls: list = []
    evs = _drain(monkeypatch, _flaky_phase(0, calls=calls))
    assert [e["type"] for e in evs] == ["phase_start", "chunk", "phase_end"]
    assert evs[-1]["text"] == "ok#1"
    assert calls == [1]


def test_one_failure_then_success_recovers(monkeypatch):
    calls: list = []
    evs = _drain(monkeypatch, _flaky_phase(1, calls=calls))
    types = [e["type"] for e in evs]
    # 失敗した 1 回目の phase_start/chunk は既に流れている → phase_retry → 2 回目が完走
    assert types == ["phase_start", "chunk", "phase_retry", "phase_start", "chunk", "phase_end"]
    retry = evs[2]
    assert retry["phase"] == "compose" and retry["retry"] == 1 and "500" in retry["message"]
    assert evs[-1]["text"] == "ok#2"  # 最終テキストは再試行側
    assert calls == [1, 2]


def test_two_failures_raise(monkeypatch):
    calls: list = []
    try:
        _drain(monkeypatch, _flaky_phase(2, calls=calls))
        raise AssertionError("2 回失敗しても raise しなかった")
    except RuntimeError as e:
        assert "500" in str(e)
    assert calls == [1, 2]  # 再試行は 1 回だけ


def test_context_error_is_not_retried(monkeypatch):
    # context 超過は再試行しても同じ結果 → 即 raise (無駄な LLM 呼び出しをしない)
    calls: list = []
    err = RuntimeError("Context size has been exceeded.")
    assert llm.is_context_error(err)
    try:
        _drain(monkeypatch, _flaky_phase(1, error=err, calls=calls))
        raise AssertionError("context 超過で raise しなかった")
    except RuntimeError:
        pass
    assert calls == [1]  # 再試行なし


def test_retries_param_is_honored(monkeypatch):
    calls: list = []
    evs = _drain(monkeypatch, _flaky_phase(2, calls=calls), retries=2)
    assert evs[-1]["text"] == "ok#3"
    assert sum(1 for e in evs if e["type"] == "phase_retry") == 2
    assert calls == [1, 2, 3]


# ─── reducer ───

def test_reducer_persists_phase_retries():
    s = {"phases": [], "validations": [], "max_refines_reached": False, "plateau_reached": False,
         "best_score": None, "score_history": [], "rag_examples": [], "lessons": []}
    tasks.apply_event_to_state(s, {"type": "phase_retry", "phase": "compose", "message": "500", "retry": 1})
    tasks.apply_event_to_state(s, {"type": "phase_retry", "phase": "plan", "message": "timeout", "retry": 1})
    assert s["phase_retries"] == [{"phase": "compose", "message": "500"}, {"phase": "plan", "message": "timeout"}]


def test_reducer_without_retry_has_no_key():
    # 再試行が無かった生成に空リストを生やさない (旧データとの形を揃える)
    s = {"phases": [], "validations": [], "max_refines_reached": False, "plateau_reached": False,
         "best_score": None, "score_history": [], "rag_examples": [], "lessons": []}
    tasks.apply_event_to_state(s, {"type": "phase_start", "phase": "plan"})
    assert "phase_retries" not in s


# ─── パイプライン統合: compose が 1 回落ちても短歌が完成する ───

_TANKA_JSON = json.dumps({
    "kigo": "蛍", "season": "夏",
    "lines": [{"body": "夏の夜に", "reading": "なつのよるに"}, {"body": "蛍ひとつが", "reading": "ほたるひとつが"},
              {"body": "川辺行く", "reading": "かわべゆく"}, {"body": "光の跡を", "reading": "ひかりのあとを"},
              {"body": "静かに残す", "reading": "しずかにのこす"}],
    "image": "川辺を蛍が飛ぶ", "emotion": "静かな余韻",
}, ensure_ascii=False)


def test_pipeline_survives_one_compose_failure(monkeypatch):
    state = {"compose_calls": 0}

    def stub(phase, messages, *, attempt=None, model=None, rescue_json=True):
        async def gen():
            yield {"type": "phase_start", "phase": phase}
            if phase == "compose":
                state["compose_calls"] += 1
                if state["compose_calls"] == 1:
                    raise RuntimeError("Engine protocol predict stream returned an error: 500")
            text = "季語: 蛍\n季節: 夏\n情景: 川辺\n心情: 余韻" if phase == "plan" else _TANKA_JSON
            yield {"type": "phase_end", "phase": phase, "text": text, "raw": text, "duration_seconds": 0.0}
        return gen()

    monkeypatch.setattr(tanka, "_run_llm_phase", stub)
    monkeypatch.setattr(tanka, "PHASE_RETRY_DELAY", 0.0)
    monkeypatch.setattr(db, "recent_failures", lambda **k: [])
    monkeypatch.setattr(rag, "RAG_ENABLED", False)
    monkeypatch.setattr(config, "SELF_CRITIQUE_ENABLED", False)

    async def run():
        return [ev async for ev in tanka.generate_tanka_pipeline("夏の川", max_refines=0, model="m")]
    evs = asyncio.run(run())
    types = [e["type"] for e in evs]
    assert "phase_retry" in types
    complete = [e for e in evs if e["type"] == "complete"][0]
    assert complete["tanka"].startswith("夏の夜に") and complete["kigo"] == "蛍"
    assert state["compose_calls"] == 2
