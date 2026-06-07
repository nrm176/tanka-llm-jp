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

    # 最初の user メッセージが入った時点でセッションタイトルを自動設定する。
    # ただし **明示的に付けられたタイトルは上書きしない** (デフォルトの "新規セッション" のときだけ)。
    # これを怠ると eval.sh が付けた "[eval] <variant>" タイトルが最初の tanka: で潰れ、
    # 評価モニタが run を識別できなくなる (実際に踏んだバグ)。
    sess = _sessions().find_one({"_id": oid}, projection={"title": 1, "messages.kind": 1})
    if sess and sess.get("title") == "新規セッション":
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


# ─── メトリクス集計 (Phase 2) ───
# 全セッションの tanka メッセージを走査し、品質指標を計算する。
# 個人利用スケール (数百セッション) なので素朴な全件走査で十分。

def compute_metrics(*, session_id: str | None = None) -> dict[str, Any]:
    """tanka 生成の品質メトリクスを集計して返す。

    session_id を指定するとそのセッションのみ。None なら全セッション横断。
    評価ハーネス (eval.sh) は 1 セッション = 1 バリアントの実行に使い、
    session_id を渡してそのバリアントの集計を取り出す。"""
    query: dict[str, Any] = {}
    if session_id:
        try:
            query["_id"] = ObjectId(session_id)
        except Exception:
            return _empty_metrics()

    tanka_msgs: list[dict] = []
    for sess in _sessions().find(query, projection={"messages": 1}):
        for m in sess.get("messages", []):
            if m.get("kind") == "tanka":
                tanka_msgs.append(m)

    return _aggregate_tanka_metrics(tanka_msgs)


def _empty_metrics() -> dict[str, Any]:
    return {
        "total": 0,
        "first_attempt_pass_rate": None,
        "overall_pass_rate": None,
        "avg_attempts": None,
        "avg_final_score": None,
        "plateau_rate": None,
        "max_refines_rate": None,
        "score_buckets": {},
        "violation_frequency": {},
        "violation_by_severity": {},
    }


def _aggregate_tanka_metrics(tanka_msgs: list[dict]) -> dict[str, Any]:
    total = len(tanka_msgs)
    if total == 0:
        return _empty_metrics()

    first_pass = 0          # attempt 0 で resolved
    overall_pass = 0        # どこかの attempt で resolved (= final_score >= 80 相当)
    attempts_sum = 0
    score_sum = 0
    score_count = 0
    plateau_count = 0
    max_refines_count = 0
    score_buckets = {"0-39": 0, "40-59": 0, "60-79": 0, "80-89": 0, "90-100": 0}
    violation_freq: dict[str, int] = {}
    severity_freq: dict[str, int] = {"critical": 0, "major": 0, "minor": 0}

    for m in tanka_msgs:
        validations = m.get("validations") or []
        attempts_sum += len(validations)

        # first-attempt pass
        if validations and validations[0].get("resolved"):
            first_pass += 1
        # overall pass: いずれかの attempt で resolved
        if any(v.get("resolved") for v in validations):
            overall_pass += 1

        if m.get("plateau_reached"):
            plateau_count += 1
        if m.get("max_refines_reached"):
            max_refines_count += 1

        fs = m.get("final_score")
        if isinstance(fs, int):
            score_sum += fs
            score_count += 1
            if fs < 40:
                score_buckets["0-39"] += 1
            elif fs < 60:
                score_buckets["40-59"] += 1
            elif fs < 80:
                score_buckets["60-79"] += 1
            elif fs < 90:
                score_buckets["80-89"] += 1
            else:
                score_buckets["90-100"] += 1

        # 違反頻度: 全 attempt の全 violation を数える
        for v in validations:
            for viol in (v.get("violations") or []):
                rule = viol.get("rule", "unknown")
                violation_freq[rule] = violation_freq.get(rule, 0) + 1
                sev = viol.get("severity", "minor")
                if sev in severity_freq:
                    severity_freq[sev] += 1

    # 違反頻度を降順ソート (dict は挿入順を保持)
    violation_freq_sorted = dict(
        sorted(violation_freq.items(), key=lambda kv: -kv[1])
    )

    return {
        "total": total,
        "first_attempt_pass_rate": round(first_pass / total, 4),
        "overall_pass_rate": round(overall_pass / total, 4),
        "avg_attempts": round(attempts_sum / total, 3),
        "avg_final_score": round(score_sum / score_count, 2) if score_count else None,
        "plateau_rate": round(plateau_count / total, 4),
        "max_refines_rate": round(max_refines_count / total, 4),
        "score_buckets": score_buckets,
        "violation_frequency": violation_freq_sorted,
        "violation_by_severity": severity_freq,
    }


# ─── Eval Monitor (Phase 2 UI): eval/repeat セッションの一覧と進捗サマリ ───
# eval.sh は title="[eval] <variant> <ts>"、eval-repeat.sh は title="[repeat N] <slug>" で
# セッションを作る。それらを拾って、テーマ別スコア・進捗・実行中テーマを返す。

import re as _re

_EVAL_TITLE_RE = _re.compile(r"^\[(eval|repeat)([^\]]*)\]\s*(.*)$")


def list_eval_runs(limit: int = 50) -> list[dict]:
    """eval/repeat セッションを新しい順に、進捗サマリ付きで返す。"""
    cursor = (
        _sessions()
        .find({"title": {"$regex": r"^\[(eval|repeat)"}})
        .sort("updated_at", -1)
        .limit(limit)
    )
    runs: list[dict] = []
    for doc in cursor:
        sid = str(doc["_id"])
        title = doc.get("title", "")
        m = _EVAL_TITLE_RE.match(title)
        kind = m.group(1) if m else "eval"
        variant = (m.group(3) or "").strip() if m else title

        tankas = [msg for msg in doc.get("messages", []) if msg.get("kind") == "tanka"]
        per_theme = [
            {
                "theme": t.get("theme"),
                "score": t.get("final_score"),
                "attempts": len(t.get("validations") or []),
                "plateau": bool(t.get("plateau_reached")),
                "kigo": t.get("kigo"),
                "season": t.get("season"),
            }
            for t in tankas
        ]
        scores = [t["score"] for t in per_theme if isinstance(t["score"], int)]

        active = find_active_task(sid)
        running_theme = None
        if active:
            running_theme = (active.get("input") or {}).get("theme")

        runs.append({
            "id": sid,
            "title": title,
            "kind": kind,
            "variant": variant,
            "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
            "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
            "done": len(per_theme),
            "scores": scores,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "running_theme": running_theme,
            "status": "running" if active else "idle",
            "themes": per_theme,
        })
    return runs
