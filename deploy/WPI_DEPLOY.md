# WPI VM Deployment Guide

Deploy `stakeholder-interview-sim` to the WPI Ubuntu VM at `interviewsimulator.wpi.edu`.

> Replace the SSH user `thameem` below with your actual WPI shell username if different.

## Architecture on the VM

- **Apache 2.4** terminates HTTPS, proxies HTTP to `127.0.0.1:8000`, AND proxies
  WebSocket (`/ws/`) using `mod_proxy_wstunnel` — this is new vs. the old
  Phase_3_webapp deploy. Without `proxy_wstunnel`, the Realtime audio loop will
  not connect.
- **uvicorn** runs the FastAPI app on `127.0.0.1:8000`.
- **PostgreSQL 16 + pgvector** runs locally on `127.0.0.1:5432`.
- **uv** manages the Python venv at `/opt/stakeholder-interview-sim/backend/.venv`.
- The backend serves the built React frontend from `backend/static/`.
- Outbound HTTPS/WSS to `api.openai.com` must be allowed by the firewall —
  required for OpenAI Realtime API.

## Server layout

```text
/opt/stakeholder-interview-sim/             app code (synced from your Mac)
/opt/stakeholder-interview-sim/backend/.venv  uv-managed venv (created on server)
/opt/stakeholder-interview-sim/backend/static  built React app (copied from frontend/dist)
/opt/stakeholder-interview-sim/.env         server secrets
/etc/systemd/system/stakeholder-interview-sim.service
/etc/apache2/sites-available/stakeholder-interview-sim.conf
~/stakeholder-interview-sim-upload/         temporary upload staging area
```

---

## One-time server setup

### 1. Install system packages (on the server)

```bash
sudo apt update
sudo apt install -y \
  postgresql-16 postgresql-16-pgvector \
  apache2 \
  python3.11 python3.11-venv \
  nodejs npm \
  build-essential libpq-dev curl
```

> If `postgresql-16-pgvector` is unavailable, use the PostgreSQL APT repo:
> `https://wiki.postgresql.org/wiki/Apt`

### 2. Install `uv` (on the server)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Move to a system location so systemd can see it
sudo cp ~/.local/bin/uv /usr/local/bin/uv
```

### 3. Create the Postgres database

```bash
sudo -u postgres psql <<'SQL'
CREATE USER sis WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
CREATE DATABASE sis OWNER sis;
\c sis
CREATE EXTENSION IF NOT EXISTS vector;
SQL
```

Verify:

```bash
PGPASSWORD=CHANGE_ME_STRONG_PASSWORD psql -h 127.0.0.1 -U sis -d sis -c "SELECT extname FROM pg_extension;"
# Should list 'vector' among results.
```

### 4. Upload code from your Mac (NOT from the server)

```bash
rsync -avz \
  --exclude '.git' \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.ruff_cache' \
  "/Users/thameem/Documents/Global Lab/stakeholder-interview-sim/" \
  thameem@interviewsimulator.wpi.edu:~/stakeholder-interview-sim-upload/
```

### 5. Move into the live app directory (on the server)

```bash
sudo mkdir -p /opt/stakeholder-interview-sim
sudo cp -R ~/stakeholder-interview-sim-upload/. /opt/stakeholder-interview-sim/
sudo chown -R thameem:www-data /opt/stakeholder-interview-sim
```

### 6. Create the secrets file (on the server)

```bash
sudo -u thameem nano /opt/stakeholder-interview-sim/.env
sudo chmod 600 /opt/stakeholder-interview-sim/.env
sudo chown thameem:www-data /opt/stakeholder-interview-sim/.env
```

Required contents:

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://sis:CHANGE_ME_STRONG_PASSWORD@127.0.0.1:5432/sis
OPENAI_REALTIME_MODEL=gpt-4o-realtime-preview
EMBEDDING_MODEL=text-embedding-3-small
```

### 7. Install backend dependencies via uv (on the server)

```bash
cd /opt/stakeholder-interview-sim/backend
uv sync --no-dev --frozen
```

This creates `/opt/stakeholder-interview-sim/backend/.venv` with the slim dep
set (no torch, no faiss, no transformers — image is ~250 MB on disk).

### 8. Run database migrations (on the server)

