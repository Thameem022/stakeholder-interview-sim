"""Shared fixtures.

Sign-in is Entra ID only, so most tests do not go through it: they create an
account (and its pseudonym) directly and attach a real app session cookie.
tests/test_sso.py drives the actual sign-in flow against a fake Entra.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth.csrf import CSRF_HEADER, SAFE_METHODS
from app.auth.sessions import generate_session_token, hash_session_token
from app.config import settings
from app.main import app
from tests.db import TEST_PREFIX, cleanup_test_rows, scalar, sql


class CSRFTestClient(TestClient):
    """Behaves like the real frontend: echoes the CSRF cookie on every write.

    Without this, every POST in the suite would need to carry the header by
    hand. Set `csrf_auto = False` to send requests the way a cross-site
    attacker would — without the header.
    """

    csrf_auto = True

    def request(self, method, url, **kwargs):
        if self.csrf_auto and method.upper() not in SAFE_METHODS:
            name = settings.csrf_cookie_name
            # Look the cookie up by iterating: the jar may hold it under the
            # server's domain, and cookies.get() raises if a second copy with
            # a different domain were ever added.
            token = next((c.value for c in self.cookies.jar if c.name == name), None)
            if token is None:
                token = "test-csrf-token"
                self.cookies.set(name, token)
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault(CSRF_HEADER, token)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def client():
    cleanup_test_rows()
    # The context manager runs the lifespan, which is what initialises the
    # asyncpg pool the endpoints rely on.
    with CSRFTestClient(app) as c:
        yield c
    cleanup_test_rows()


@pytest.fixture
def email() -> str:
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}@wpi.edu"


def create_account(email: str, first: str = "Alex", last: str = "Rivera") -> str:
    """An Entra-backed account and its pseudonym, as a first sign-in would make
    them. Returns the account id."""
    pid = scalar("INSERT INTO participants DEFAULT VALUES RETURNING participant_id::text")
    return scalar(
        """
        INSERT INTO identity.users (email, first_name, last_name, participant_id, entra_subject)
        VALUES (%s, %s, %s, %s, %s) RETURNING id::text
        """,
        (email, first, last, pid, str(uuid.uuid4())),
    )


def sign_in_as(client: TestClient, email: str) -> str:
    """Swap the client's identity to `email`'s account, returning the cookie.

    Issues a real app session, exactly what a completed Entra sign-in issues.
    Identity is swapped on the one client rather than by building a second
    TestClient: `init_pool` writes a module-level global, so two concurrent
    lifespans would fight over the same `_pool`.
    """
    token = generate_session_token()
    sql(
        """
        INSERT INTO identity.auth_sessions (user_id, token_hash, expires_at)
        SELECT id, %s, %s FROM identity.users WHERE email = %s
        """,
        (hash_session_token(token), datetime.now(timezone.utc) + timedelta(hours=12), email),
    )
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, token)
    return token


def enroll(client: TestClient, *roles: str, email: str | None = None) -> tuple[str, str]:
    """Create an account (with app roles) and sign the client in as it.
    Returns (email, account id)."""
    email = email or f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}@wpi.edu"
    user_id = create_account(email)
    for role in roles:
        sql(
            "INSERT INTO identity.account_roles (user_id, role, granted_by) VALUES (%s, %s, 'test')",
            (user_id, role),
        )
    sign_in_as(client, email)
    return email, user_id


@pytest.fixture
def logged_in_client(client, email):
    """A TestClient carrying a real session cookie. Yields (client, user_id)."""
    _, user_id = enroll(client, email=email)
    yield client, user_id


@pytest.fixture
def owned_session():
    """Insert an interview_sessions row directly for a given owner.

    Sidesteps /api/realtime/token so eval and transcript tests never need a
    network call to OpenAI.
    """

    def _make(user_id: str, transcript: str = "[]") -> uuid.UUID:
        # Takes the account id the auth fixtures hand out; the row is keyed by
        # that account's pseudonym, as the app would key it.
        sid = uuid.uuid4()
        sql(
            """
            INSERT INTO interview_sessions
                (id, participant_id, persona_id, voice_id, started_at, transcript)
            VALUES (%s, (SELECT participant_id FROM identity.users WHERE id = %s),
                    'alex_martinez', 'alloy', now(), %s::jsonb)
            """,
            (str(sid), user_id, transcript),
        )
        return sid

    return _make
