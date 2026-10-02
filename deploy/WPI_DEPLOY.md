# WPI VM Deployment — `stakeholder-engagement-simulator.wpi.edu`

Deploy this repo to the WPI Ubuntu VM that also hosts `interviewsimulator.wpi.edu`. The two apps coexist:

| App | URL | Port | Install dir | systemd unit |
|---|---|---|---|---|
| Old | `interviewsimulator.wpi.edu` | 8000 | `/opt/interviewsimulator/` | `interviewsimulator.service` |
| **New** | **`stakeholder-engagement-simulator.wpi.edu`** | **8001** | **`/opt/stakeholder-engagement-simulator/`** | **`stakeholder-engagement-simulator.service`** |

> Both the SSH login and the Unix service user are `mohammedthameem` on this VM
> (no separate `thameem` account exists). Replace with your own WPI username
> if you fork this for a different host. The systemd unit
> (`deploy/systemd/stakeholder-engagement-simulator.service`) is already set
> to `User=mohammedthameem`.

---

## Architecture on the VM

- **Apache 2.4** terminates HTTPS for both hostnames; each vhost reverse-proxies HTTP to its own uvicorn (`127.0.0.1:8000` for the old app, `127.0.0.1:8001` for the new app).
- **One WebSocket route.** The live interview streams audio browser ↔ SES over `wss://…/api/realtime/stream`, and the backend relays it to Amazon Bedrock (Nova Sonic). Apache needs `mod_proxy_http` and `mod_proxy_wstunnel`; see `deploy/apache/`.
- **uvicorn** runs FastAPI on `127.0.0.1:8001`.
- **PostgreSQL 16 + pgvector** runs locally on `127.0.0.1:5432`; this app needs its own database (`sis` user / `sis` database). The old app does not use Postgres, so there's no conflict.
- **uv** manages the Python venv at `/opt/stakeholder-engagement-simulator/backend/.venv`.
- The backend serves the built React frontend from `backend/static/` (the build step copies `frontend/dist` → `backend/static`).
- **Outbound HTTPS from the VM** must be allowed to Entra ID (`login.microsoftonline.com`) and to the AWS endpoints in `AWS_REGION`: `bedrock-runtime.<region>.amazonaws.com` (Nova Sonic, Titan, Guardrails), `bedrock-mantle.<region>.api.aws` (Claude), and, with IAM Roles Anywhere, `rolesanywhere.<region>.amazonaws.com`. Browsers only ever connect to the SES hostname.

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
sudo chown -R mohammedthameem:www-data /opt/stakeholder-engagement-simulator
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
  --exclude '.git' --exclude 'node_modules' --exclude '/dist' \
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
sudo chown -R mohammedthameem:www-data /opt/stakeholder-engagement-simulator
```

### 7. Create the server env file

```bash
sudo nano /opt/stakeholder-engagement-simulator/.env
sudo chmod 600 /opt/stakeholder-engagement-simulator/.env
sudo chown mohammedthameem:mohammedthameem /opt/stakeholder-engagement-simulator/.env
```

Required variables:

```env
DATABASE_URL=postgresql+asyncpg://sis:CHANGE_ME_STRONG_PASSWORD@127.0.0.1:5432/sis
PORT=8001
AWS_REGION=us-east-1
AWS_PROFILE=ses-bedrock            # the credential_process profile from step 7b
BEDROCK_GUARDRAIL_ID=<guardrail-id>
BEDROCK_GUARDRAIL_VERSION=<numbered version, not DRAFT>
```

There is **no AI key in this file**. AWS credentials are short-lived and come
from the profile set up in step 7b. With `ENVIRONMENT=prod` the service refuses
to start if a long-term AWS access key is present in its environment, or if
`BEDROCK_GUARDRAIL_ID` is missing.

### 7a. Register SES in Entra ID (single sign-on)

Sign-in is Entra ID only (SR-2026-052 SEC-IAM-001). With the identity team:

1. **App registration**, single tenant. Platform **Web**, redirect URI
   `https://stakeholder-engagement-simulator.wpi.edu/api/auth/callback`.
2. **Client secret** (or certificate). Store it in the vault and deliver it to
   the service as `ENTRA_CLIENT_SECRET`. Never commit it, and rotate it on the
   vault's schedule.
3. **App roles.** The values must match exactly: `Student`, `Instructor`,
   `StudyPersonnel`, `ExportApprover`, `SupportOwner`. SES syncs a user's roles
   from these at every sign-in. A sign-in with none of them is refused.