```bash
cd /opt/stakeholder-interview-sim/backend
set -a; . /opt/stakeholder-interview-sim/.env; set +a
uv run alembic upgrade head
```

Expected output ends with: `Running upgrade  -> 0001_initial, initial schema with pgvector`.

### 9. Seed the vector store (on the server, one-shot)

This embeds the corpus + dossier + facts + world-bible chunks via the OpenAI
Embeddings API (~2.3k chunks total, costs roughly $0.05).

```bash
cd /opt/stakeholder-interview-sim/backend
set -a; . /opt/stakeholder-interview-sim/.env; set +a
uv run python scripts/embed_and_load.py
```

Confirm row counts:

```bash
PGPASSWORD=CHANGE_ME_STRONG_PASSWORD psql -h 127.0.0.1 -U sis -d sis -c \
  "SELECT source, count(*) FROM persona_chunks GROUP BY source ORDER BY source;"
PGPASSWORD=CHANGE_ME_STRONG_PASSWORD psql -h 127.0.0.1 -U sis -d sis -c \
  "SELECT count(*) FROM world_bible_chunks;"
```

Expected:

```
 source  | count
---------+-------
 corpus  |  1820
 dossier |  ~45
 facts   |  ~272
(world)  |   114
```

### 10. Build the frontend (on the server)

```bash
cd /opt/stakeholder-interview-sim/frontend
npm ci
npm run build
# The build output goes to frontend/dist — copy it to where the backend serves from
sudo rm -rf /opt/stakeholder-interview-sim/backend/static
sudo cp -R /opt/stakeholder-interview-sim/frontend/dist /opt/stakeholder-interview-sim/backend/static
sudo chown -R thameem:www-data /opt/stakeholder-interview-sim/backend/static
```

### 11. Install the systemd service

```bash
sudo cp /opt/stakeholder-interview-sim/deploy/systemd/stakeholder-interview-sim.service \
        /etc/systemd/system/stakeholder-interview-sim.service
sudo systemctl daemon-reload
sudo systemctl enable --now stakeholder-interview-sim
sudo systemctl status stakeholder-interview-sim
```

### 12. Configure Apache (with WebSocket proxy)

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel headers rewrite ssl
sudo cp /opt/stakeholder-interview-sim/deploy/apache/stakeholder-interview-sim.conf \
        /etc/apache2/sites-available/stakeholder-interview-sim.conf

# Disable the old Phase_3_webapp site if it's still enabled
sudo a2dissite interviewsimulator.conf 2>/dev/null || true
sudo a2ensite stakeholder-interview-sim.conf

sudo apachectl configtest
sudo systemctl reload apache2
```

### 13. Verify end-to-end

```bash
# 1. Health endpoint (local)
curl http://127.0.0.1:8000/api/health
# {"status":"ok"}

# 2. Health endpoint (public)
curl https://interviewsimulator.wpi.edu/api/health

# 3. Personas list
curl https://interviewsimulator.wpi.edu/api/personas

# 4. Open the UI in a browser — should serve the React app
open https://interviewsimulator.wpi.edu/

# 5. Browser test: start interview, speak, hear persona respond, interrupt.
#    If the WebSocket handshake fails, check Apache mod_proxy_wstunnel is loaded:
#    apache2ctl -M | grep wstunnel
```

---

## Update-only workflow

For normal code updates (most days):

### 1. Sync changed files from your Mac

```bash
# Backend
rsync -avz \
  --exclude '.git' --exclude '.env' --exclude '.venv' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.ruff_cache' \
  "/Users/thameem/Documents/Global Lab/stakeholder-interview-sim/backend/" \
  thameem@interviewsimulator.wpi.edu:~/stakeholder-interview-sim-upload/backend/

# Frontend
rsync -avz \
  --exclude '.git' --exclude 'node_modules' --exclude 'dist' \
  "/Users/thameem/Documents/Global Lab/stakeholder-interview-sim/frontend/" \
  thameem@interviewsimulator.wpi.edu:~/stakeholder-interview-sim-upload/frontend/

# Deploy configs
rsync -avz \
  "/Users/thameem/Documents/Global Lab/stakeholder-interview-sim/deploy/" \
  thameem@interviewsimulator.wpi.edu:~/stakeholder-interview-sim-upload/deploy/
