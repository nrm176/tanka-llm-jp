"""LM Studio との通信層 (OpenAI 互換クライアント)。

クライアント生成・timeout 設定・ストリーミング・harmony フォーマット処理・
コンテキスト超過エラー判定をまとめる。パイプライン (tanka.py) はここを通じてのみ LLM を呼ぶ。"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI, OpenAI

import config

log = logging.getLogger("llm")

LM_STUDIO_URL = config.LM_STUDIO_URL
MODEL = config.MODEL

_TIMEOUT = httpx.Timeout(
    connect=config.LLM_CONNECT_TIMEOUT,
    read=config.LLM_READ_TIMEOUT,
    write=config.LLM_CONNECT_TIMEOUT,
    pool=config.LLM_CONNECT_TIMEOUT,
)

# sync client: ヘルスチェック用 (models.list)
client = OpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio", timeout=_TIMEOUT)
# async client: ストリーミング用。timeout でサイレントハングを防ぐ。
async_client = AsyncOpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio", timeout=_TIMEOUT)


# ── ハーモニーフォーマット (思考トレースの leak) 処理 ──
HARMONY_FINAL_MARKER = "<|channel|>final<|message|>"
SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]+\|>")


def split_harmony(content: str) -> tuple[str | None, str]:
    """harmony 形式の出力を (思考, 最終回答) に分離する。マーカーが無ければ思考 None。"""
    if HARMONY_FINAL_MARKER not in content:
        return None, content.strip()
    reasoning, _, answer = content.partition(HARMONY_FINAL_MARKER)
    reasoning = SPECIAL_TOKEN_RE.sub("", reasoning).strip()
    answer = SPECIAL_TOKEN_RE.sub("", answer).strip()
    return reasoning or None, answer


def is_context_error(exc: Exception) -> bool:
    """LM Studio / OpenAI 互換 API のコンテキスト超過エラーを判定する。
    文言がプロバイダにより異なるため広めに拾う。"""
    msg = str(exc).lower()
    return any(s in msg for s in (
        "context size", "context length", "context window",
        "maximum context", "too many tokens", "exceed",
    ))


async def stream_completion(messages: list[dict], temperature: float = 0.3) -> AsyncIterator[str]:
    """LM Studio に投げて生のテキスト delta を yield する (async)。"""
    stream = await async_client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta
