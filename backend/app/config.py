import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    """Look for .env in backend/ first, then the repo root (one level up).

    Without this, starting uvicorn from `backend/` silently ignores the root
    `.env` and every setting silently falls back to its default.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    candidates = [backend_dir / ".env", backend_dir.parent / ".env"]
    for c in candidates:
        if c.is_file():
            return str(c)
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sis"
    port: int = 8000

    # --- AI: Amazon Bedrock, direct (SR-2026-052 SEC-AI-001) ------------------
    # Credentials come ONLY from the standard AWS chain: an assumed role, STS,
    # IAM Roles Anywhere (credential_process), or vault-issued temporary
    # credentials. Never a key in .env, in source, or in the browser.
    aws_region: str = "us-east-1"
    # Rubric scoring: Claude on Bedrock (Messages API endpoint, official SDK).
    bedrock_scoring_model: str = "anthropic.claude-opus-5-5"
    # Used when the primary errors or declines (client-side refusal fallback).
    bedrock_scoring_fallback_model: str = "anthropic.claude-sonnet-5-5"
    # The cosmetic per-tier "consequence" text on the coverage report.
    bedrock_enrichment_model: str = "anthropic.claude-sonnet-5-5"
    # Thinking depth for scoring. Opus 5.5 accepts no temperature; effort is
    # fixed here so runs are comparable, and recorded with every evaluation.
    bedrock_scoring_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    # Retrieval embeddings: Amazon Titan Text Embeddings V2.
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    bedrock_embedding_dimensions: int = 1024
    # Live voice persona: Amazon Nova Sonic, bidirectional stream via the backend.
    bedrock_speech_model_id: str = "amazon.nova-sonic-v1:0"
    # Nova Sonic caps one stream's lifetime; the proxy renews before this many
    # seconds, carrying the conversation over, at the next turn boundary.
    nova_sonic_stream_renew_seconds: int = 420
    realtime_max_session_minutes: int = 30
    # Written interviews (SR-2026-052 item 2.1): the same persona, in a typed
    # chat, played by Claude on Bedrock. Replies are conversational, so effort
    # is low; a declined reply is retried on the fallback model.
    bedrock_text_persona_model: str = "anthropic.claude-opus-5-5"
    bedrock_text_persona_fallback_model: str = "anthropic.claude-sonnet-5-5"
    bedrock_text_persona_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # Bedrock Guardrails, applied to persona speech, student turns and feedback.
    # Required in production.
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = "DRAFT"

    # "dev" or "prod". Gates cookie Secure and the production boot checks.
    environment: str = "dev"

    # Comma-separated origins allowed to call the API cross-origin with
    # credentials. Production serves the SPA from the API's own origin, so it
    # needs none; empty in development falls back to the Vite dev server.
    cors_allow_origins: str = ""

    # Double-submit CSRF cookie. Readable by the page on purpose — the frontend
    # echoes it back in X-CSRF-Token, which a cross-site form cannot do.
    auth_csrf_cookie_name: str = "sis_csrf"

    # Research participation (IRB-27-0033). OFF until the organizational gate
    # clears: an approved FERPA consent form, a DPIA, and Data Governance / OGC
    # sign-off. While off, no consent is asked for and nothing is copied into
    # the research store. Production refuses to enable it with draft consent
    # text (see app/research/consent.py).
    research_enabled: bool = False
    # How long a named approver's export approval stays usable.
    research_export_approval_hours: int = 72

    # Retention schedule (SEC-RET-001), enforced by app/jobs/retention.py.
    # Course data (interviews, feedback, and the sign-in <-> pseudonym mapping)
    # from a term is deleted RETENTION_COURSE_GRACE_DAYS after that term's end.
    # Empty term end = no course deletion yet. Only records from on or before
    # the term end are touched, so a forgotten setting never eats a new term.
    retention_term_end: str = ""
    retention_course_grace_days: int = 30
    # RAG query telemetry is operational: a short window, any time of term.
    retention_telemetry_days: int = 30
    # Research copies and consent: kept until the protocol's end date (empty =
    # retained under the protocol; the job never touches them).
    retention_research_until: str = ""

    # Backups (SEC-BCK-001), taken daily by app/jobs/backup.py. Encrypted to
    # BACKUP_PUBLIC_KEY (an OpenPGP public key file); the matching private key
    # is held offline, never on this server. A backup is kept BACKUP_KEEP_DAYS,
    # capped at the shortest retention window above, so nothing the retention
    # job deletes survives in a backup for longer than that window again.
    backup_dir: str = "/var/backups/stakeholder-engagement-simulator"
    backup_public_key: str = ""
    backup_keep_days: int = 14

    # Security audit events (JSON lines on the "ses.audit" logger). "stdout"
    # lands in the service journal; "syslog" sends to AUDIT_SYSLOG_ADDRESS (a
    # socket path, or host:port for a forwarder); "none" disables them.
    audit_log_sink: str = "stdout"
    audit_syslog_address: str = "/dev/log"

    # Auth
    auth_email_domain: str = "wpi.edu"
    auth_cookie_name: str = "sis_session"
    auth_session_remember_days: int = 30
    auth_session_default_hours: int = 12
    # How stale last_seen_at may get before an authenticated request refreshes
    # it. Purely informational, so throttling costs nothing but saves ~100
    # single-row updates per interview.
    auth_session_touch_interval_seconds: int = 60

    # Microsoft Entra ID (OIDC) — the only way to sign in (SEC-IAM-001).
    # Tenant and client ids identify the app registration; they are not
    # secrets, but are deployment config and never committed with real values.
    # The client secret is injected from the vault / service environment.
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    # The callback registered in Entra, e.g. https://<host>/api/auth/callback.
    entra_redirect_uri: str = ""
    # Microsoft's endpoint. Development may point this at the local mock
    # provider (scripts/dev_oidc_provider.py); production refuses anything else.
    entra_authority: str = "https://login.microsoftonline.com"
    # Defence in depth on top of Entra's "assignment required": a token with no
    # recognised app role (Student, Instructor, ...) is refused.
    entra_require_app_role: bool = True

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "prod"

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]
        if not origins and not self.is_production:
            return [_DEV_FRONTEND_ORIGIN]
        return origins

    @property
    def csrf_cookie_name(self) -> str:
        # __Host- makes the browser refuse the cookie unless it is Secure,
        # host-only and path=/, so a sibling subdomain cannot plant one. It
        # needs HTTPS, hence production only.
        name = self.auth_csrf_cookie_name
        return f"__Host-{name}" if self.is_production else name

    @property
    def sso_configured(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_client_id and self.entra_redirect_uri)


_DEV_FRONTEND_ORIGIN = "http://localhost:5173"
ENTRA_AUTHORITY = "https://login.microsoftonline.com"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"}


def check_cors_origins(s: Settings) -> None:
    """Refuse a production boot that would trust a loopback origin.

    Any page served from the user's own machine — a dev server, a local tool —
    could otherwise make credentialed calls against production.
    """
    if not s.is_production:
        return
    for origin in s.cors_origins:
        if urlsplit(origin).hostname in _LOOPBACK_HOSTS or origin == "*":
            raise RuntimeError(
                f"CORS_ALLOW_ORIGINS contains {origin!r}, which cannot run with "
                "ENVIRONMENT=prod. Production is same-origin; leave it empty or "
                "list the production origin only."
            )


def check_sso(s: Settings) -> None:
    """Production signs in through Microsoft's Entra endpoint, over HTTPS, with
    every setting present — or it does not start."""
    if not s.is_production:
        return
    missing = [
        name for name, value in (
            ("ENTRA_TENANT_ID", s.entra_tenant_id),
            ("ENTRA_CLIENT_ID", s.entra_client_id),
            ("ENTRA_CLIENT_SECRET", s.entra_client_secret),
            ("ENTRA_REDIRECT_URI", s.entra_redirect_uri),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"ENVIRONMENT=prod needs {', '.join(missing)} for Entra sign-in.")
    if s.entra_authority.rstrip("/") != ENTRA_AUTHORITY:
        raise RuntimeError(
            f"ENTRA_AUTHORITY={s.entra_authority!r} cannot run with ENVIRONMENT=prod; "
            f"production signs in only through {ENTRA_AUTHORITY}."
        )
    if not s.entra_redirect_uri.startswith("https://"):
        raise RuntimeError("ENTRA_REDIRECT_URI must be https:// in production.")


def check_aws(s: Settings) -> None:
    """Production gets AWS credentials from a role or the vault, never a stored
    key, and always has a guardrail. A long-term access key (an access key id
    without a session token) in the environment refuses the boot."""
    if not s.is_production:
        return
    if os.environ.get("AWS_ACCESS_KEY_ID") and not os.environ.get("AWS_SESSION_TOKEN"):
        raise RuntimeError(
            "A long-term AWS access key is set in the environment. Production must "
            "use short-lived credentials (role / STS / vault); see deploy/WPI_DEPLOY.md."
        )
    if not s.bedrock_guardrail_id:
        raise RuntimeError("ENVIRONMENT=prod needs BEDROCK_GUARDRAIL_ID.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    check_cors_origins(s)
    check_sso(s)
    check_aws(s)

    if not s.sso_configured:
        logging.getLogger(__name__).warning(
            "Entra sign-in is not configured (ENTRA_TENANT_ID / ENTRA_CLIENT_ID / "
            "ENTRA_REDIRECT_URI); nobody can sign in. For local development, run "
            "scripts/dev_oidc_provider.py and point ENTRA_AUTHORITY at it."
        )

    return s


class _SettingsProxy:
    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


settings = _SettingsProxy()
