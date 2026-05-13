# Tanka Chat (FastAPI + React + MongoDB, dockerised)

Local web app for chatting with `llm-jp-4-8b-thinking` running in LM Studio,
with a structured tanka-generation pipeline (Plan → Compose → Verify → Refine)
and **persistent chat history** in MongoDB. Sessions are listed in a sidebar so
you can switch between conversations and resume after a reload.

> For development-level architecture details (module diagrams, data flow,
> data model, design decisions, extension guide), see [docs/architecture.md](docs/architecture.md).

## Architecture

```
Host (macOS)
  LM Studio (:1234)
        ▲
        │ host.docker.internal:1234
  ┌─────┴─────────── Docker network ──────────────┐
  │                                               │
  │  frontend (Vite :5178)                        │
  │       │  /api/* (proxy)                       │
  │       ▼                                       │
  │  backend (FastAPI :8001) ──► mongo:27017      │
  │                              (mongo:8.0)      │
  └───────────────────────────────────────────────┘
```

- Backend talks to LM Studio host-to-host, so **CORS in LM Studio is not needed**.
- Vite dev server proxies `/api/*` to the backend service via the docker network.
- MongoDB data is persisted in a named volume (`mongo_data`).

## Prerequisites

- Docker (Desktop on macOS/Windows) with `docker compose` v2
- LM Studio running on the host with `llm-jp-4-8b-thinking` loaded (`lms server start` is enough — no `--cors` needed)

## Run

```bash
./app/start.sh
```

This runs `docker compose up --build`. First run pulls images and builds; subsequent runs are fast. Open <http://localhost:5178>.

Stop with **Ctrl-C** (or `docker compose down` from another terminal). Use `docker compose down -v` to also wipe the MongoDB volume.

## Manual operations

```bash
# logs
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f mongo

# rebuild after dependency changes
docker compose build backend
docker compose build frontend

# inspect MongoDB
docker compose exec mongo mongosh tanka_chat
> db.sessions.find({}, {messages: 0}).pretty()
> db.sessions.findOne({_id: ObjectId("…")})

# wipe DB (development)
docker compose down -v
```

## Endpoints

```
GET    /api/health               LM Studio + MongoDB health
GET    /api/sessions             list sessions (no message bodies)
POST   /api/sessions             create new session
GET    /api/sessions/{id}        full session with messages
PATCH  /api/sessions/{id}        rename
DELETE /api/sessions/{id}        delete

POST   /api/chat                 SSE: chunk → complete → done
POST   /api/tanka                SSE: phase_start/chunk/phase_end/validation/complete/done
```

## Configuration (env vars on backend container)

| Variable | Default | Purpose |
|---|---|---|
| `LM_STUDIO_URL` | `http://host.docker.internal:1234/v1` | LM Studio endpoint |
| `LM_STUDIO_MODEL` | `llm-jp-4-8b-thinking` | Model id |
| `MONGO_URL` | `mongodb://mongo:27017` | MongoDB connection string |
| `MONGO_DB` | `tanka_chat` | Database name |

Edit `docker-compose.yml` to override.

## Without Docker (legacy / fallback)

If you'd rather run things directly on the host:

```bash
# 1. MongoDB on the host (or pointed at a remote)
brew services start mongodb-community  # or run mongo container manually

# 2. Backend (set env vars to localhost variants)
cd app/backend
uv sync
MONGO_URL=mongodb://localhost:27017 uv run uvicorn main:app --reload --port 8001

# 3. Frontend
cd app/frontend
npm install
npm run dev
```

The Vite proxy defaults to `http://localhost:8001` when `BACKEND_URL` is not set, so nothing else changes.
