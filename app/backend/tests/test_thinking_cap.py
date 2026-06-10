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
    async def stub(messages, temperature=0.3, model=None, max_tokens=None):
        captured["model"] = model
        captured["max_tokens"] = max_tokens
        yield '{"ok": true}'
    return stub


def _drain_phase(**kwargs):
    async def run():
        return [ev async for ev in tanka._run_llm_phase("plan", [], **kwargs)]
    return asyncio.run(run())


def test_default_cap_is_enabled():
    # 既定 4096: 実測分布 (p50=387 / p90≈10k / p95≈20k chars) の censored tail を切る位置
    assert config.MAX_COMPLETION_TOKENS == 4096


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
