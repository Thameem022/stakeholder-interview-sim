# Stakeholder Engagement Simulator

AI-powered stakeholder interview simulator for the Harbortown / Global Lab
educational program. A signed-in student picks one of four stakeholder
personas, interviews them by speaking naturally in the browser, ends the
interview, and receives a rubric-scored report (IQR + SIC).

Repo name is `stakeholder-interview-sim`; the deployed product name is
**Stakeholder Engagement Simulator**
(`stakeholder-engagement-simulator.wpi.edu`).

## Personas

| Persona id | Role | Nova Sonic voice |
|---|---|---|
| `alex_martinez` | Municipal planner | `tiffany` |
| `michael_mike_alvarez` | Waterfront resident | `matthew` |
| `sarah_donnelly` | Small-business owner | `amy` |
| `thomas_tom_caldwell` | Developer | `matthew` |

Voices are set in `backend/app/personas/voices.py`. Confirm them against the
Nova Sonic voices available to the AWS account and region before go-live.

## Architecture

- **Backend** — FastAPI on Python 3.12, managed with `uv`; asyncpg against
  PostgreSQL + pgvector. In production it also serves the built React SPA
  from `backend/static/`.
- **Frontend** — React 18 + Vite + TypeScript + Tailwind, React Router.
- **AI — Amazon Bedrock, direct, from the backend only** (SR-2026-052
  SEC-AI-001). The browser talks to the SES origin and nothing else, and never
  holds an AI credential.
  - **Live voice persona** — Amazon **Nova Sonic** over a bidirectional stream.
    The backend is in the audio path: browser ↔ SES (WebSocket) ↔ Bedrock.
  - **Retrieval** — PostgreSQL + pgvector over **Titan Text Embeddings V2**
    (1024 dimensions).
  - **Scoring** — IQR + SIC on **Claude Opus 5.5** through the official
    Anthropic SDK's Bedrock client, falling back to **Claude Sonnet 5.5** on a
    refusal or transient error. Fixed `effort`, schema-validated JSON, and the
    IQR overall score computed in code.
  - **Guardrails** — Bedrock Guardrails on persona speech, student turns and
    feedback.
  - AWS credentials come only from the standard chain (role / STS / IAM Roles
    Anywhere / vault), never from `.env` or source.
- **Auth** — Microsoft Entra ID single sign-on (OIDC + PKCE), then an app
  session cookie. Every route except `/api/health` and the sign-in endpoints
  requires a session.

### Interview flow

1. `POST /api/realtime/token` — after the pre-session notice is acknowledged,
   the server inserts the `interview_sessions` row and returns a **60-second,
   single-use stream token** bound to that session and participant. No
   provider credential is ever issued to the browser, and the browser never
   chooses its own session id.
2. The browser opens `WebSocket /api/realtime/stream` on the same origin
   (session cookie + Origin check) and presents the token. The backend opens a
   Nova Sonic stream with the persona's system prompt, voice and the
   `retrieve_context` tool.
3. The microphone is captured by an AudioWorklet as 16 kHz 16-bit PCM and sent
   as binary frames. The backend relays them to Nova Sonic, and sends the
   persona's 24 kHz audio back the same way. Audio is relayed in memory and
   never stored.
4. When the model calls `retrieve_context`, the backend embeds the query with
   Titan and runs parallel pgvector searches over persona and world-bible
   chunks. The same code serves `POST /api/realtime/retrieve`.
5. The backend writes each final turn to the session's JSONB transcript, and
   runs the guardrail on it. Nova Sonic streams have a bounded lifetime, so
   the proxy renews the stream at a turn boundary and replays the
   conversation so far into the new one.
6. "End interview" closes the stream, and the server marks the session ended.
   `POST /api/eval/iqr` then runs IQR and SIC in parallel on Claude, checks the
   feedback with the guardrail, persists it to `session_evaluations` and
   returns it for the score report.

### No barge-in (deliberate)

The persona cannot be interrupted. The browser stops sending microphone
frames from the moment a persona reply starts until its audio has finished
playing (plus a 300 ms tail), so playback bleed, background noise or an early
start never reaches the model mid-reply. Replies need no push-to-talk button.

## Local development

