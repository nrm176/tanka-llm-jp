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
import llm
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

async def _run_chat(task_id: str, session_id: str, user_message: str, mode: str,
                    model: str | None = None) -> None:
    """user メッセージは既に DB に書かれている前提。
    DB から履歴を再構築 → LLM ストリーム → 最終結果を DB に保存。"""
    log.info("chat task started: task=%s session=%s mode=%s", task_id, session_id, mode)
    raw = ""
    try:
        sess = db.get_session(session_id) or {}
        history_db = sess.get("messages", [])
        api_messages = _db_messages_to_api(history_db)

        async for event in tanka.chat_stream(api_messages, mode=mode, model=model):
            etype = event.get("type")
            if etype == "chunk":
                raw += event.get("text", "")
            await _emit(task_id, event)

        # complete イベントが出たあと: 最終 assistant message を保存
        thinking, answer = llm.split_harmony(raw)
        db.append_message(session_id, {
            "kind": "assistant",
            "content": answer,
            "thinking": thinking,
        })
        await _finalize(task_id, "completed")

    except asyncio.CancelledError:
        # 中断時は途中経過を partial として保存
        thinking, answer = llm.split_harmony(raw)
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

# 永続化する各フェーズ raw (thinking 込み全文) の上限。thinking モデルの 1 フェーズは
# 通常数 KB だが、暴走時にセッションドキュメント (Mongo 16MB 上限) を圧迫しないための保険。
# 末尾側を切り捨てる: 思考の冒頭を残すほうが「なぜこう詠んだか」の追跡に役立ち、
# answer 部分は phase_end.text / complete イベント経由で別途残るため失われない。
PHASE_RAW_CAP = 20_000


def apply_event_to_state(state: dict[str, Any], event: dict[str, Any]) -> None:
    """パイプラインイベントを final_state に反映する純粋な reducer (副作用なし)。

    DB 記録 (record_failure) や SSE 送信は副作用なので呼び出し側 (_run_tanka) が行う。
    終了系イベント (complete / max_refines_reached / plateau_reached) は best_score 等の
    永続化に直結するため、ここで漏れなく state へ落とす。**plateau と max_refines は対称に扱う**
    (過去、max_refines 経路だけ best_score/score_history を捨てており事後分析を妨げた)。"""
    etype = event.get("type")
    if etype == "rag":
        state["rag_examples"] = event.get("examples", [])
    elif etype == "phase_end":
        # 生成過程 (thinking 込み raw) を永続化し、セッション再訪時に再生できるようにする。
        # chunk は蓄積しない (raw に全文が載っているため)。
        raw = event.get("raw") or ""
        if len(raw) > PHASE_RAW_CAP:
            raw = raw[:PHASE_RAW_CAP] + "\n…(長いため省略)"
        state["phases"].append({
            "phase": event.get("phase"),
            "attempt": event.get("attempt"),
            "raw": raw,
        })
    elif etype == "complete":
        state["plan"] = event.get("plan")
        state["tanka"] = event.get("tanka")
        state["moras"] = event.get("moras", [])
        state["kigo"] = event.get("kigo")
        state["season"] = event.get("season")
        state["image"] = event.get("image")
        state["emotion"] = event.get("emotion")
        state["final_score"] = event.get("score")
        state["model"] = event.get("model")
    elif etype == "validation":
        state["validations"].append({
            "attempt": event.get("attempt"),
            "score": event.get("score"),
            "errors": event.get("errors", []),
            "warnings": event.get("warnings", []),
            "violations": event.get("violations", []),
            "resolved": event.get("resolved", False),
        })
    elif etype == "max_refines_reached":
        state["max_refines_reached"] = True
        state["best_score"] = event.get("best_score")
        state["score_history"] = event.get("history", [])
    elif etype == "plateau_reached":
        state["plateau_reached"] = True
        state["best_score"] = event.get("best_score")
        state["score_history"] = event.get("history", [])
    elif etype == "llm_error":
        state["llm_error"] = event.get("message")
        state["llm_error_recovered"] = event.get("recovered", False)


async def _run_tanka(task_id: str, session_id: str, theme: str, max_refines: int,
                     model: str | None = None, manual_plan: dict | None = None) -> None:
    """tanka:お題 のユーザーメッセージは既に DB に書かれている前提。
    パイプライン実行 → 完成短歌を DB に保存。"""
    log.info("tanka task started: task=%s theme=%s", task_id, theme)
    final_state: dict[str, Any] = {
        "kind": "tanka",
        "theme": theme,
        "plan": None,
        "tanka": None,
        "moras": [],
        "phases": [],  # 生成過程 (phase 毎の thinking 込み raw)。再訪時の再生用
        "validations": [],
        "max_refines_reached": False,
        "plateau_reached": False,
        "best_score": None,
        "score_history": [],
        # validator 由来の構造化フィールド
        "kigo": None,
        "season": None,
        "image": None,
        "emotion": None,
        "final_score": None,
        "model": None,       # 生成に使ったモデル (#15)
        "rag_examples": [],  # RAG で参照した古典作例
    }
    try:
        async for event in tanka.generate_tanka_pipeline(theme, max_refines=max_refines, model=model, manual_plan=manual_plan):
            etype = event.get("type")
            apply_event_to_state(final_state, event)

            # 長期失敗記憶への記録 (resolved == False の validation のみ。副作用なので reducer の外)
            if etype == "validation" and not event.get("resolved"):
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
                        model=event.get("model"),
                    )
                except Exception as e:
                    log.warning("record_failure failed: %s", e)

            # フロント送信時は内部用フィールドを落とす (raw_output / raw は重い)。
            # phase_end.raw はライブ UI には不要 (chunk から組み立て済み) で永続化専用。
            if etype == "validation":
                client_event = {k: v for k, v in event.items() if k not in ("raw_output", "parsed_tanka")}
                await _emit(task_id, client_event)
            elif etype == "phase_end":
                client_event = {k: v for k, v in event.items() if k != "raw"}
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


def start_chat(task_id: str, session_id: str, user_message: str, mode: str,
               model: str | None = None) -> asyncio.Task:
    """model はセッション実効モデル (#20)。main.py がタスク作成時に解決して渡す。"""
    return _register(task_id, _run_chat(task_id, session_id, user_message, mode, model=model))


def start_tanka(task_id: str, session_id: str, theme: str, max_refines: int,
                model: str | None = None, manual_plan: dict | None = None) -> asyncio.Task:
    """model はセッション実効モデル (#20)。main.py がタスク作成時に解決して渡す。
    manual_plan を渡すと LLM Plan フェーズをスキップする (手動構想モード)。"""
    return _register(task_id, _run_tanka(task_id, session_id, theme, max_refines,
                                         model=model, manual_plan=manual_plan))
