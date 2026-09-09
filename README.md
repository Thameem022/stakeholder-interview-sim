# Stakeholder Engagement Simulator

AI-powered stakeholder interview simulator for the Harbortown / Global Lab
educational program. A signed-in student picks one of four stakeholder
personas, interviews them by speaking naturally in the browser, ends the
interview, and receives a rubric-scored report (IQR + SIC).

Repo name is `stakeholder-interview-sim`; the deployed product name is
**Stakeholder Engagement Simulator**
(`stakeholder-engagement-simulator.wpi.edu`).

## Personas

| Persona id | Role | Realtime voice |
|---|---|---|
| `alex_martinez` | Municipal planner | `echo` |
| `michael_mike_alvarez` | Waterfront resident | `ash` |
| `sarah_donnelly` | Small-business owner | `coral` |
| `thomas_tom_caldwell` | Developer | `ballad` |

## Architecture

- **Backend** — FastAPI on Python 3.11, managed with `uv`; asyncpg against
  PostgreSQL + pgvector. In production it also serves the built React SPA
  from `backend/static/`.
- **Frontend** — React 18 + Vite + TypeScript + Tailwind, React Router.
- **Realtime audio** — **browser-direct WebRTC** against the OpenAI Realtime
  API. The backend is never in the audio path; it only mints the ephemeral
  key, fulfills RAG tool calls, and persists transcripts.
- **Vector store** — PostgreSQL + pgvector, embedded once with OpenAI
  `text-embedding-3-small`. No torch or sentence-transformers in the runtime
  image.
- **Evaluation** — IQR + SIC scorers (LangChain + `gpt-4o`, `gpt-4o-mini`
  fallback). Persona prompts and configs are preserved byte-for-byte from the
  previous system.
- **Auth** — hand-rolled cookie sessions (Argon2 passwords, SHA-256 session
  token hashes). Every route except `/api/health` and `/api/auth/*` requires
  a session.

### Interview flow

1. `POST /api/realtime/token` — the server assembles the Realtime session
   config (persona instructions, voice, server-VAD turn detection, the
   `retrieve_context` tool), inserts the `interview_sessions` row, then
   exchanges the long-lived `OPENAI_API_KEY` for a short-lived ephemeral key.
   **The browser never sees the real API key**, and never chooses its own
   session id.
2. The browser performs the SDP exchange directly with
   `https://api.openai.com/v1/realtime/calls`. Audio then flows
   browser ↔ OpenAI; the data channel carries transcripts and tool calls.
3. When the model calls `retrieve_context`, the browser relays the query to
   `POST /api/realtime/retrieve`, which embeds it and runs parallel pgvector
   searches over persona chunks and Harbortown world-bible chunks.
4. Each completed turn is posted to `POST /api/realtime/transcript` and
   appended to the session's JSONB transcript.
5. "End interview" calls `POST /api/eval/iqr`, which runs IQR and SIC in
   parallel, persists the merged payload to `session_evaluations`, and
   returns it for the score report.

### No barge-in (deliberate)

The persona cannot be interrupted. The server session sets
`interrupt_response: false` with a 0.95 VAD threshold; the client
additionally sets `track.enabled = false` **and** `sender.replaceTrack(null)`
for the duration of every assistant response, so nothing — not even silence
packets — reaches OpenAI's server VAD mid-response. Server VAD is still on
for user turns, so replies auto-commit with no push-to-talk button. Output
speed is `0.9` to give students more processing time.

## Local development

```bash
# 1. Backend dependencies
cd backend && uv sync && cd ..

# 2. Frontend dependencies
cd frontend && npm install && cd ..

# 3. Postgres with pgvector
docker compose up -d db

# 4. Environment
cp .env.example .env          # fill in OPENAI_API_KEY

# 5. Migrations and vector-store seed (~5-10 min, one-shot)
cd backend
set -a; . ../.env; set +a
uv run alembic upgrade head
uv run python scripts/embed_and_load.py
cd ..

# 6. Backend (terminal 1)
cd backend
uv run uvicorn app.main:app --reload --port 8000

# 7. Frontend dev server (terminal 2)
cd frontend
npm run dev
# Open http://localhost:5173 — Vite proxies /api to :8000
```

### First sign-in

There is **no email delivery yet**, so registration runs in fixed-temporary-
password mode:

1. Register at `/register` with an address matching
   `AUTH_REGISTRATION_ALLOWLIST` (default `*@wpi.edu`).
