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

# 起動時の既定モデル (env LM_STUDIO_MODEL)。後方互換のため MODEL の名前も残すが、
# 現在値は runtime 切替可能 (#15) なので参照は get_model() を使うこと。
DEFAULT_MODEL = config.MODEL
MODEL = DEFAULT_MODEL

_current_model: str = DEFAULT_MODEL


def get_model() -> str:
    """現在の生成モデル。タスク開始時にスナップショットして使う (途中切替の影響を受けない)。"""
    return _current_model


def set_model(model: str) -> None:
    """生成モデルを runtime 切替する (POST /api/model)。永続化は呼び出し側 (main.py) の責務。"""
    global _current_model
    if model != _current_model:
        log.info("model switched: %s -> %s", _current_model, model)
    _current_model = model


def effective_model(session: dict | None) -> str:
    """セッションの実効モデルを解決する (#20): session.model > グローバル現在値。
    セッション作成時にモデルを固定したセッションはそれを使い、未固定 (legacy 含む) は
    グローバル切替に追従する。タスク作成時に一度だけ解決し、スナップショットとして渡すこと。"""
    return (session or {}).get("model") or get_model()


def is_chat_model(model_id: str) -> bool:
    """embedding 系モデルを除外する素朴なフィルタ。
    LM Studio の /v1/models は能力フラグを返さないため名前で判定する。"""
    return "embed" not in model_id.lower()


def list_available_models() -> list[str]:
    """LM Studio がロード/ダウンロード済みのモデル id 一覧 (ヘルスチェックと同じ経路)。"""
    return [m.id for m in client.models.list().data]

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


async def stream_completion(messages: list[dict], temperature: float = 0.3,
                            model: str | None = None,
                            max_tokens: int | None = None) -> AsyncIterator[str]:
    """LM Studio に投げて生のテキスト delta を yield する (async)。
    model 省略時は現在のモデル (get_model())。長いタスクは開始時にスナップショットを渡すこと。
    max_tokens を指定すると completion を打ち切る (#22 thinking 暴走対策)。
    打ち切りは例外ではなく正常終了 (finish_reason=length) なので呼び出し側の特別処理は不要。"""
    extra: dict = {"max_tokens": max_tokens} if max_tokens else {}
    stream = await async_client.chat.completions.create(
        model=model or _current_model,
        messages=messages,
        temperature=temperature,
        stream=True,
        **extra,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta
