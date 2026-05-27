# WPI VM Deployment — `stakeholder-engagement-simulator.wpi.edu`

Deploy this repo to the WPI Ubuntu VM that also hosts `interviewsimulator.wpi.edu`. The two apps coexist:

| App | URL | Port | Install dir | systemd unit |
|---|---|---|---|---|
| Old | `interviewsimulator.wpi.edu` | 8000 | `/opt/interviewsimulator/` | `interviewsimulator.service` |
| **New** | **`stakeholder-engagement-simulator.wpi.edu`** | **8001** | **`/opt/stakeholder-engagement-simulator/`** | **`stakeholder-engagement-simulator.service`** |

> Replace the SSH user `mohammedthameem` and Unix user `thameem` below with your actual WPI usernames if different.

---

## Architecture on the VM

- **Apache 2.4** terminates HTTPS for both hostnames; each vhost reverse-proxies HTTP to its own uvicorn (`127.0.0.1:8000` for the old app, `127.0.0.1:8001` for the new app).
- **No WebSocket proxy.** The realtime audio loop is **WebRTC**, browser ↔ OpenAI directly. The backend never sits in the audio path. Apache only needs `mod_proxy_http` (no `mod_proxy_wstunnel`).
- **uvicorn** runs FastAPI on `127.0.0.1:8001`.
- **PostgreSQL 16 + pgvector** runs locally on `127.0.0.1:5432`; this app needs its own database (`sis` user / `sis` database). The old app does not use Postgres, so there's no conflict.
- **uv** manages the Python venv at `/opt/stakeholder-engagement-simulator/backend/.venv`.
- The backend serves the built React frontend from `backend/static/` (the build step copies `frontend/dist` → `backend/static`).
- **Outbound HTTPS/WSS to `api.openai.com` must be allowed** by the campus firewall — required for OpenAI Realtime (WebRTC SDP exchange and the data channel) and for the embedding API used by RAG.

## Server layout

```text
/opt/stakeholder-engagement-simulator/                  app code (rsync'd from your Mac)
/opt/stakeholder-engagement-simulator/backend/.venv     uv-managed venv (created on server)
/opt/stakeholder-engagement-simulator/backend/static    built React app (copied from frontend/dist)
/opt/stakeholder-engagement-simulator/.env              server secrets (mode 600)
/etc/systemd/system/stakeholder-engagement-simulator.service
/etc/apache2/sites-available/stakeholder-engagement-simulator.conf
~/stakeholder-engagement-simulator-upload/              temporary upload staging area
```

---

## One-time server setup

### 1. Install system packages (server)

```bash
sudo apt update
sudo apt install -y \
  postgresql-16 postgresql-16-pgvector \
  apache2 \
  python3.12 python3.12-venv \
  nodejs npm \
  build-essential libpq-dev curl
```

> If `postgresql-16-pgvector` isn't available in the default repo, add the PostgreSQL APT repo: <https://wiki.postgresql.org/wiki/Apt>.

### 2. Install `uv` (server)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo cp ~/.local/bin/uv /usr/local/bin/uv      # so systemd can find it
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

Verify pgvector is loaded:

```bash
PGPASSWORD=CHANGE_ME_STRONG_PASSWORD psql -h 127.0.0.1 -U sis -d sis \
  -c "SELECT extname FROM pg_extension;"
# Expect 'vector' in the result.
```

### 4. Create the install directory

```bash
sudo mkdir -p /opt/stakeholder-engagement-simulator
sudo chown -R thameem:www-data /opt/stakeholder-engagement-simulator
mkdir -p ~/stakeholder-engagement-simulator-upload/backend
mkdir -p ~/stakeholder-engagement-simulator-upload/frontend
```

### 5. Sync the code from your Mac

Run on your **Mac**, not the server:

```bash
cd "/Users/thameem/Documents/Global Lab/stakeholder-interview-sim"

# Backend
rsync -avz \
  --exclude '.git' --exclude '.env' --exclude '.venv' --exclude 'venv' \
  --exclude '__pycache__' --exclude '*.pyc' \
  backend/ mohammedthameem@stakeholder-engagement-simulator.wpi.edu:~/stakeholder-engagement-simulator-upload/backend/

# Frontend
rsync -avz \
  --exclude '.git' --exclude 'node_modules' --exclude 'dist' \
  frontend/ mohammedthameem@stakeholder-engagement-simulator.wpi.edu:~/stakeholder-engagement-simulator-upload/frontend/

# Deploy artifacts (apache/systemd/this doc)
rsync -avz \
  deploy/ mohammedthameem@stakeholder-engagement-simulator.wpi.edu:~/stakeholder-engagement-simulator-upload/deploy/
```

If you can't yet SSH to the new hostname (DNS not cut over), point the rsync at the old hostname temporarily — both URLs resolve to the same VM:

```bash
… mohammedthameem@interviewsimulator.wpi.edu:~/stakeholder-engagement-simulator-upload/…
```

### 6. Move staged files into the live install directory

Run on the **server**:

```bash
sudo cp -R ~/stakeholder-engagement-simulator-upload/. /opt/stakeholder-engagement-simulator/
sudo chown -R thameem:www-data /opt/stakeholder-engagement-simulator
```

### 7. Create the server env file

```bash
sudo nano /opt/stakeholder-engagement-simulator/.env
sudo chmod 600 /opt/stakeholder-engagement-simulator/.env
sudo chown thameem:thameem /opt/stakeholder-engagement-simulator/.env
```

Required variables:

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://sis:CHANGE_ME_STRONG_PASSWORD@127.0.0.1:5432/sis
OPENAI_REALTIME_MODEL=gpt-realtime
EMBEDDING_MODEL=text-embedding-3-small
PORT=8001
```

### 8. Install backend deps into the uv-managed venv

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u thameem uv sync
```

This creates `backend/.venv` and installs everything from `pyproject.toml` / `uv.lock`.

### 9. Run database migrations

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u thameem uv run alembic upgrade head
```

You should see both migrations apply: `0001_initial` then `0002_session_evaluations`.

### 10. Seed embeddings (one-shot, ~5–10 min)

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u thameem -E uv run python scripts/embed_and_load.py
```

`-E` preserves the shell env so the script picks up `OPENAI_API_KEY` from the .env (alternatively `source /opt/stakeholder-engagement-simulator/.env && export OPENAI_API_KEY` first). The script TRUNCATES the vector tables before inserting, so it's safe to re-run.

Verify:

```bash
PGPASSWORD=CHANGE_ME_STRONG_PASSWORD psql -h 127.0.0.1 -U sis -d sis -c \
  "SELECT 'persona_chunks' AS t, count(*) FROM persona_chunks
   UNION ALL SELECT 'world_bible_chunks', count(*) FROM world_bible_chunks;"
# Expect persona_chunks ~2,137 and world_bible_chunks ~114.
```

### 11. Build the frontend and copy to backend/static

```bash
cd /opt/stakeholder-engagement-simulator/frontend
sudo -u thameem npm ci
sudo -u thameem npm run build

# FastAPI serves the SPA from backend/static (see app/main.py).
sudo -u thameem rm -rf /opt/stakeholder-engagement-simulator/backend/static
sudo -u thameem cp -R dist /opt/stakeholder-engagement-simulator/backend/static
```

### 12. Install the systemd service

```bash
sudo cp /opt/stakeholder-engagement-simulator/deploy/systemd/stakeholder-engagement-simulator.service \
        /etc/systemd/system/stakeholder-engagement-simulator.service
sudo systemctl daemon-reload
sudo systemctl enable --now stakeholder-engagement-simulator
sudo systemctl status stakeholder-engagement-simulator
```

Smoke-test the backend directly (bypassing Apache):

```bash
curl http://127.0.0.1:8001/api/health
# {"status":"ok"}
```

### 13. Configure Apache as a reverse proxy

```bash
sudo cp /opt/stakeholder-engagement-simulator/deploy/apache/stakeholder-engagement-simulator.conf \
        /etc/apache2/sites-available/stakeholder-engagement-simulator.conf

sudo a2enmod proxy proxy_http headers rewrite ssl
sudo a2ensite stakeholder-engagement-simulator.conf
sudo apachectl configtest
sudo systemctl reload apache2
```

> WPI's SSL certs for the new hostname should be installed at
> `/etc/incommon/stakeholder-engagement-simulator.wpi.edu.{crt,key,chain.crt}`.
> If WPI gives you a different layout (e.g., a single `fullchain.pem`), edit
> the three `SSLCertificate*` lines in the vhost accordingly.

Full verify:

```bash
curl https://stakeholder-engagement-simulator.wpi.edu/api/health
```

Open `https://stakeholder-engagement-simulator.wpi.edu/` in a browser, pick a persona, run a short interview, press End, and confirm the score page renders both IQR and SIC panels.

---

## Update-only workflow

For code changes after the initial deploy:

### 1. Sync changed files from your Mac

```bash
cd "/Users/thameem/Documents/Global Lab/stakeholder-interview-sim"

# Backend (whenever Python code, prompts, or SIC keys change)
rsync -avz \
  --exclude '.git' --exclude '.env' --exclude '.venv' --exclude 'venv' \
  --exclude '__pycache__' --exclude '*.pyc' \
  backend/ mohammedthameem@stakeholder-engagement-simulator.wpi.edu:~/stakeholder-engagement-simulator-upload/backend/

# Frontend (whenever React code or styles change)
rsync -avz \
  --exclude '.git' --exclude 'node_modules' --exclude 'dist' \
  frontend/ mohammedthameem@stakeholder-engagement-simulator.wpi.edu:~/stakeholder-engagement-simulator-upload/frontend/
```

### 2. Move and rebuild on the server