4. **Enterprise application → Properties → Assignment required = Yes.** Assign
   the roster-driven ID2050 group to `Student`. Assign named staff to the other
   roles, and give `StudyPersonnel` only to the IRB's approved study personnel.
5. **Conditional Access:** require MFA for this application. No VPN is required;
   assignment + MFA + TLS is the access control.
6. **Token configuration:** add the optional ID-token claims `email`,
   `given_name` and `family_name`.
7. **Term end:** remove the term's group assignment (or let the roster sync do
   it), so access ends with the course.

Then add to the server `.env`, with the secret coming from the vault:

```env
ENTRA_TENANT_ID=<tenant-id>
ENTRA_CLIENT_ID=<application-client-id>
ENTRA_CLIENT_SECRET=<from-the-vault>
ENTRA_REDIRECT_URI=https://stakeholder-engagement-simulator.wpi.edu/api/auth/callback
ENVIRONMENT=prod
```

With `ENVIRONMENT=prod` the service refuses to start if any of these are
missing, if the redirect URI is not `https://`, or if `ENTRA_AUTHORITY` is
anything but `https://login.microsoftonline.com`.

### 7b. AWS credentials for Bedrock (no keys on the server)

SR-2026-052 items 1.1 / 1.6. All AI runs in WPI's AWS account on Bedrock,
through a dedicated least-privilege role, with short-lived credentials that
never leave the server. Use a **separate development account** for development;
never production credentials.

1. **Model access** in `AWS_REGION`: Claude Opus 5.5 and Claude Sonnet 5.5 (the
   Messages-API Bedrock endpoint), Amazon Titan Text Embeddings V2, and Amazon
   Nova Sonic.
2. **IAM role `ses-bedrock`**, allowing exactly what SES calls and nothing else.
   Confirm the action names and model ARNs against the Bedrock console for your
   region and model versions before applying:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Sid": "ClaudeScoringAndWrittenPersona", "Effect": "Allow",
         "Action": "bedrock-mantle:CreateInference",
         "Resource": ["<ARN of anthropic.claude-opus-5-5>", "<ARN of anthropic.claude-sonnet-5-5>"] },
       { "Sid": "TitanEmbeddings", "Effect": "Allow",
         "Action": "bedrock:InvokeModel",
         "Resource": "arn:aws:bedrock:<region>::foundation-model/amazon.titan-embed-text-v2:0" },
       { "Sid": "NovaSonicVoice", "Effect": "Allow",
         "Action": "bedrock:InvokeModelWithBidirectionalStream",
         "Resource": "arn:aws:bedrock:<region>::foundation-model/amazon.nova-sonic-v1:0" },
       { "Sid": "Guardrail", "Effect": "Allow",
         "Action": "bedrock:ApplyGuardrail",
         "Resource": "arn:aws:bedrock:<region>:<account-id>:guardrail/<guardrail-id>" }
     ]
   }
   ```
3. **Credential delivery.** The VM is on Nutanix, which has no EC2 instance
   metadata, so use **IAM Roles Anywhere** (an X.509 certificate from a WPI CA
   is exchanged for STS credentials), or vault-issued STS credentials. Either
   way it plugs into the standard AWS chain as a `credential_process` profile.
   For the service user's `~/.aws/config`:

   ```ini
   [profile ses-bedrock]
   credential_process = /usr/local/bin/aws_signing_helper credential-process \
       --certificate /etc/ses/bedrock.crt --private-key /etc/ses/bedrock.key \
       --trust-anchor-arn <trust-anchor-arn> --profile-arn <profile-arn> --role-arn <ses-bedrock-role-arn>
   region = us-east-1
   ```

   The certificate key is readable only by the service user. Rotate it on the
   CA's schedule. botocore refreshes the STS credentials before they expire.
4. **Guardrail.** Create a Bedrock Guardrail with content filters (hate,
   insults, sexual, violence, misconduct, prompt attacks) and the sensitive
   information types students should not disclose (e.g. phone, email, address,
   government ids). Publish a **numbered version** and set
   `BEDROCK_GUARDRAIL_ID` / `BEDROCK_GUARDRAIL_VERSION`. An intervention on
   persona speech ends the interview and flags it; on a student turn it flags
   it; on feedback it withholds and flags it (see RUNBOOK.md).
5. **CloudTrail** must cover this account (organizational control).

Check from the VM, as the service user:

```bash
sudo -u mohammedthameem AWS_PROFILE=ses-bedrock aws sts get-caller-identity
```

### 8. Install backend deps into the uv-managed venv

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u mohammedthameem uv sync
```

This creates `backend/.venv` and installs everything from `pyproject.toml` / `uv.lock`.

