# stakeholder-interview-sim

AI-powered stakeholder interview simulator for the Harbortown / Global Lab
educational program. Students interview persona stakeholders (municipal
planner, waterfront resident, small-business owner, developer) by speaking
naturally; the system grades the interview using the IQR and SIC rubrics.

## Architecture

- **Backend** — FastAPI on Python 3.11, managed with `uv`. Bridges a browser
  WebSocket to the OpenAI Realtime API for audio-in/audio-out conversation.
  Per-turn RAG context is retrieved from pgvector and injected into the
  Realtime conversation before each model response.
- **Frontend** — React 18 + Vite + TypeScript. Captures mic audio via
  AudioWorklet (PCM16, 24 kHz), plays back assistant audio gaplessly, drives
  an amplitude-based avatar mouth animation, and handles barge-in via
  client-side VAD.
- **Vector store** — PostgreSQL + pgvector. Embeddings produced once by
  OpenAI `text-embedding-3-small`; no torch or sentence-transformers in the
  runtime image.
- **Evaluation** — IQR + SIC scorers (LangChain + gpt-4o) ported verbatim
  from the previous system. Persona prompts and configs are preserved
  byte-for-byte.

## Local development

```bash
# 1. Install backend dependencies
cd backend
uv sync
cd ..

# 2. Install frontend dependencies
cd frontend
npm install
cd ..

# 3. Start Postgres with pgvector
docker compose up -d db

# 4. Run migrations and seed the vector store
cd backend
cp ../.env.example ../.env   # fill in OPENAI_API_KEY
set -a; . ../.env; set +a
uv run alembic upgrade head
uv run python scripts/embed_and_load.py
cd ..

# 5. Start backend (terminal 1)
cd backend
uv run uvicorn app.main:app --reload --port 8000

# 6. Start frontend dev server (terminal 2)
cd frontend
npm run dev
# Open http://localhost:5173
```

## Production deployment

- **WPI VM** (Apache + systemd + local Postgres): see
  [deploy/WPI_DEPLOY.md](deploy/WPI_DEPLOY.md).
- **Single Railway service** (Dockerfile at repo root, Railway Postgres):
  set `OPENAI_API_KEY`, `DATABASE_URL`, `OPENAI_REALTIME_MODEL` env vars and
  deploy from this repo's `Dockerfile`.

## Project layout

```
stakeholder-interview-sim/
├── backend/
│   ├── app/
│   │   ├── main.py, config.py, db.py, vector_store.py
│   │   ├── realtime/        (WebSocket + OpenAI Realtime proxy)
│   │   ├── rag/             (persona dossier/facts chunking)
│   │   ├── personas/        (prompts, configs, dossiers, voices, assembly)
│   │   ├── evaluation/      (iqr_scorer, sic_scorer, prompts, sic_keys)
│   │   └── api/             (health, personas, eval routers)
│   ├── alembic/             (schema migrations)
│   ├── scripts/             (embed_and_load.py, build_world_chunks.py, ...)
│   └── tests/
├── frontend/
│   └── src/
│       ├── App.tsx, main.tsx, api.ts
│       ├── hooks/useRealtimeSession.ts
│       ├── audio/           (pcm-worklet.js, playback.ts, vad.ts)
│       └── components/      (Avatar, Header, PersonaSelector, …)
├── deploy/
│   ├── WPI_DEPLOY.md
│   ├── systemd/stakeholder-interview-sim.service
│   └── apache/stakeholder-interview-sim.conf
├── Dockerfile               (multi-stage: Node build → Python runtime)
├── docker-compose.yml       (local dev: db + backend + frontend)
└── .github/workflows/ci.yml
```

## Required environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | Yes | Realtime API + embeddings + IQR/SIC scoring (all OpenAI) |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@host:5432/db` |
| `OPENAI_REALTIME_MODEL` | No | Defaults to `gpt-4o-realtime-preview` |
| `EMBEDDING_MODEL` | No | Defaults to `text-embedding-3-small` |
| `PORT` | No | Defaults to `8000` |
