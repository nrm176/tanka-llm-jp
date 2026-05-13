"""Redis Streams を使ったタスク向けイベントブローカ。

設計:
- 各タスクに 1 ストリーム: `task:{task_id}:events`
- LLM ワーカーが各イベントを XADD で追加
- SSE エンドポイントは XREAD BLOCK で replay+ライブ受信
- ストリームは TTL 1 時間 (再接続猶予) と MAXLEN ≈ 5000 (暴走防止)

イベント形式は dict (str → str) として XADD する。
JSON 文字列にしてネストしない構造にする (XADD のフィールドはフラット):
  {"type": "chunk", "data": "<json-encoded payload>"}
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis, from_url

log = logging.getLogger("queue")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_client: Redis | None = None

# ストリーム保持期間。完了後ユーザーが reconnect する余裕。
STREAM_TTL_SECONDS = 3600
# 異常時の暴走防止。普通の chat でも数千チャンクあるので余裕を持って。
STREAM_MAXLEN = 10000


def _get() -> Redis:
    global _client
    if _client is None:
        log.info("Connecting to Redis at %s", REDIS_URL)
        _client = from_url(REDIS_URL, decode_responses=True)
    return _client


def _stream_key(task_id: str) -> str:
    return f"task:{task_id}:events"


async def add_event(task_id: str, event: dict[str, Any]) -> None:
    """イベントをタスクストリームに追加。"""
    payload = json.dumps(event, ensure_ascii=False)
    await _get().xadd(
        _stream_key(task_id),
        {"data": payload},
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )


async def expire_stream(task_id: str, seconds: int = STREAM_TTL_SECONDS) -> None:
    await _get().expire(_stream_key(task_id), seconds)


async def read_events(task_id: str, *, block_ms: int = 5000) -> AsyncIterator[dict[str, Any]]:
    """ストリームを最初から購読する非同期ジェネレータ。

    - replay: 既存のエントリを ID `0-0` から全て読む
    - live:  新規エントリを XREAD BLOCK で待ち受ける
    - 'done' イベントが流れてきたら終了 (生産側が必ず流す)

    block_ms はタイムアウト。タイムアウトしても永続的に再 XREAD する。
    呼び出し側 (SSE エンドポイント) は別途 task.status を確認して
    タスクが死んでいないかを見張る。
    """
    r = _get()
    key = _stream_key(task_id)
    last_id = "0"  # 最初から
    while True:
        result = await r.xread({key: last_id}, block=block_ms, count=200)
        if not result:
            # タイムアウト。生きているか確認する責務は呼び出し側に渡す。
            yield {"type": "_heartbeat"}
            continue
        for _stream_name, entries in result:
            for entry_id, fields in entries:
                last_id = entry_id
                payload = fields.get("data")
                if not payload:
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    log.warning("malformed event in stream %s: %r", key, payload)
                    continue
                yield event
                if event.get("type") == "done":
                    return


async def ping() -> bool:
    try:
        return await _get().ping()
    except Exception as e:
        log.warning("Redis ping failed: %s", e)
        return False


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
