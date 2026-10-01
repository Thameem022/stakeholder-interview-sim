import logging
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    """Look for .env in backend/ first, then the repo root (one level up).

    Without this, starting uvicorn from `backend/` silently ignores the root
    `.env` and OPENAI_API_KEY ends up empty.
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

    openai_api_key: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sis"
    openai_realtime_model: str = "gpt-realtime"
    embedding_model: str = "text-embedding-3-small"
    port: int = 8000

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    check_cors_origins(s)
    check_sso(s)
    # Bridge loaded values into os.environ so libraries that read directly
    # (langchain ChatOpenAI, openai SDK, scorers using os.getenv) all see them.
    # Do not overwrite values the user already set in their shell.
    if s.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = s.openai_api_key

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
