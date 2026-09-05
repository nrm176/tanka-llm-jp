"""thinking 暴走対策 (#22) のテスト (LLM/DB 不要)。

1. 生成フェーズが config.MAX_COMPLETION_TOKENS を stream_completion に渡すこと
2. 0 (無効) のとき None が渡り従来挙動になること
3. system prompt に reading 自己検証の抑止文言が入っていること
"""

from __future__ import annotations

import asyncio

import config
import llm
import prompts
import tanka


def _stub_stream(captured):
    async def stub(messages, temperature=0.3, model=None, max_tokens=None, meta=None):
        captured["model"] = model
        captured["max_tokens"] = max_tokens
        yield '{"ok": true}'
    return stub


def _drain_phase(**kwargs):
    async def run():
        return [ev async for ev in tanka._run_llm_phase("plan", [], **kwargs)]
    return asyncio.run(run())


def test_default_cap_is_enabled():
    # 既定 8192 = 安全弁: A/B で 4096 は正常長考に課税 (合格率 -17pt) と判明したため、
    # 観測分布の censored tail (真の暴走) だけを切る位置に置く
    assert config.MAX_COMPLETION_TOKENS == 8192


def test_phase_passes_cap_to_llm(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(captured))
    monkeypatch.setattr(config, "MAX_COMPLETION_TOKENS", 1234)
    events = _drain_phase(model="m-x")
    assert captured["max_tokens"] == 1234
    assert captured["model"] == "m-x"
    assert events[-1]["type"] == "phase_end"


def test_cap_zero_disables(monkeypatch):
    # TANKA_MAX_COMPLETION_TOKENS=0 → max_tokens=None (無制限・従来挙動)
    captured = {}
    monkeypatch.setattr(llm, "stream_completion", _stub_stream(captured))
    monkeypatch.setattr(config, "MAX_COMPLETION_TOKENS", 0)
    _drain_phase()
    assert captured["max_tokens"] is None


def test_system_prompt_suppresses_self_verification():
    # 空白スパイラル (実トレースの ~4 割) のトリガー除去文言
    assert "自己検証は不要" in prompts.TANKA_SYSTEM_PROMPT
    assert "空白・改行・句読点を含めない" in prompts.TANKA_SYSTEM_PROMPT


# ─── chat モードの completion 上限 (#54) ───

def _stub_chat_stream(captured, deltas, separated=False):
    async def stub(messages, temperature=0.3, model=None, max_tokens=None, meta=None):
        captured["max_tokens"] = max_tokens
        for d in deltas:
            yield d
        if meta is not None:
            meta["reasoning_separated"] = separated
    return stub


def _drain_chat():
    async def run():
        return [ev async for ev in tanka.chat_stream([{"role": "user", "content": "hi"}])]
    return asyncio.run(run())


def test_chat_passes_cap_to_llm(monkeypatch):
    # 詩歌の質問は chat でも thinking 暴走を誘発する (実測 39k 字) → chat にも上限を渡す
    captured = {}
    monkeypatch.setattr(llm, "stream_completion", _stub_chat_stream(captured, ["こんにちは"]))
    monkeypatch.setattr(config, "CHAT_MAX_COMPLETION_TOKENS", 777)
    events = _drain_chat()
    assert captured["max_tokens"] == 777
    assert events[-1] == {"type": "complete", "thinking": None, "answer": "こんにちは"}


def test_chat_cap_zero_disables(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm, "stream_completion", _stub_chat_stream(captured, ["x"]))
    monkeypatch.setattr(config, "CHAT_MAX_COMPLETION_TOKENS", 0)
    _drain_chat()
    assert captured["max_tokens"] is None


def test_chat_default_cap_matches_pipeline_cap():
    assert config.CHAT_MAX_COMPLETION_TOKENS == config.MAX_COMPLETION_TOKENS == 8192


def test_chat_thinking_only_truncation_yields_notice(monkeypatch):
    # 分離型で content が一度も来ずに終わった (上限到達で思考のみ) → 思考は thinking に残し、
    # answer は案内文。思考全文を回答バブルに流し込まない
    monkeypatch.setattr(llm, "stream_completion",
                        _stub_chat_stream({}, ["拍数を数え直す…", "また数え直す…"], separated=True))
    end = _drain_chat()[-1]
    assert end["type"] == "complete"
    assert end["thinking"] == "拍数を数え直す…また数え直す…"
    assert end["answer"] == tanka.CHAT_TRUNCATED_NOTICE


def test_chat_separated_with_answer_is_split_normally(monkeypatch):
    # 分離型でも content が来ていれば従来どおり (思考, 回答) に分離される
    monkeypatch.setattr(llm, "stream_completion",
                        _stub_chat_stream({}, ["思考", llm.HARMONY_FINAL_MARKER, "回答"], separated=True))
    end = _drain_chat()[-1]
    assert (end["thinking"], end["answer"]) == ("思考", "回答")


def test_chat_non_separated_without_marker_unchanged(monkeypatch):
    # 非分離ストリーム (マーカー無し = 全文が回答) は一切変えない
    monkeypatch.setattr(llm, "stream_completion", _stub_chat_stream({}, ["ただの回答"], separated=False))
    end = _drain_chat()[-1]
    assert (end["thinking"], end["answer"]) == (None, "ただの回答")
