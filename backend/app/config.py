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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # Bridge loaded values into os.environ so libraries that read directly
    # (langchain ChatOpenAI, openai SDK, scorers using os.getenv) all see them.
    # Do not overwrite values the user already set in their shell.
    if s.openai_api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = s.openai_api_key
    return s


class _SettingsProxy:
    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


settings = _SettingsProxy()
