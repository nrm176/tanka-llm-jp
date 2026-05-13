"""バックグラウンドタスク (LLM 呼び出し本体) の実行と管理。

設計:
- HTTP リクエストとは独立した asyncio.Task として実行
- 各イベントは Redis Stream に流す → SSE エンドポイントが購読
- 完了時に最終結果を MongoDB に保存
- キャンセル要求は asyncio.Task.cancel() を呼ぶ → CancelledError を捕捉して partial save
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import db
import bus as q
import tanka

log = logging.getLogger("tasks")

# 実行中の asyncio.Task を task_id でひも付ける。キャンセル時に参照する。
_running: dict[str, asyncio.Task] = {}


def get_running(task_id: str) -> asyncio.Task | None:
    return _running.get(task_id)


def has_running(task_id: str) -> bool:
    t = _running.get(task_id)
    return t is not None and not t.done()


async def cancel_task(task_id: str) -> bool:
    t = _running.get(task_id)
    if t and not t.done():
        t.cancel()
        return True
    return False


def count_running() -> int:
    """まだ完了していない asyncio.Task の数。"""
    return sum(1 for t in _running.values() if not t.done())


async def cancel_all() -> int:
    """全タスクをキャンセル。CancelledError ハンドラ (partial save) は別途完了を待つ必要がある。
    呼び出し側は count_running() == 0 を polling して掃除完了を確認する。"""
    cancelled = 0
    for tid, t in list(_running.items()):
        if not t.done():
            t.cancel()
            cancelled += 1
    return cancelled


def _db_messages_to_api(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        kind = m.get("kind")
        if kind == "user":
            out.append({"role": "user", "content": m.get("content", "")})
        elif kind == "assistant":
            out.append({"role": "assistant", "content": m.get("content", "")})
        elif kind == "tanka" and m.get("tanka"):
            out.append({
                "role": "assistant",
                "content": f"(構想)\n{m.get('plan', '')}\n\n(短歌)\n{m.get('tanka', '')}",
            })
    return out


async def _emit(task_id: str, event: dict[str, Any]) -> None:
    """イベントを Redis Stream に追加。例外は飲み込む (タスクが死なないように)。"""
    try:
        await q.add_event(task_id, event)
    except Exception as e:
        log.warning("failed to emit event %s for task %s: %s", event.get("type"), task_id, e)


async def _finalize(task_id: str, status: str, error: str | None = None) -> None:
    """共通の終了処理: ステータス更新 + done イベント + ストリーム TTL。"""
    db.update_task(task_id, status=status, error=error)
    await _emit(task_id, {"type": "done", "status": status, **({"error": error} if error else {})})
    await q.expire_stream(task_id)


# ─── chat タスク ───

async def _run_chat(task_id: str, session_id: str, user_message: str, mode: str) -> None:
    """user メッセージは既に DB に書かれている前提。
    DB から履歴を再構築 → LLM ストリーム → 最終結果を DB に保存。"""
    log.info("chat task started: task=%s session=%s mode=%s", task_id, session_id, mode)
    raw = ""
    try:
        sess = db.get_session(session_id) or {}
        history_db = sess.get("messages", [])
        api_messages = _db_messages_to_api(history_db)

        async for event in tanka.chat_stream(api_messages, mode=mode):
            etype = event.get("type")
            if etype == "chunk":
                raw += event.get("text", "")
            await _emit(task_id, event)

        # complete イベントが出たあと: 最終 assistant message を保存
        thinking, answer = tanka.split_harmony(raw)
        db.append_message(session_id, {
            "kind": "assistant",
            "content": answer,
            "thinking": thinking,
        })
        await _finalize(task_id, "completed")

    except asyncio.CancelledError:
        # 中断時は途中経過を partial として保存
        thinking, answer = tanka.split_harmony(raw)
        partial = (answer or "").strip()
        if partial:
            db.append_message(session_id, {
                "kind": "assistant",
                "content": partial + "\n\n*(中断)*",
                "thinking": thinking,
                "cancelled": True,
            })
        await _emit(task_id, {"type": "cancelled"})
        await _finalize(task_id, "cancelled")
        raise
    except Exception as e:
        log.exception("chat task %s failed", task_id)
        await _emit(task_id, {"type": "error", "message": str(e)})
        await _finalize(task_id, "failed", error=str(e))


# ─── tanka タスク ───

async def _run_tanka(task_id: str, session_id: str, theme: str, max_refines: int) -> None:
    """tanka:お題 のユーザーメッセージは既に DB に書かれている前提。
    パイプライン実行 → 完成短歌を DB に保存。"""
    log.info("tanka task started: task=%s theme=%s", task_id, theme)
    final_state: dict[str, Any] = {
        "kind": "tanka",
        "theme": theme,
        "plan": None,
        "tanka": None,
        "moras": [],
        "validations": [],
        "max_refines_reached": False,
        # validator 由来の構造化フィールド
        "kigo": None,
        "season": None,
        "image": None,
        "emotion": None,
        "final_score": None,
    }
    try:
        async for event in tanka.generate_tanka_pipeline(theme, max_refines=max_refines):
            etype = event.get("type")
            if etype == "complete":
                final_state["plan"] = event.get("plan")
                final_state["tanka"] = event.get("tanka")
                final_state["moras"] = event.get("moras", [])
                final_state["kigo"] = event.get("kigo")
                final_state["season"] = event.get("season")
                final_state["image"] = event.get("image")
                final_state["emotion"] = event.get("emotion")
                final_state["final_score"] = event.get("score")
            elif etype == "validation":
                final_state["validations"].append({
                    "attempt": event.get("attempt"),
                    "score": event.get("score"),
                    "errors": event.get("errors", []),
                    "warnings": event.get("warnings", []),
                    "violations": event.get("violations", []),
                    "resolved": event.get("resolved", False),
                })
                # 長期失敗記憶への記録 (resolved == False の attempt のみ)
                if not event.get("resolved"):
                    try:
                        db.record_failure(
                            task_id=task_id,
                            session_id=session_id,
                            theme=theme,
                            attempt=event.get("attempt", 0),
                            raw_output=event.get("raw_output", ""),
                            parsed=event.get("parsed_tanka"),
                            score=event.get("score", 0),
                            violations=event.get("violations", []),
                        )
                    except Exception as e:
                        log.warning("record_failure failed: %s", e)
            elif etype == "max_refines_reached":
                final_state["max_refines_reached"] = True
            elif etype == "plateau_reached":
                final_state["plateau_reached"] = True
                final_state["best_score"] = event.get("best_score")
                final_state["score_history"] = event.get("history", [])

            # フロント送信時は内部用フィールドを落とす (raw_output は重い)
            if etype == "validation":
                client_event = {k: v for k, v in event.items() if k not in ("raw_output", "parsed_tanka")}
                await _emit(task_id, client_event)
            else:
                await _emit(task_id, event)

        if final_state["tanka"]:
            db.append_message(session_id, final_state)
        await _finalize(task_id, "completed")

    except asyncio.CancelledError:
        if final_state.get("tanka"):
            # complete まで到達していたなら保存。途中なら破棄。
            db.append_message(session_id, final_state)
        await _emit(task_id, {"type": "cancelled"})
        await _finalize(task_id, "cancelled")
        raise
    except Exception as e:
        log.exception("tanka task %s failed", task_id)
        await _emit(task_id, {"type": "error", "message": str(e)})
        await _finalize(task_id, "failed", error=str(e))


# ─── 起動エントリ (registry 管理) ───

def _register(task_id: str, coro):
    """coroutine を asyncio.Task として起動し、_running に登録。
    終了時に自動で登録解除する。"""
    async def wrapped():
        try:
            await coro
        finally:
            _running.pop(task_id, None)
    t = asyncio.create_task(wrapped())
    _running[task_id] = t
    return t


def start_chat(task_id: str, session_id: str, user_message: str, mode: str) -> asyncio.Task:
    return _register(task_id, _run_chat(task_id, session_id, user_message, mode))


def start_tanka(task_id: str, session_id: str, theme: str, max_refines: int) -> asyncio.Task:
    return _register(task_id, _run_tanka(task_id, session_id, theme, max_refines))