```

### 2. Promote staged files (on the server)

```bash
sudo cp -R ~/stakeholder-interview-sim-upload/backend/.    /opt/stakeholder-interview-sim/backend/
sudo cp -R ~/stakeholder-interview-sim-upload/frontend/.   /opt/stakeholder-interview-sim/frontend/
sudo cp -R ~/stakeholder-interview-sim-upload/deploy/.     /opt/stakeholder-interview-sim/deploy/
sudo chown -R thameem:www-data /opt/stakeholder-interview-sim
```

### 3. Rebuild only what changed (on the server)

If backend deps changed (`pyproject.toml` / `uv.lock`):

```bash
cd /opt/stakeholder-interview-sim/backend
uv sync --no-dev --frozen
```

If there's a new Alembic migration:

```bash
cd /opt/stakeholder-interview-sim/backend
set -a; . /opt/stakeholder-interview-sim/.env; set +a
uv run alembic upgrade head
```

If frontend changed:

```bash
cd /opt/stakeholder-interview-sim/frontend
npm ci
npm run build
sudo rm -rf /opt/stakeholder-interview-sim/backend/static
sudo cp -R dist /opt/stakeholder-interview-sim/backend/static
sudo chown -R thameem:www-data /opt/stakeholder-interview-sim/backend/static
```

### 4. Restart the backend

```bash
sudo systemctl restart stakeholder-interview-sim
sudo systemctl status stakeholder-interview-sim
```

Apache only needs reloading if `deploy/apache/*.conf` changed:

```bash
sudo apachectl configtest && sudo systemctl reload apache2
```

---

## Logs

```bash
# Backend service
sudo journalctl -u stakeholder-interview-sim -n 200 --no-pager
sudo journalctl -u stakeholder-interview-sim -f      # follow

# Apache
sudo tail -n 100 /var/log/apache2/stakeholder-interview-sim_error.log
sudo tail -n 100 /var/log/apache2/stakeholder-interview-sim_access.log

# Postgres
sudo journalctl -u postgresql -n 50 --no-pager
```

---

## Common gotchas (new in this stack)

### WebSocket proxy must be enabled

The Realtime audio loop will fail with `WebSocket connection failed` if Apache
doesn't have `mod_proxy_wstunnel` enabled. Verify with:

```bash
apache2ctl -M | grep -E 'wstunnel|proxy_http'
# proxy_http_module (shared)
# proxy_wstunnel_module (shared)
```

The `ProxyPass /ws/ ws://...` directive in
`deploy/apache/stakeholder-interview-sim.conf` **must come before** the
catch-all `ProxyPass /` or the WebSocket upgrade is swallowed by the HTTP
proxy.

### Outbound HTTPS/WSS to OpenAI must work

The backend opens a WebSocket from the VM to `wss://api.openai.com/v1/realtime`.
If WPI's outbound firewall blocks this, the `session_ready` event never fires.
Test:

```bash
curl -I https://api.openai.com/v1/models -H "Authorization: Bearer $OPENAI_API_KEY"
# Expect HTTP/2 200
```

If blocked, request a firewall exception for `api.openai.com:443` (TCP).

### pgvector extension must be created per-database

`CREATE EXTENSION vector` runs inside a specific database. If you ever drop and
recreate the `sis` database, re-run the extension creation before migrations.

### Don't run rsync from the server

If your prompt looks like `thameem@interviewsimulator:~$`, stop. `rsync` should
run on your Mac with the WPI host as the destination.

### `uv sync` on the server needs `uv.lock` to be committed

Always commit `backend/uv.lock` to the repo and sync it. The `--frozen` flag
will fail if the lock file is missing or stale, which is what you want — never
let the server resolve unpinned versions.

### Don't copy local `.env` or `.venv`

`.env` lives only on the server. `.venv` is rebuilt on the server by `uv sync`.

---

## Cost notes

- **OpenAI Realtime API** is the largest cost — roughly $0.06/min audio input,
  $0.24/min audio output (preview pricing, subject to change). A 15-minute
  interview is ~$2-3.
- **Embeddings** are cheap — one-time seed of ~2,300 chunks at
  `text-embedding-3-small` costs roughly $0.05 total.
- **Postgres on this VM** is free (you already own the box).