2. Exchange `AUTH_DEV_TEMP_PASSWORD` (default `7QF-42KD-XM`) for a real
   password of at least `AUTH_MIN_PASSWORD_LENGTH` characters.

Registration is two-phase: `register` only writes a `pending_registrations`
row, and a `users` row appears only when the temporary password is exchanged.
An empty `AUTH_REGISTRATION_ALLOWLIST` permits **nobody** — opening
registration up is meant to be deliberate.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Realtime API + embeddings + IQR/SIC scoring |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@host:5432/db` |
| `ENVIRONMENT` | No | `dev` (default) or `prod`. `prod` makes the session cookie `Secure` and refuses to boot while `AUTH_TEMP_PASSWORD_MODE=fixed` |
| `OPENAI_REALTIME_MODEL` | No | Defaults to `gpt-realtime` |
| `EMBEDDING_MODEL` | No | Defaults to `text-embedding-3-small` |
| `PORT` | No | Defaults to `8000` |
| `AUTH_EMAIL_DOMAIN` | No | Institutional domain enforced at registration (`wpi.edu`) |
| `AUTH_REGISTRATION_ALLOWLIST` | No | Who may register while the temp password is fixed. Empty = nobody |
| `AUTH_TEMP_PASSWORD_MODE` | No | `fixed` (default) or `random`. Switch to `random` once email delivery exists |
| `AUTH_DEV_TEMP_PASSWORD` | No | The shared temporary password while mode is `fixed` |
| `AUTH_COOKIE_NAME` | No | Defaults to `sis_session` |
| `AUTH_SESSION_DEFAULT_HOURS` / `AUTH_SESSION_REMEMBER_DAYS` | No | Session TTL without / with "remember me" |
| `AUTH_MIN_PASSWORD_LENGTH`, `AUTH_TEMP_PASSWORD_TTL_HOURS`, `AUTH_MAX_TEMP_PASSWORD_ATTEMPTS`, `AUTH_SESSION_TOUCH_INTERVAL_SECONDS` | No | Auth tuning; see [.env.example](.env.example) |
| `LEGACY_SESSION_OWNER_EMAIL` | Once | Read **only** by migration `0005` to assign pre-auth sessions an owner |

See [.env.example](.env.example) for the annotated set. Note that
`docker-compose.yml` passes an explicit `OPENAI_REALTIME_MODEL` default that
overrides the application default.

## API surface

Public: `GET /api/health`, and `/api/auth/register`, `/api/auth/set-password`,
`/api/auth/login`, `/api/auth/logout`, `/api/auth/me`.

Authenticated (declared once in `main.py`, not per route):
`GET /api/personas`, `GET /api/voices`, `POST /api/realtime/token`,
`POST /api/realtime/retrieve`, `POST /api/realtime/transcript`,
`POST /api/eval/iqr`, `POST /api/eval/sic`,
`GET /api/eval/sessions/{session_id}/latest`.

Two conventions worth knowing:

- **Ownership failures answer 404, never 403.** Someone else's session is
  indistinguishable from one that does not exist, so holding a UUID never
  confirms it names anything real. A 401 reaching the browser therefore means
  exactly one thing: the login session is gone.
- **Endpoints that spend money are metered per user**, not per IP (campus NAT
  would let one runaway client throttle everyone), via the
  `auth_rate_limits` table.

## Evaluation

- **IQR** — four dimensions (`framing_and_stakeholder_fit`,
  `question_quality_and_precision`, `probing_and_follow_up_depth`,
  `listening_interpretation_and_stewardship`), each scored 1–10 with an
  evidence quote and a "what was missed", plus an overall score, skill label,
  and a top strip (strength / missed opportunities / next move).
- **SIC** — grades which catalog items from
  `app/evaluation/sic_keys/<persona_id>_sic_key.json` the student elicited,
  distinguishing **earned** from **volunteered** disclosure.
- Evaluator prompts are versioned (`prompts/iqr/v2/`, `prompts/sic/v2/`);
  bump the path constant in the scorer to upgrade.
- `sanitize_transcript` strips isolated speech-to-text artifacts ("um",
  "bye") before either scorer sees the transcript.

## Database

Five Alembic migrations:

| Revision | Adds |
|---|---|
| `0001_initial` | pgvector extension, `personas`, `persona_chunks`, `world_bible_chunks`, `interview_sessions` |
| `0002_session_evaluations` | `session_evaluations`, session user index |
| `0003_auth_tables` | citext, `pending_registrations`, `users`, `auth_sessions` |
| `0004_auth_rate_limits` | `auth_rate_limits` |
| `0005_session_ownership` | `interview_sessions.user_id` ownership backfill |

`0005` reads `LEGACY_SESSION_OWNER_EMAIL` once to assign pre-auth sessions an
owner, falling back to the oldest account, and **fails loudly rather than
guessing** if neither resolves. Read the dedicated section of
[deploy/WPI_DEPLOY.md](deploy/WPI_DEPLOY.md) before running it against an
existing install.

`scripts/embed_and_load.py` is idempotent (it truncates both chunk tables
before inserting) and loads ~2,137 persona chunks and 114 world chunks.

## Tests

```bash
cd backend && uv run pytest
```

Coverage is currently auth and authorization only (`tests/test_auth.py`,
`tests/test_authz.py`). The realtime, RAG, and scoring paths have no
automated coverage. There is no CI workflow in this repo.

Frontend: `npm run typecheck` and `npm run lint`. Vitest is configured
(`npm run test`) but no frontend test files exist yet.

## Production deployment

- **WPI VM** (Apache → uvicorn on `127.0.0.1:8001`, local Postgres, systemd):
  see [deploy/WPI_DEPLOY.md](deploy/WPI_DEPLOY.md). Apache needs only
  `mod_proxy_http` — there is no WebSocket tunnel, because the audio path is
  WebRTC. Outbound HTTPS to `api.openai.com` must be permitted.
- **Single Railway service** (root `Dockerfile`, Railway Postgres): set
  `OPENAI_API_KEY`, `DATABASE_URL`, `ENVIRONMENT`, and the auth variables,
  then deploy from this repo's `Dockerfile`.

The frontend build output (`frontend/dist`) is copied to `backend/static/`,
which FastAPI serves with an SPA fallback.

## Gotchas

- **Alembic does not read `.env`.** It falls back to the DSN in
  `alembic.ini`. Pass `DATABASE_URL` explicitly on the server.
- **`.mjs` MIME type is registered explicitly in `main.py`.** Browsers reject
  AudioWorklet modules served with a non-JS MIME type, which silently breaks
  the HeadAudio lip-sync pipeline in production.
- **HeadAudio asset URLs carry a `?v=` cache-buster.** A broken deploy once
  had these paths return `index.html`, which browsers cached and kept serving.
  Bump `HEADAUDIO_ASSET_VERSION` in `Avatar.tsx` if that recurs.
- **Persona naming has two layers.** Configs and corpus files use role slugs
  (`municipal_planner`, `urban_planner`); runtime keys are person slugs
  (`alex_martinez`). `resolve_persona_record` bridges them by alias — don't
  rename one side in isolation.
- **`ENVIRONMENT=prod` with `AUTH_TEMP_PASSWORD_MODE=fixed` refuses to boot**,
  because every account would share one known temporary password.

## Project layout

```
stakeholder-interview-sim/
├── backend/
│   ├── app/
│   │   ├── main.py, config.py, db.py, vector_store.py
│   │   ├── realtime/        (token mint, RAG fulfillment, session state)
│   │   ├── auth/            (passwords, sessions, rate limits, dependencies)
│   │   ├── rag/             (persona dossier/facts chunking)
│   │   ├── personas/        (prompts, configs, dossiers, voices, assembly)
│   │   ├── evaluation/      (iqr_scorer, sic_scorer, prompts, sic_keys)
│   │   └── api/             (health, auth, personas, eval routers)
│   ├── alembic/versions/    (0001 … 0005)
│   ├── scripts/             (embed_and_load.py, build_world_chunks.py, …)
│   └── tests/               (auth + authz)
├── frontend/
│   ├── public/              (avatars/*.glb, background/, headaudio/dist/)
│   └── src/
│       ├── App.tsx, api.ts, personas.ts, ScorePage.tsx, ScoreReport.tsx
│       ├── auth/            (AuthContext, Login, Register, RequireAuth)
│       ├── realtime/webrtc.ts
│       ├── hooks/useRealtimeSession.ts
│       └── components/      (Avatar, Header, Controls, …)
├── deploy/
│   ├── WPI_DEPLOY.md
│   ├── systemd/stakeholder-engagement-simulator.service
│   └── apache/stakeholder-engagement-simulator.conf
├── Dockerfile               (multi-stage: Node build → Python runtime)
└── docker-compose.yml       (local dev: db + backend + frontend)
```
