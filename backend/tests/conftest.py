"""Test-wide environment and shared fixtures.

The environment line must run before any test module imports `app`, which
pytest guarantees for conftest. While the fixed temporary password is active,
registration is closed unless an allowlist says otherwise — so the suite opens
the domain explicitly, exactly as a developer would in their own .env.
"""

from __future__ import annotations

import os

os.environ.setdefault("AUTH_REGISTRATION_ALLOWLIST", "*@wpi.edu")

import uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from tests.db import TEST_PREFIX, cleanup_test_rows, sql  # noqa: E402

TEMP = settings.auth_dev_temp_password
GOOD_PASSWORD = "Harbortown!2026x"


@pytest.fixture
def client():
    cleanup_test_rows()
    # The context manager runs the lifespan, which is what initialises the
    # asyncpg pool the endpoints rely on.
    with TestClient(app) as c:
        yield c
    cleanup_test_rows()


@pytest.fixture
def email() -> str:
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}@wpi.edu"


def register(client: TestClient, email: str):
    return client.post(
        "/api/auth/register",
        json={"first_name": "Alex", "last_name": "Rivera", "email": email},
    )


def set_password(client: TestClient, email: str, temp=TEMP, new=GOOD_PASSWORD):
    return client.post(
        "/api/auth/set-password",
        json={"email": email, "temp_password": temp, "new_password": new},
    )


def sign_in_as(client: TestClient, email: str, password: str = GOOD_PASSWORD) -> str:
    """Swap the client's identity, returning the new session cookie.

    Identity is swapped on the one client rather than by building a second
    TestClient: `init_pool` writes a module-level global, so two concurrent
    lifespans would fight over the same `_pool`.
    """
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return client.cookies.get(settings.auth_cookie_name)


@pytest.fixture
def logged_in_client(client, email):
    """A TestClient carrying a real session cookie. Yields (client, user_id)."""
    register(client, email)
    r = set_password(client, email)
    assert r.status_code == 200, r.text
    yield client, r.json()["id"]


@pytest.fixture
def owned_session():
    """Insert an interview_sessions row directly for a given owner.

    Sidesteps /api/realtime/token so eval and transcript tests never need a
    network call to OpenAI.
    """

    def _make(user_id: str, transcript: str = "[]") -> uuid.UUID:
        sid = uuid.uuid4()
        sql(
            """
            INSERT INTO interview_sessions
                (id, user_id, persona_id, voice_id, started_at, transcript)
            VALUES (%s, %s, 'alex_martinez', 'alloy', now(), %s::jsonb)
            """,
            (str(sid), user_id, transcript),
        )
        return sid

    return _make