### 9. Run database migrations

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u mohammedthameem uv run alembic upgrade head
```

You should see every migration apply in order, `0001_initial` through `0010_entra_sso`.

> **Before `0007` on a server:** the database owner (`sis`) cannot create roles,
> so create the three access roles as the Postgres superuser **first** — the
> migration then grants them the right access (and prints a NOTICE if they are
> missing):
>
> ```bash
> sudo -u postgres psql -c "CREATE ROLE ses_support_owner NOLOGIN;" \
>                       -c "CREATE ROLE ses_course_reader NOLOGIN;" \
>                       -c "CREATE ROLE ses_study_personnel NOLOGIN;"
> ```
>
> Grant `ses_support_owner` only to the Solution Support Owner's own login role
> (`GRANT ses_support_owner TO <their_login>;`); instructors who need database
> read access get `ses_course_reader`, which cannot read the `identity` or
> `research` schemas; only the IRB's approved study personnel get
> `ses_study_personnel` (the `research` schema).
>
> If the roles are created after migrating, apply the `GRANT` statements from
> migrations `0007`, `0008` and `0009` by hand. Do **not** downgrade and
> re-upgrade to get them: the `0008` and `0010` downgrades are not lossless.
>
> Application roles (instructor, study personnel, export approver, support
> owner) are separate: they are Entra app-role assignments (step 7a), synced at
> every sign-in — see [RUNBOOK.md](RUNBOOK.md#roles-in-ses).

> **`alembic` does not load `.env`.** `alembic/env.py` falls back to the DSN in
> `alembic.ini` (`postgres:postgres@localhost/sis`), which is not this server's
> credentials. Pass `DATABASE_URL` explicitly, or `source ../.env && export DATABASE_URL`
> first, or the migration will fail to connect — or worse, connect to the wrong database.

> **On a fresh install `0005` is a no-op** (no interview sessions exist yet). On an
> existing install it needs a user account to assign the old sessions to — see
> *Upgrading an existing install to authenticated access* below.

### 10. Seed embeddings (one-shot, ~5–10 min)

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u mohammedthameem env AWS_PROFILE=ses-bedrock uv run python scripts/embed_and_load.py
```

Embeddings come from Titan on Bedrock with the step-7b credentials. The script
TRUNCATES the vector tables before inserting, so it's safe to re-run. **It must
be re-run after migration `0011`**, which empties the chunk tables to change
the vector dimension (1536 → 1024). Until then retrieval returns no context.

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
sudo -u mohammedthameem npm ci
sudo -u mohammedthameem npm run build

# FastAPI serves the SPA from backend/static (see app/main.py).
sudo -u mohammedthameem rm -rf /opt/stakeholder-engagement-simulator/backend/static
sudo -u mohammedthameem cp -R dist /opt/stakeholder-engagement-simulator/backend/static
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
  --exclude '.git' --exclude 'node_modules' --exclude '/dist' \
  frontend/ mohammedthameem@stakeholder-engagement-simulator.wpi.edu:~/stakeholder-engagement-simulator-upload/frontend/
```

### 2. Move and rebuild on the server

```bash
sudo cp -R ~/stakeholder-engagement-simulator-upload/. /opt/stakeholder-engagement-simulator/
sudo chown -R mohammedthameem:www-data /opt/stakeholder-engagement-simulator

# If pyproject.toml / uv.lock changed:
cd /opt/stakeholder-engagement-simulator/backend && sudo -u mohammedthameem uv sync

# If alembic/versions/ has new migrations:
# NOTE: alembic does not read .env — pass DATABASE_URL explicitly.
cd /opt/stakeholder-engagement-simulator/backend && sudo -u mohammedthameem \
  DATABASE_URL="$(grep '^DATABASE_URL=' /opt/stakeholder-engagement-simulator/.env | cut -d= -f2-)" \
  uv run alembic upgrade head

# If you re-ran scripts/build_persona_config.py or world chunks changed:
cd /opt/stakeholder-engagement-simulator/backend && sudo -u mohammedthameem -E uv run python scripts/embed_and_load.py

# If frontend changed:
cd /opt/stakeholder-engagement-simulator/frontend
sudo -u mohammedthameem npm ci
sudo -u mohammedthameem npm run build
sudo -u mohammedthameem rm -rf /opt/stakeholder-engagement-simulator/backend/static
sudo -u mohammedthameem cp -R dist /opt/stakeholder-engagement-simulator/backend/static

