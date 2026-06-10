"""FastAPI アプリ。

エンドポイント:
- GET    /api/health               LM Studio + MongoDB + Redis のヘルス
- GET    /api/sessions             セッション一覧 (active_task_id 付き)
- POST   /api/sessions             新規セッション作成 (model 指定でセッション固定 #20)
- GET    /api/sessions/{sid}       セッション全体 (メッセージ込み)
- PATCH  /api/sessions/{sid}       タイトル更新
- DELETE /api/sessions/{sid}       削除
- GET    /api/sessions/{sid}/active-task   進行中タスクがあれば {task_id, kind} を返す

- POST   /api/chat                 チャットタスク作成 → {task_id} を即返す (LLM はバックグラウンド)
- POST   /api/tanka                短歌タスク作成 → {task_id}
- GET    /api/tanka/records        全セッション横断の短歌一覧 (新しい順)
- GET    /api/tasks/{tid}/stream   SSE: タスクのイベントストリーム (replay+ライブ)
- POST   /api/tasks/{tid}/cancel   実行中タスクのキャンセル

- GET    /api/models               {current, available[]} (LM Studio から、embedding 除外)
- POST   /api/model                生成モデルの runtime 切替 (新規タスクから有効、mongo に永続化)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import db
import bus as q
import llm
import tasks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("app")


SHUTDOWN_GRACE_SECONDS = 25  # docker stop_grace_period (30s) より少し短く取る


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── startup ──
    # 前回の再起動で取り残された "running" 状態のタスクを掃除
    n = db.fail_orphaned_tasks()
    if n > 0:
        log.info("marked %d orphaned tasks as failed on startup", n)

    # 永続化されたモデル設定を復元 (precedence: db settings > env LM_STUDIO_MODEL > 既定)。
    # 併せて #15 以前の failure レコードに model を backfill (env 既定モデル産が真)。
    try:
        db.backfill_failure_model(llm.DEFAULT_MODEL)
        saved_model = db.get_setting("model")
        if saved_model and saved_model != llm.get_model():
            llm.set_model(saved_model)
            log.info("restored model from settings: %s", saved_model)
    except Exception as e:
        log.warning("model setting restore failed (using default %s): %s", llm.DEFAULT_MODEL, e)

    yield

    # ── shutdown (graceful) ──
    # 走っているタスクをキャンセルし、各タスクの except asyncio.CancelledError ブロック
    # で partial save が走るのを待つ。タイムアウト超過分は db.fail_orphaned_tasks() で
    # 後始末するので、最悪ケースでも DB 状態は一貫する。
    cancelled = await tasks.cancel_all()
    if cancelled:
        log.info(
            "shutdown: cancelling %d running task(s); waiting up to %ds for cleanup",
            cancelled, SHUTDOWN_GRACE_SECONDS,
        )
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
        while tasks.count_running() > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        remaining = tasks.count_running()
        if remaining > 0:
            log.warning(
                "shutdown: %d task(s) did not finish cleanup within grace period; "
                "will be marked failed", remaining,
            )

    # cleanup handler が走らずに残ったものを failed に倒す (整合性保証)
    db.fail_orphaned_tasks()
    await q.close()
    log.info("shutdown complete")


app = FastAPI(title="Tanka Backend", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request models ───

class ChatRequest(BaseModel):
    session_id: str
    user_message: str
    mode: Literal["normal", "tanka"] = "normal"


class TankaRequest(BaseModel):
    session_id: str
    theme: str = Field(..., min_length=1)
    # None なら無制限 (plateau 検知のみ。最後の安全網は HARD_CAP=50)。
    # 数値を指定すると refine 回数の上限になる (旧来の固定回数挙動)。
    max_refines: int | None = Field(None, ge=0, le=500)


class CreateSessionRequest(BaseModel):
    title: str | None = None
    # セッション固定モデル (#20)。None ならグローバル現在値に追従
    model: str | None = Field(None, min_length=1, max_length=200)


class UpdateTitleRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ModelSwitchRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=200)


# ─── SSE helpers ───

def _format_sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


# ─── Health ───

@app.get("/api/health")
async def health() -> dict[str, Any]:
    out: dict[str, Any] = {
        "lm_studio_url": llm.LM_STUDIO_URL,
        "configured_model": llm.get_model(),
        "default_model": llm.DEFAULT_MODEL,
        "mongo_url": db.MONGO_URL,
        "mongo_db": db.MONGO_DB,
        "mongo_ok": db.ping(),
        "redis_url": q.REDIS_URL,
        "redis_ok": await q.ping(),
    }
    try:
        ids = llm.list_available_models()
        out["lm_studio_ok"] = True
        out["available_models"] = ids
        out["model_loaded"] = llm.get_model() in ids
    except Exception as e:
        log.warning("LM Studio health check failed: %s", e)
        out["lm_studio_ok"] = False
        out["lm_studio_error"] = str(e)

    out["status"] = "ok" if (out["mongo_ok"] and out["redis_ok"] and out.get("lm_studio_ok")) else "degraded"
    return out


# ─── Model selection (#15) ───

@app.get("/api/models")
async def get_models() -> dict[str, Any]:
    """切替可能なモデル一覧。LM Studio のダウンロード済みモデルから embedding 系を除外して返す。
    注意: LM Studio の /v1/models は「ダウンロード済み」であって「ロード済み」ではない。
    未ロードモデルへ切替えた場合、初回生成は JIT ロードで遅くなる (既定 context が小さい点にも注意)。"""
    try:
        ids = llm.list_available_models()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LM Studio unreachable: {e}")
    return {
        "current": llm.get_model(),
        "default": llm.DEFAULT_MODEL,
        "available": [i for i in ids if llm.is_chat_model(i)],
    }


def _validate_model_choice(model: str) -> None:
    """モデル指定の妥当性検証 (POST /api/model と POST /api/sessions で共通)。"""
    try:
        ids = llm.list_available_models()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LM Studio unreachable: {e}")
    if model not in ids:
        raise HTTPException(status_code=400, detail=f"model not available in LM Studio: {model}")
    if not llm.is_chat_model(model):
        raise HTTPException(status_code=400, detail=f"not a chat model: {model}")


@app.post("/api/model")
async def post_model(req: ModelSwitchRequest) -> dict[str, Any]:
    """生成モデルを runtime 切替する。実行中タスクは開始時のモデルで走り切り、新規タスクから有効。
    mongo settings に永続化され、再起動後も維持される (env より優先)。"""
    _validate_model_choice(req.model)

    previous = llm.get_model()
    llm.set_model(req.model)
    try:
        db.set_setting("model", req.model)
    except Exception as e:
        # 永続化失敗でも runtime 切替自体は有効のまま (再起動で戻る)。degraded を明示する
        log.warning("model setting persistence failed: %s", e)
        return {"current": req.model, "previous": previous, "persisted": False}
    return {"current": req.model, "previous": previous, "persisted": True}


# ─── Sessions CRUD ───

@app.get("/api/sessions")
async def get_sessions() -> list[dict]:
    sessions = db.list_sessions()
    # 各セッションについて active task があれば追加
    for s in sessions:
        active = db.find_active_task(s["id"])
        s["active_task"] = (
            {"task_id": active["id"], "kind": active.get("kind")}
            if active else None
        )
    return sessions


@app.post("/api/sessions")
async def post_session(req: CreateSessionRequest) -> dict:
    if req.model:
        _validate_model_choice(req.model)
    return db.create_session(title=req.title, model=req.model)


@app.get("/api/sessions/{sid}")
async def get_one_session(sid: str) -> dict:
    s = db.get_session(sid)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.patch("/api/sessions/{sid}")
async def patch_session(sid: str, req: UpdateTitleRequest) -> dict:
    db.update_session_title(sid, req.title)
    s = db.get_session(sid)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.delete("/api/sessions/{sid}")
async def delete_session_endpoint(sid: str) -> dict:
    # 進行中タスクがあれば先にキャンセル
    active = db.find_active_task(sid)
    if active:
        await tasks.cancel_task(active["id"])
    ok = db.delete_session(sid)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": sid}


@app.get("/api/sessions/{sid}/active-task")
async def get_active_task(sid: str) -> dict:
    active = db.find_active_task(sid)
    if not active:
        return {"task_id": None}
    return {"task_id": active["id"], "kind": active.get("kind")}


# ─── Task creation (LLM はバックグラウンド) ───

@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict:
    sess = db.get_session(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    # 同一セッションで既に走っている場合は拒否 (UI 側で防ぐが二重保険)
    if db.find_active_task(req.session_id):
        raise HTTPException(status_code=409, detail="another task is already running for this session")

    # セッション実効モデル (#20) をタスク作成時に解決 (session.model > グローバル現在値)
    model = llm.effective_model(sess)
    # ユーザーメッセージを即時保存
    db.append_message(req.session_id, {"kind": "user", "content": req.user_message})
    # タスクレコード作成 → asyncio.Task 起動
    task = db.create_task(req.session_id, kind="chat", input_data={"mode": req.mode, "model": model})
    tasks.start_chat(task["id"], req.session_id, req.user_message, req.mode, model=model)

    return {"task_id": task["id"], "session_id": req.session_id, "kind": "chat"}


@app.post("/api/tanka")
async def tanka_endpoint(req: TankaRequest) -> dict:
    sess = db.get_session(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    if db.find_active_task(req.session_id):
        raise HTTPException(status_code=409, detail="another task is already running for this session")

    # セッション実効モデル (#20) をタスク作成時に解決 (session.model > グローバル現在値)
    model = llm.effective_model(sess)
    db.append_message(req.session_id, {"kind": "user", "content": f"tanka:{req.theme}"})
    task = db.create_task(req.session_id, kind="tanka", input_data={"theme": req.theme, "max_refines": req.max_refines, "model": model})
    tasks.start_tanka(task["id"], req.session_id, req.theme, req.max_refines, model=model)

    return {"task_id": task["id"], "session_id": req.session_id, "kind": "tanka"}


# ─── Task streaming (SSE) ───

@app.get("/api/tasks/{tid}/stream")
async def stream_task(tid: str) -> StreamingResponse:
    task = db.get_task(tid)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    async def emit() -> AsyncIterator[str]:
        # 接続時点のスナップショットを最初に送って、フロントが種別を知れるように。
        yield _format_sse({
            "type": "task_meta",
            "task_id": tid,
            "kind": task.get("kind"),
            "session_id": task.get("session_id"),
            "status": task.get("status"),
        })

        last_status_check = 0
        check_every = 3  # 3 ハートビートに 1 回 status をチェック
        heartbeats = 0

        async for event in q.read_events(tid):
            if event.get("type") == "_heartbeat":
                # XREAD タイムアウト。タスクが死んでないか念のためチェック
                heartbeats += 1
                if heartbeats - last_status_check >= check_every:
                    last_status_check = heartbeats
                    fresh = db.get_task(tid)
                    if fresh and fresh.get("status") in ("completed", "failed", "cancelled"):
                        # 何らかの理由で done イベントが流れていない → 強制終了
                        yield _format_sse({"type": "done", "status": fresh["status"]})
                        return
                # ハートビートを SSE 側にも流して TCP keepalive 代わりに
                yield ": keepalive\n\n"
                continue
            yield _format_sse(event)
            if event.get("type") == "done":
                return

    return StreamingResponse(emit(), media_type="text/event-stream", headers=_sse_headers())


# ─── Metrics (Phase 2: 品質指標の集計) ───

@app.get("/api/metrics")
async def get_metrics(session_id: str | None = None) -> dict[str, Any]:
    """tanka 生成の品質メトリクスを集計して返す。

    - session_id 省略: 全セッション横断
    - session_id 指定: そのセッションのみ (eval.sh で variant = 1 session の集計に使う)
    """
    return db.compute_metrics(session_id=session_id)


# ─── Eval Monitor (Phase 2 UI): eval/repeat run の進捗を観測する ───

@app.get("/api/eval/runs")
async def get_eval_runs() -> list[dict]:
    """eval.sh / eval-repeat.sh が作った eval セッションを、進捗サマリ付きで返す。
    フロントの「評価モニタ」がこれをポーリングして各 run の状況を表示する。"""
    return db.list_eval_runs()


# ─── Tanka Gallery: 全セッション横断の短歌一覧 ───

@app.get("/api/tanka/records")
async def get_tanka_records(limit: int = 500) -> dict:
    """過去に生成した短歌を全セッション横断で新しい順に返す。フロントの「短歌一覧」ビュー用。

    count は limit 適用前のトータル件数 (UI が「全 N 首」を正しく出せるように)。"""
    records = db.list_tanka_records()
    return {"count": len(records), "items": records[: max(1, min(limit, 2000))]}


# ─── Failures (長期記憶) の閲覧・クリア ───

@app.get("/api/failures")
async def get_failures(limit: int = 50) -> dict:
    """蓄積された違反例を新しい順に返す。UI の「失敗履歴」パネル用。"""
    return {
        "count": db.count_failures(),
        "items": db.list_failures(limit=max(1, min(limit, 500))),
    }


@app.delete("/api/failures")
async def clear_failures_endpoint() -> dict:
    n = db.clear_failures()
    return {"cleared": n}


@app.post("/api/tasks/{tid}/cancel")
async def cancel_task_endpoint(tid: str) -> dict:
    task = db.get_task(tid)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("status") != "running":
        return {"task_id": tid, "status": task.get("status"), "cancelled": False}
    cancelled = await tasks.cancel_task(tid)
    return {"task_id": tid, "cancelled": cancelled}