```bash
# 1. Backend dependencies
cd backend && uv sync && cd ..

# 2. Frontend dependencies
cd frontend && npm install && cd ..

# 3. Postgres with pgvector
docker compose up -d db

# 4. Environment
cp .env.example .env
# AI calls need AWS credentials for a DEV account with Bedrock model access:
# sign in with your own profile (e.g. `aws sso login --profile ses-dev`) and
# set AWS_PROFILE. Never put AWS keys in .env.

# 5. Migrations and vector-store seed (Titan embeddings; ~5-10 min, one-shot)
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

### Signing in

Sign-in is **WPI single sign-on (Microsoft Entra ID, OIDC + PKCE) only**. There
are no SES passwords or local accounts. Opening the app signed out goes straight
to the sign-in page; the first sign-in creates the account and its pseudonymous
participant ID, and later sign-ins resume the same one.

For local development without an Entra tenant, run the bundled **mock identity
provider**. It speaks the same protocol, so the app runs its one real sign-in
path:

```bash
cd backend && uv run python -m scripts.dev_oidc_provider   # http://127.0.0.1:9999
```

Set the development `ENTRA_*` values shown in [.env.example](.env.example).
The mock lets you pick an address and app roles. It binds to loopback only, and
production refuses to start with any authority but Microsoft's.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@host:5432/db` |
| `ENVIRONMENT` | No | `dev` (default) or `prod`. `prod` makes cookies `Secure` and refuses to boot without a complete Entra configuration |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_REDIRECT_URI` | Yes | The Entra app registration (placeholders in `.env.example`) |
| `ENTRA_CLIENT_SECRET` | Yes | Injected from the vault / service environment, never committed |
| `ENTRA_AUTHORITY` | No | `https://login.microsoftonline.com` (the only value production accepts) |
| `ENTRA_REQUIRE_APP_ROLE` | No | Refuse tokens without a recognised app role (default `true`) |
| `AWS_REGION` | No | Bedrock region (default `us-east-1`). Credentials come from the AWS chain, never this file |
| `BEDROCK_SCORING_MODEL` / `BEDROCK_SCORING_FALLBACK_MODEL` | No | `anthropic.claude-opus-5-5` / `anthropic.claude-sonnet-5-5` |
| `BEDROCK_SCORING_EFFORT` | No | Claude effort for scoring (default `high`); recorded with every evaluation |
| `BEDROCK_ENRICHMENT_MODEL` | No | Model for the cosmetic coverage text (default `anthropic.claude-sonnet-5-5`) |
| `BEDROCK_EMBEDDING_MODEL_ID` / `BEDROCK_EMBEDDING_DIMENSIONS` | No | `amazon.titan-embed-text-v2:0` / `1024`. The dimension must match the pgvector columns |
| `BEDROCK_SPEECH_MODEL_ID` | No | Nova Sonic model id (default `amazon.nova-sonic-v1:0`) |
| `NOVA_SONIC_STREAM_RENEW_SECONDS`, `REALTIME_MAX_SESSION_MINUTES` | No | Stream renewal age (420 s) and interview time limit (30 min) |
| `BEDROCK_GUARDRAIL_ID` / `BEDROCK_GUARDRAIL_VERSION` | Prod | Required in production |
| `PORT` | No | Defaults to `8000` |
| `AUTH_EMAIL_DOMAIN` | No | Institutional domain a signed-in address must have (`wpi.edu`) |
| `AUTH_COOKIE_NAME` | No | Defaults to `sis_session` |
| `AUTH_SESSION_DEFAULT_HOURS`, `AUTH_SESSION_TOUCH_INTERVAL_SECONDS` | No | App session tuning; see [.env.example](.env.example) |
| `LEGACY_SESSION_OWNER_EMAIL` | Once | Read **only** by migration `0005` to assign pre-auth sessions an owner |

See [.env.example](.env.example) for the annotated set. `docker-compose.yml`
mounts `~/.aws` read-only and passes `AWS_PROFILE` / `AWS_REGION`.

## API surface

Public: `GET /api/health`, and the sign-in endpoints `GET /api/auth/login`
(starts Entra sign-in), `GET /api/auth/callback`, `POST /api/auth/logout`,
`GET /api/auth/me`.

Authenticated (declared once in `main.py`, not per route):
`GET /api/personas`, `GET /api/voices`, `POST /api/realtime/token`,
`POST /api/realtime/retrieve`, `POST /api/realtime/transcript`,
`WebSocket /api/realtime/stream` (authenticates itself: cookie, Origin,
single-use stream token),
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

Eleven Alembic migrations:

| Revision | Adds |
|---|---|
| `0001_initial` | pgvector extension, `personas`, `persona_chunks`, `world_bible_chunks`, `interview_sessions` |
| `0002_session_evaluations` | `session_evaluations`, session user index |
| `0003_auth_tables` | citext, `pending_registrations`, `users`, `auth_sessions` |
| `0004_auth_rate_limits` | `auth_rate_limits` |
| `0005_session_ownership` | `interview_sessions.user_id` ownership backfill |
| `0006_retrieval_events` | `retrieval_events` (per-retrieve-call timing + result telemetry) |
| `0007_pseudonymous_participants` | `participants`; sign-in tables moved to the restricted `identity` schema; sessions re-keyed from `user_id` to `participant_id`; pre-session notice acknowledgement |
| `0008_research_and_incidents` | restricted `research` schema (consent, consented copies, export approvals + log); `identity.account_roles`; `session_flags`; `interview_sessions.purged_at` |
| `0009_retention` | `deletion_log`; research consent no longer cascades from course participants |
| `0010_entra_sso` | Entra ID sign-in: `identity.users.entra_subject`, `identity.oidc_logins`; drops `password_hash` and `pending_registrations` |
| `0011_bedrock` | Titan embedding dimension (1024; chunk tables emptied for re-embedding); `realtime_stream_tokens` |