```bash
sudo cp -R ~/stakeholder-engagement-simulator-upload/. /opt/stakeholder-engagement-simulator/
sudo chown -R thameem:www-data /opt/stakeholder-engagement-simulator

# If pyproject.toml / uv.lock changed:
cd /opt/stakeholder-engagement-simulator/backend && sudo -u thameem uv sync

# If alembic/versions/ has new migrations:
cd /opt/stakeholder-engagement-simulator/backend && sudo -u thameem uv run alembic upgrade head

# If you re-ran scripts/build_persona_config.py or world chunks changed:
cd /opt/stakeholder-engagement-simulator/backend && sudo -u thameem -E uv run python scripts/embed_and_load.py

# If frontend changed:
cd /opt/stakeholder-engagement-simulator/frontend
sudo -u thameem npm ci
sudo -u thameem npm run build
sudo -u thameem rm -rf /opt/stakeholder-engagement-simulator/backend/static
sudo -u thameem cp -R dist /opt/stakeholder-engagement-simulator/backend/static

sudo systemctl restart stakeholder-engagement-simulator
sudo systemctl status stakeholder-engagement-simulator
```

### 3. Verify

```bash
curl https://stakeholder-engagement-simulator.wpi.edu/api/health
```

---

## Verification

```bash
# Backend reachable internally
curl http://127.0.0.1:8001/api/health

# Backend reachable through Apache + TLS
curl https://stakeholder-engagement-simulator.wpi.edu/api/health

# Both services up
sudo systemctl status stakeholder-engagement-simulator
sudo systemctl status apache2

# DB has data
sudo -u postgres psql -d sis -c \
  "SELECT count(*) FROM persona_chunks; SELECT count(*) FROM session_evaluations;"
```

End-to-end browser test:

1. Open `https://stakeholder-engagement-simulator.wpi.edu/`.
2. Pick any persona, press Start, talk for a few turns, press End.
3. Score page should render with **both** IQR dimensions and SIC tier coverage.
4. Refresh the score page URL — it should still render (evaluation is persisted in `session_evaluations`).

---

## Logs

```bash
# Backend service (FastAPI / uvicorn)
sudo journalctl -u stakeholder-engagement-simulator -n 100 --no-pager
sudo journalctl -u stakeholder-engagement-simulator -f          # live

# Apache vhost
sudo tail -n 100 /var/log/apache2/stakeholder-engagement-simulator_error.log
sudo tail -n 100 /var/log/apache2/stakeholder-engagement-simulator_access.log
```

---

## Known gotchas

### Port collision with the old app

The old `interviewsimulator.service` already binds `127.0.0.1:8000`. This service binds `127.0.0.1:8001`. If you see `address already in use` for 8001, something else (or a stuck instance) is on 8001:

```bash
sudo ss -ltnp | grep 800
```

### `OPENAI_API_KEY` not loaded → 502 from `/api/realtime/token`

Symptoms: browser shows `SDP exchange failed: 502 openai transport error: Illegal header value b'Bearer '`. Root cause: `.env` isn't being read.

Fix: confirm `/opt/stakeholder-engagement-simulator/.env` exists, has `OPENAI_API_KEY=sk-…`, and is readable by the `thameem` user. `app/config.py` searches both `backend/.env` and the repo-root `.env`; the systemd unit also sets `EnvironmentFile=`, so any of those is fine.

```bash
sudo -u thameem cat /opt/stakeholder-engagement-simulator/.env | grep OPENAI_API_KEY
sudo systemctl restart stakeholder-engagement-simulator
```

### WebRTC needs outbound to `api.openai.com`

If the score page loads but `Start interview` fails with an opaque `connection failed`, check whether the campus firewall blocks outbound HTTPS to `api.openai.com` (and the SDP-answer endpoint at `/v1/realtime/calls`). The audio stream itself is over UDP/STUN — those ports also need to be allowed outbound.

### `curl -I` on `/api/health` returns 405

Expected. `curl -I` sends `HEAD`; the route only handles `GET`. Use plain `curl`:

```bash
curl https://stakeholder-engagement-simulator.wpi.edu/api/health
```

### Stale frontend UI

The backend serves whatever is in `backend/static/`. If the UI looks old after a deploy, the static copy got skipped:

```bash
ls -la /opt/stakeholder-engagement-simulator/backend/static/   # check mtime
sudo -u thameem rm -rf /opt/stakeholder-engagement-simulator/backend/static
cd /opt/stakeholder-engagement-simulator/frontend && sudo -u thameem npm run build
sudo -u thameem cp -R dist /opt/stakeholder-engagement-simulator/backend/static
sudo systemctl restart stakeholder-engagement-simulator
```

### `alembic upgrade head` fails with "no such relation"

Usually means the database hasn't been created yet, or `DATABASE_URL` in `.env` points somewhere unreachable. Re-run step 3 (create db + extension), then verify with `psql`.

### Never overwrite `/opt/stakeholder-engagement-simulator/.env`

The rsync commands above exclude `.env` for exactly this reason. If you ever do an unconditional copy, back up the server `.env` first.