sudo systemctl restart stakeholder-engagement-simulator
sudo systemctl status stakeholder-engagement-simulator
```

---

## Upgrading an existing install to authenticated access (migration 0005)

> **Historical.** This applies only to an install still at migration `0004`. Its
> step 1 used local registration, which was removed with Entra SSO (`0010`).
> Such an install must first be brought to `0005` with a build from before
> `0010`, then upgraded normally. On `0010`, legacy accounts are linked to
> their Entra identity by address on first SSO sign-in.

**Do this once, and read it before running `alembic upgrade head`.** The usual
"migrate, then restart" order breaks this particular upgrade in two ways:

- `0005` makes `interview_sessions.user_id` `NOT NULL`. Between the migration and
  the restart, the *old* build is still running and its INSERT omits that column,
  so every `POST /api/realtime/token` fails with a 500.
- `0005` needs a `users` row to assign the pre-auth sessions to, and user rows are
  only created through `POST /api/auth/set-password` — an endpoint of the app
  itself. Migrating first on an empty `users` table aborts the migration.

Correct sequence:

1. **With the current build still running**, create the account that will inherit
   the old interview data — register and set a password through the web UI. Any
   `@wpi.edu` address on `AUTH_REGISTRATION_ALLOWLIST` works.

2. **Stop the service.** Roughly a minute of downtime is the simple, correct
   answer here; the alternative (a temporary column default) can silently
   mis-attribute any session created during the window.

   ```bash
   sudo systemctl stop stakeholder-engagement-simulator
   ```

3. Deploy the new code as in section 2 above (rsync, `uv sync`, `npm run build`,
   copy `dist` → `backend/static`) — but **do not** run the migration line yet.

4. Run the migration with both variables set explicitly:

   ```bash
   cd /opt/stakeholder-engagement-simulator/backend
   sudo -u mohammedthameem \
     DATABASE_URL="$(grep '^DATABASE_URL=' ../.env | cut -d= -f2-)" \
     LEGACY_SESSION_OWNER_EMAIL=youraccount@wpi.edu \
     uv run alembic upgrade head
   ```

   Confirm the line `0005: assigned N orphaned interview_sessions to youraccount@wpi.edu`.
   If the account cannot be found the migration aborts and rolls back — nothing is
   half-applied, so fix the address and re-run.

5. Start the service and confirm an anonymous request is now refused:

   ```bash
   sudo systemctl start stakeholder-engagement-simulator
   curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     -H 'Content-Type: application/json' -d '{"persona_id":"alex_martinez"}' \
     https://stakeholder-engagement-simulator.wpi.edu/api/realtime/token   # expect 401
   curl -s -o /dev/null -w '%{http_code}\n' \
     https://stakeholder-engagement-simulator.wpi.edu/api/health           # expect 200
   ```

Also set `LEGACY_SESSION_OWNER_EMAIL=` back to empty (or remove it) afterwards —
it is read only by this migration and never by the application.

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

### Retention / deletion job

The schedule is enforced daily by `app/jobs/retention.py` (SR-2026-052
SEC-RET-001), configured in `.env`:

| Setting | Meaning |
|---|---|
| `RETENTION_TERM_END` | Last day of the term (YYYY-MM-DD, UTC). Empty = no course deletion yet. |
| `RETENTION_COURSE_GRACE_DAYS` | Days after term end before course data is deleted (default 30). |
| `RETENTION_TELEMETRY_DAYS` | Days RAG query telemetry is kept (default 30). |
| `RETENTION_RESEARCH_UNTIL` | The research protocol's end date. Empty = research data retained. |

Only records from on or before the term end are deleted, so a stale
`RETENTION_TERM_END` cannot touch the next term. Sessions under an **open**
incident flag are held until the flag is reviewed or purged.

Install the timer once:

```bash
sudo cp deploy/systemd/stakeholder-engagement-simulator-retention.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stakeholder-engagement-simulator-retention.timer
systemctl list-timers stakeholder-engagement-simulator-retention.timer
```

Check what a run would do before setting a term end for the first time:

```bash
cd /opt/stakeholder-engagement-simulator/backend
sudo -u mohammedthameem .venv/bin/python -m app.jobs.retention --dry-run   # reads the install .env
```

Every run (including dry runs and failures) writes a row to `deletion_log` and
an `admin.retention_run` audit event. A failed run exits non-zero, so the unit
shows `failed`:

```bash
sudo journalctl -u stakeholder-engagement-simulator-retention -n 50 --no-pager
psql ... -c "SELECT started_at, status, counts FROM deletion_log ORDER BY started_at DESC LIMIT 5;"
```

**Backups must expire on the same schedule** (SEC-RET-001: "backups expire on
the same schedule"). Backup retention is deployment configuration, not code:
set the backup rotation so that no backup outlives
`RETENTION_COURSE_GRACE_DAYS` after the term end. In practice, keep daily
backups for no longer than the grace period. Otherwise deleted course data
survives in backups. Record the rotation setting in the solution document.
Purges done through incident flags have the same backup caveat (see
[RUNBOOK.md](RUNBOOK.md#purging-a-flagged-session)).

### Security audit events

Security-relevant events are written as one JSON object per line on a dedicated
`ses.audit` logger, separate from the application log: sign-in and sign-out,
failed sign-ins, rate-limit trips, rejected sessions and CSRF checks, attempts
to reach another participant's session, and metadata for every AI call
(realtime session start, retrieval, scoring — model, prompt version, latency,
outcome). Each event names `actor_user_id` and `participant_id`. Events never
contain interview text, email addresses, or credentials.

Where they go is set in `.env`:

| `AUDIT_LOG_SINK` | Destination |
|---|---|
| `stdout` (default) | The service journal, alongside the application log |
| `syslog` | Syslog facility `auth`, tag `ses-audit`, at `AUDIT_SYSLOG_ADDRESS` — `/dev/log` (default) for the local daemon, or `host:port` (UDP) for a forwarder |
| `none` | Disabled — development only |

```bash
# Audit events only, from the journal (stdout sink)
sudo journalctl -u stakeholder-engagement-simulator -o cat | grep '"type":"audit"'
# Audit events only, from syslog (syslog sink)
sudo journalctl -t ses-audit -o cat
```

**Still to arrange outside this repository:** forwarding these events to the
institution's SIEM (point the local syslog forwarder, or `AUDIT_SYSLOG_ADDRESS`,
at the collector it provides), retention of at least 12 months there, and
cloud-provider audit logging for the AI account. None of the endpoint details
belong in this repository.

---

## Known gotchas

### Port collision with the old app

The old `interviewsimulator.service` already binds `127.0.0.1:8000`. This service binds `127.0.0.1:8001`. If you see `address already in use` for 8001, something else (or a stuck instance) is on 8001:

```bash
sudo ss -ltnp | grep 800
```

### Interview ends immediately: `stream_open_failed`

The audit log shows `ai.realtime_session` with `stage: stream_end`,
`reason: stream_open_failed` and an `error_type`. The backend could not open
the Nova Sonic stream. In order:

- `ProfileNotFoundError` / `NoCredentialsError`: `AWS_PROFILE` is not set for
  the service, or the profile is missing from the service user's
  `~/.aws/config`. Check with `sudo -u mohammedthameem AWS_PROFILE=ses-bedrock
  aws sts get-caller-identity`.
- `AccessDeniedException`: the `ses-bedrock` role lacks
  `bedrock:InvokeModelWithBidirectionalStream` on the Nova Sonic model, or
  model access is not enabled in `AWS_REGION`.
- Timeouts: outbound HTTPS to `bedrock-runtime.<region>.amazonaws.com` is
  blocked.

### Interview starts but no audio / disconnects at once

Apache is not proxying the WebSocket. Enable `mod_proxy_wstunnel` and make sure
the `ProxyPass /api/realtime/stream ws://…` line comes **before** the catch-all
`ProxyPass /` (see `deploy/apache/`). A browser console error on
`wss://…/api/realtime/stream` confirms it.

### `curl -I` on `/api/health` returns 405

Expected. `curl -I` sends `HEAD`; the route only handles `GET`. Use plain `curl`:

```bash
curl https://stakeholder-engagement-simulator.wpi.edu/api/health
```

### Stale frontend UI

The backend serves whatever is in `backend/static/`. If the UI looks old after a deploy, the static copy got skipped:

```bash
ls -la /opt/stakeholder-engagement-simulator/backend/static/   # check mtime
sudo -u mohammedthameem rm -rf /opt/stakeholder-engagement-simulator/backend/static
cd /opt/stakeholder-engagement-simulator/frontend && sudo -u mohammedthameem npm run build
sudo -u mohammedthameem cp -R dist /opt/stakeholder-engagement-simulator/backend/static
sudo systemctl restart stakeholder-engagement-simulator
```

### `alembic upgrade head` fails with "no such relation"

Usually means the database hasn't been created yet, or `DATABASE_URL` in `.env` points somewhere unreachable. Re-run step 3 (create db + extension), then verify with `psql`.

### Never overwrite `/opt/stakeholder-engagement-simulator/.env`

The rsync commands above exclude `.env` for exactly this reason. If you ever do an unconditional copy, back up the server `.env` first.