### Pseudonymous data model

Student work (`interview_sessions` and everything hanging off it) is keyed
**only** by a pseudonymous `participant_id`. Names, email addresses,
credentials and the sign-in ↔ pseudonym link live in the separate `identity`
schema, which nothing in `public` references. Database roles:
`ses_course_reader` (instructors / study personnel — work tables only) and
`ses_support_owner` (the only role that can read `identity`). The data stays
**Restricted**: the mapping exists, so records remain re-linkable.

### Research participation and incidents

Research data (IRB-27-0033) lives in its own `research` schema, readable only
by approved study personnel (`ses_study_personnel`); no coursework table or
endpoint can see a consent decision. It is **off** (`RESEARCH_ENABLED=false`)
until the consent form, DPIA and Data Governance / OGC sign-off are in place —
and production refuses to enable it while the consent text in
`app/research/consent.py` is the draft. Exports need a named approver's
recorded, single-use approval, and the approver cannot be the exporter.
Sessions can be flagged for review and purged through a flag; see
[deploy/RUNBOOK.md](deploy/RUNBOOK.md).

### Retention and self-export

A daily job (`python -m app.jobs.retention`, systemd timer in `deploy/systemd/`)
enforces the retention schedule configured by `RETENTION_*` in `.env`: course
data and the identity mapping go a set period after term end, query telemetry
on a short window, research data only at the protocol's end. Every run writes a
`deletion_log` row. Students download their own transcripts and feedback as a
zip from the score report (`/api/export/...`); SES does no grading.

**Audio is never stored by SES.** It streams from the browser to the AI service
for live transcription and the persona's replies; only the written transcript
comes back. No table, file or browser store holds audio, and
`tests/test_pseudonym.py` fails if one appears.

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
  see [deploy/WPI_DEPLOY.md](deploy/WPI_DEPLOY.md). Apache needs
  `mod_proxy_http` and `mod_proxy_wstunnel` (the interview WebSocket). Outbound
  HTTPS to Entra ID and the AWS Bedrock endpoints must be permitted. AWS
  credentials come from IAM Roles Anywhere or the vault.
- **Container image** (root `Dockerfile`): supply `DATABASE_URL`,
  `ENVIRONMENT`, the Entra variables and AWS credentials from the platform's
  role or secret store, then deploy the image.

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
- **`ENVIRONMENT=prod` refuses to boot** without a complete Entra configuration
  (tenant, client, secret, `https://` redirect URI) pointed at Microsoft's
  authority. The local mock identity provider can never stand in for it.

## Project layout

```
stakeholder-interview-sim/
├── backend/
│   ├── app/
│   │   ├── main.py, config.py, db.py, vector_store.py
│   │   ├── ai/              (Bedrock: Claude client, Titan, Guardrails, AWS credentials)
│   │   ├── realtime/        (stream token, Nova Sonic proxy, RAG, session state)
│   │   ├── auth/            (Entra OIDC, sessions, CSRF, rate limits, dependencies)
│   │   ├── rag/             (persona dossier/facts chunking)
│   │   ├── personas/        (prompts, configs, dossiers, voices, assembly)
│   │   ├── evaluation/      (iqr_scorer, sic_scorer, prompts, sic_keys)
│   │   └── api/             (health, auth, personas, eval routers)
│   ├── alembic/versions/    (0001 … 0011)
│   ├── scripts/             (embed_and_load.py, build_world_chunks.py, …)
│   └── tests/               (pytest; Bedrock and Entra are faked in-process)
├── frontend/
│   ├── public/              (avatars/*.glb, background/, headaudio/dist/, audio/ capture worklet)
│   └── src/
│       ├── App.tsx, api.ts, personas.ts, ScorePage.tsx, ScoreReport.tsx
│       ├── auth/            (AuthContext, sign-in landing, RequireAuth, sso)
│       ├── realtime/streamSession.ts
│       ├── hooks/useRealtimeSession.ts
│       └── components/      (Avatar, Header, Controls, …)
├── deploy/
│   ├── WPI_DEPLOY.md
│   ├── systemd/stakeholder-engagement-simulator.service
│   └── apache/stakeholder-engagement-simulator.conf
├── Dockerfile               (multi-stage: Node build → Python runtime)
└── docker-compose.yml       (local dev: db + backend + frontend)
```
