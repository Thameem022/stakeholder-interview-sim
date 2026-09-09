import logging
import os
from functools import lru_cache
from pathlib import Path

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

    # "dev" or "prod". Gates cookie Secure and the fixed-temp-password guard.
    environment: str = "dev"

    # Auth
    auth_email_domain: str = "wpi.edu"
    auth_cookie_name: str = "sis_session"
    auth_session_remember_days: int = 30
    auth_session_default_hours: int = 12
    # How stale last_seen_at may get before an authenticated request refreshes
    # it. Purely informational, so throttling costs nothing but saves ~100
    # single-row updates per interview.
    auth_session_touch_interval_seconds: int = 60
    auth_min_password_length: int = 12
    auth_temp_password_ttl_hours: int = 24
    auth_max_temp_password_attempts: int = 10

    # "fixed" hands every registration the same known constant below. That is
    # only viable while no email is sent; get_settings() refuses to boot with
    # it in production. "random" generates a real one per registration.
    auth_temp_password_mode: str = "fixed"
    auth_dev_temp_password: str = "7QF-42KD-XM"

    # Comma-separated addresses permitted to register while the fixed
    # temporary password is active. An entry is either a full address or a
    # domain wildcard ("*@wpi.edu"). An empty list permits nobody, so opening
    # registration up is always a deliberate act rather than a default.
    auth_registration_allowlist: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "prod"

    @property
    def registration_allowlist(self) -> set[str]:
        return {
            e.strip().lower()
            for e in self.auth_registration_allowlist.split(",")
            if e.strip()
        }

    def registration_permitted(self, email: str) -> bool:
        """Whether `email` may open a registration.

        Only consulted while the temporary password is the fixed constant —
        with a per-registration password there is nothing to gate, because
        knowing the constant is no longer enough to claim someone's address.
        """
        if self.auth_temp_password_mode != "fixed":
            return True
        email = email.strip().lower()
        allowed = self.registration_allowlist
        return email in allowed or f"*@{email.rpartition('@')[2]}" in allowed


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # Bridge loaded values into os.environ so libraries that read directly
    # (langchain ChatOpenAI, openai SDK, scorers using os.getenv) all see them.
    # Do not overwrite values the user already set in their shell.
    if s.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = s.openai_api_key

    # A fixed temp password plus open @wpi.edu registration means anyone who
    # knows the constant can claim any address. Fail the boot rather than warn:
    # a warning scrolls past, a refused start does not.
    if s.is_production and s.auth_temp_password_mode == "fixed":
        raise RuntimeError(
            "AUTH_TEMP_PASSWORD_MODE=fixed cannot run with ENVIRONMENT=prod: "
            "every account would share one known temporary password. Set "
            "AUTH_TEMP_PASSWORD_MODE=random (once email delivery exists) or "
            "run with ENVIRONMENT=dev."
        )

    if s.auth_temp_password_mode == "fixed":
        log = logging.getLogger(__name__)
        allowed = sorted(s.registration_allowlist)
        log.warning(
            "Auth is using the FIXED development temporary password. "
            "Registration allowlist: %s",
            ", ".join(allowed) or "(empty)",
        )
        if not allowed:
            # Not fatal: the rest of the app is unrelated to auth and should
            # still run. But registration silently accepting nobody is worth
            # saying out loud, because the endpoint stays deliberately quiet.
            log.warning(
                "No AUTH_REGISTRATION_ALLOWLIST set, so no address can register. "
                "Set it to specific addresses, or to '*@%s' to allow the whole "
                "domain.",
                s.auth_email_domain,
            )

    return s


class _SettingsProxy:
    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


settings = _SettingsProxy()
