"""MongoDB persistence layer for chat sessions.

各セッションは 1 ドキュメント。メッセージはその中に埋め込みで保持する。
（会話単位での fetch/delete が単純で、サイズも MongoDB の 16MB 制限まで余裕）"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

log = logging.getLogger("db")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "tanka_chat")

_client: MongoClient | None = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        log.info("Connecting to MongoDB at %s (db=%s)", MONGO_URL, MONGO_DB)
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _client


def _sessions() -> Collection:
    return _get_client()[MONGO_DB]["sessions"]


def _tasks() -> Collection:
    return _get_client()[MONGO_DB]["tasks"]


def _failures() -> Collection:
    return _get_client()[MONGO_DB]["failures"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: dict | None) -> dict | None:
    """Convert ObjectId / datetime to JSON-friendly forms."""
    if doc is None:
        return None
    out: dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, list):
            out[k] = [_serialize(x) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        else:
            out[k] = v
    return out


def ping() -> bool:
    """LM Studio スタイルのヘルスチェック。"""
    try:
        _get_client().admin.command("ping")
        return True
    except PyMongoError as e:
        log.warning("MongoDB ping failed: %s", e)
        return False


def create_session(title: str | None = None) -> dict:
    doc = {
        "title": title or "新規セッション",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }
    res = _sessions().insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize(doc)  # type: ignore[return-value]


def list_sessions(limit: int = 200) -> list[dict]:
    cursor = (
        _sessions()
        .find({}, projection={"messages": 0})  # メッセージは含めず軽量に
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [_serialize(d) for d in cursor]  # type: ignore[return-value]


def get_session(session_id: str) -> dict | None:
    try:
        oid = ObjectId(session_id)
    except Exception:
        return None
    doc = _sessions().find_one({"_id": oid})
    return _serialize(doc)


def append_message(session_id: str, message: dict) -> dict:
    """メッセージを追加し、updated_at と必要なら title を更新する。"""
    try:
        oid = ObjectId(session_id)
    except Exception:
        log.warning("invalid session_id: %s", session_id)
        return {}

    msg = {**message, "created_at": _now()}

    update: dict[str, Any] = {
        "$push": {"messages": msg},
        "$set": {"updated_at": _now()},
    }

    # 最初の user メッセージが入った時点でセッションタイトルを更新
    sess = _sessions().find_one({"_id": oid}, projection={"title": 1, "messages.kind": 1})
    if sess:
        existing_user = any(m.get("kind") == "user" for m in sess.get("messages", []))
        if not existing_user and msg.get("kind") == "user":
            content = (msg.get("content") or "").strip().replace("\n", " ")
            if content:
                update["$set"]["title"] = content[:60]

    _sessions().update_one({"_id": oid}, update)
    return _serialize(msg) or {}


def update_session_title(session_id: str, title: str) -> None:
    try:
        oid = ObjectId(session_id)
    except Exception:
        return
    title = (title or "").strip()[:120] or "新規セッション"
    _sessions().update_one(
        {"_id": oid},
        {"$set": {"title": title, "updated_at": _now()}},
    )


def delete_session(session_id: str) -> bool:
    try:
        oid = ObjectId(session_id)
    except Exception:
        return False
    res = _sessions().delete_one({"_id": oid})
    if res.deleted_count > 0:
        # セッション削除時に紐付くタスクも掃除
        _tasks().delete_many({"session_id": session_id})
        return True
    return False


# ─── tasks コレクション ───
# 1 つの LLM 呼び出し (chat / tanka) を表す軽量な状態レコード。
# ステータスは "running" | "completed" | "failed" | "cancelled"。
# in-flight な状態 (チャンクなど) は MongoDB に持たず、Redis Stream に流す。

def create_task(session_id: str, kind: str, input_data: dict | None = None) -> dict:
    doc = {
        "session_id": session_id,
        "kind": kind,
        "status": "running",
        "input": input_data or {},
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    res = _tasks().insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize(doc)  # type: ignore[return-value]


def get_task(task_id: str) -> dict | None:
    try:
        oid = ObjectId(task_id)
    except Exception:
        return None
    return _serialize(_tasks().find_one({"_id": oid}))


def update_task(task_id: str, *, status: str | None = None, error: str | None = None) -> None:
    try:
        oid = ObjectId(task_id)
    except Exception:
        return
    update: dict[str, Any] = {"updated_at": _now()}
    if status is not None:
        update["status"] = status
    if error is not None:
        update["error"] = error
    _tasks().update_one({"_id": oid}, {"$set": update})


def find_active_task(session_id: str) -> dict | None:
    """そのセッションでまだ走っているタスクがあれば返す。"""
    return _serialize(_tasks().find_one(
        {"session_id": session_id, "status": "running"},
        sort=[("created_at", -1)],
    ))


# ─── failures コレクション (長期失敗記憶) ───
# 検証で不合格になった生成物を蓄積し、将来の生成 prompt に anti-example として注入する。
# サイズ暴走を避けるため、書き込み毎に古いものを pruning する (簡易 LRU)。

FAILURE_MAX = 1000


def record_failure(
    *,
    task_id: str | None,
    session_id: str | None,
    theme: str,
    attempt: int,
    raw_output: str,
    parsed: dict | None,
    score: int,
    violations: list[dict],
) -> None:
    doc = {
        "task_id": task_id,
        "session_id": session_id,
        "theme": theme,
        "attempt": attempt,
        "raw_output": (raw_output or "")[:5000],  # サイズキャップ
        "parsed": parsed,
        "score": score,
        "violations": violations,
        "ts": _now(),
    }
    _failures().insert_one(doc)
    _prune_failures()


def _prune_failures(max_count: int = FAILURE_MAX) -> None:
    """新しい順に max_count 件残し、それ以上古いものを削除。"""
    coll = _failures()
    total = coll.estimated_document_count()
    if total <= max_count:
        return
    # 最新 max_count 件以外を削除
    threshold = list(coll.find({}, {"_id": 1, "ts": 1}).sort("ts", -1).skip(max_count).limit(1))
    if not threshold:
        return
    cutoff_ts = threshold[0]["ts"]
    coll.delete_many({"ts": {"$lte": cutoff_ts}})


def recent_failures(*, limit: int = 5, season: str | None = None) -> list[dict]:
    """直近の失敗を取得。season を指定すると同季のもののみ返す。"""
    q: dict[str, Any] = {}
    if season:
        q["parsed.season"] = season
    cursor = _failures().find(q).sort("ts", -1).limit(limit)
    return [_serialize(d) for d in cursor]  # type: ignore[return-value]


def list_failures(*, limit: int = 100) -> list[dict]:
    cursor = _failures().find().sort("ts", -1).limit(limit)
    return [_serialize(d) for d in cursor]  # type: ignore[return-value]


def clear_failures() -> int:
    return _failures().delete_many({}).deleted_count


def count_failures() -> int:
    return _failures().estimated_document_count()


def fail_orphaned_tasks() -> int:
    """バックエンド起動時に呼ぶ。前回の実行で 'running' のままだった
    タスクは復活できないので 'failed' に倒す。
    戻り値はクリーンアップしたタスク数 (ログ用)。"""
    res = _tasks().update_many(
        {"status": "running"},
        {"$set": {
            "status": "failed",
            "error": "orphaned (backend restarted)",
            "updated_at": _now(),
        }},
    )
    return res.modified_count
