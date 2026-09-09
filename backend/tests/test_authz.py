"""Authorization: who may call what, and whose data they see.

Nothing here needs OpenAI. Sessions are inserted directly via the
`owned_session` fixture, and every ownership case is rejected before any
scorer would run.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from tests.conftest import GOOD_PASSWORD, register, set_password, sign_in_as
from tests.db import scalar, sql

# (method, path, json) for every route that must require a session.
PROTECTED_ROUTES = [
    ("GET", "/api/personas", None),
    ("GET", "/api/voices", None),
    ("POST", "/api/eval/iqr?session_id=" + str(uuid.uuid4()), None),
    ("POST", "/api/eval/sic?session_id=" + str(uuid.uuid4()), None),
    ("GET", f"/api/eval/sessions/{uuid.uuid4()}/latest", None),
    ("POST", "/api/realtime/token", {"persona_id": "alex_martinez"}),
    ("POST", "/api/realtime/retrieve", {"persona_id": "alex_martinez", "query": "x"}),
    (
        "POST",
        "/api/realtime/transcript",
        {"session_id": str(uuid.uuid4()), "role": "user", "text": "hi"},
    ),
]


def _call(client, method: str, path: str, body):
    return client.get(path) if method == "GET" else client.post(path, json=body)


# --- the gate ---------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", PROTECTED_ROUTES)
def test_every_protected_route_rejects_an_anonymous_caller(client, method, path, body):
    """The highest-value test here: it fails when a new route ships unguarded."""
    client.cookies.clear()
    r = _call(client, method, path, body)
    assert r.status_code == 401, f"{method} {path} was reachable anonymously"
    assert r.json()["detail"]["code"] == "not_authenticated"


def test_auth_is_checked_before_the_request_body(client):
    """A malformed body must not turn a 401 into a 422.

    FastAPI resolves dependencies before validating the body, so this holds
    today — it is pinned because a framework upgrade could silently invert it
    and start leaking "this route exists and your payload was wrong".
    """
    client.cookies.clear()
    r = client.post("/api/realtime/token", json={"nonsense": True})
    assert r.status_code == 401


def test_a_forged_cookie_is_rejected(client):
    client.cookies.set(settings.auth_cookie_name, "not-a-real-token")
    assert client.get("/api/personas").status_code == 401


def test_health_stays_public(client):
    """The deploy probe must not need credentials."""
    client.cookies.clear()
    assert client.get("/api/health").status_code == 200


def test_a_signed_in_caller_gets_through(logged_in_client):
    client, _ = logged_in_client
    assert client.get("/api/personas").status_code == 200


# --- ownership --------------------------------------------------------------


@pytest.fixture
def two_users(client, logged_in_client):
    """User A (signed in, owns nothing yet) and a second registered user B."""
    _, user_a = logged_in_client
    cookie_a = client.cookies.get(settings.auth_cookie_name)

    email_b = f"sis-test-{uuid.uuid4().hex[:12]}@wpi.edu"
    register(client, email_b)
    r = set_password(client, email_b)
    assert r.status_code == 200, r.text
    user_b = r.json()["id"]

    # Leave the client signed in as A.
    client.cookies.clear()
    client.cookies.set(settings.auth_cookie_name, cookie_a)
    return client, user_a, email_b, user_b


def test_another_users_evaluation_is_not_readable(two_users, owned_session):
    client, user_a, email_b, _ = two_users
    sid = owned_session(user_a)
    sql(
        "INSERT INTO session_evaluations (session_id, evaluation) "
        "VALUES (%s, %s::jsonb)",
        (str(sid), '{"overall_score": 9}'),
    )

    assert client.get(f"/api/eval/sessions/{sid}/latest").status_code == 200

    sign_in_as(client, email_b, GOOD_PASSWORD)
    r = client.get(f"/api/eval/sessions/{sid}/latest")
    # 404 rather than 403 on purpose: a 403 would confirm the id names a real
    # session. It also keeps 401 meaning only "your session is gone", which is
    # what the frontend's redirect logic relies on.
    assert r.status_code == 404


def test_another_users_session_cannot_be_scored(two_users, owned_session):
    client, user_a, email_b, _ = two_users
    sid = owned_session(user_a, transcript='[{"role":"user","text":"hi","timestamp":"t"}]')

    sign_in_as(client, email_b, GOOD_PASSWORD)
    r = client.post(f"/api/eval/iqr?session_id={sid}")
    # Rejected before any scorer runs, so this never reaches OpenAI.
    assert r.status_code == 404
    assert scalar(
        "SELECT count(*) FROM session_evaluations WHERE session_id = %s", (str(sid),)
    ) == 0


def test_another_users_transcript_cannot_be_appended_to(two_users, owned_session):
    client, user_a, email_b, _ = two_users
    original = '[{"role": "user", "text": "mine", "timestamp": "2026-01-01T00:00:00Z"}]'
    sid = owned_session(user_a, transcript=original)

    sign_in_as(client, email_b, GOOD_PASSWORD)
    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": str(sid), "role": "user", "text": "injected"},
    )
    assert r.status_code == 404

    after = scalar("SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid),))
    assert "injected" not in after
    assert "mine" in after


def test_owner_can_append_to_their_own_session(logged_in_client, owned_session):
    client, user_a = logged_in_client
    sid = owned_session(user_a)
    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": str(sid), "role": "user", "text": "hello"},
    )
    assert r.status_code == 200 and r.json()["turns"] == 1


def test_ending_a_session_is_sticky(logged_in_client, owned_session):
    """A turn arriving after the interview ended must not reopen it."""
    client, user_a = logged_in_client
    sid = owned_session(user_a)
    body = {"session_id": str(sid), "role": "user", "text": "bye", "ended": True}
    client.post("/api/realtime/transcript", json=body)
    ended_first = scalar("SELECT ended_at FROM interview_sessions WHERE id = %s", (str(sid),))
    assert ended_first is not None

    client.post(
        "/api/realtime/transcript",
        json={"session_id": str(sid), "role": "assistant", "text": "late turn"},
    )
    assert scalar(
        "SELECT ended_at FROM interview_sessions WHERE id = %s", (str(sid),)
    ) == ended_first


def test_token_request_does_not_accept_a_session_id():
    """The field that made the wipe possible is gone from the schema."""
    from app.realtime.token import TokenRequest

    assert "session_id" not in TokenRequest.model_fields


def test_token_mint_cannot_target_an_existing_session(
    two_users, owned_session, monkeypatch
):
    """Regression for the transcript-wipe.

    session_id used to be fed straight into an upsert, so minting with someone
    else's id replaced their transcript with an empty one. The request below
    still sends the field, because a stale frontend bundle would: Pydantic
    ignores unknown fields, so it must reach nothing.

    OpenAI is stubbed out. The session row is written before that call, so
    everything under test here still happens — and the suite stays offline and
    free to run.
    """
    import httpx

    async def _no_network(*args, **kwargs):
        raise httpx.ConnectError("blocked in tests")

    monkeypatch.setattr(httpx.AsyncClient, "post", _no_network)

    client, user_a, email_b, _ = two_users
    original = '[{"role": "user", "text": "mine", "timestamp": "2026-01-01T00:00:00Z"}]'
    sid = owned_session(user_a, transcript=original)

    sign_in_as(client, email_b, GOOD_PASSWORD)
    r = client.post(
        "/api/realtime/token",
        json={"persona_id": "alex_martinez", "session_id": str(sid)},
    )
    assert r.status_code == 502, "expected the stubbed OpenAI call to fail the mint"

    assert "mine" in scalar(
        "SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid),)
    )
    assert scalar(
        "SELECT user_id::text FROM interview_sessions WHERE id = %s", (str(sid),)
    ) == user_a


# --- metering ---------------------------------------------------------------


def test_scoring_is_rate_limited_per_user(logged_in_client):
    """Limits apply before ownership, so probing costs the attacker budget."""
    client, _ = logged_in_client
    missing = uuid.uuid4()
    codes = [
        client.post(f"/api/eval/iqr?session_id={missing}").status_code
        for _ in range(22)
    ]
    assert 429 in codes, "expected the per-user eval limit to trip"
    assert codes[0] == 404, "an unknown session should 404 before it 429s"
    assert client.post(f"/api/eval/iqr?session_id={missing}").json()["detail"]["code"] == (
        "rate_limited"
    )


# --- session touch throttling -----------------------------------------------


def test_last_seen_at_is_not_rewritten_on_every_request(logged_in_client):
    client, _ = logged_in_client
    client.get("/api/personas")
    first = scalar("SELECT max(last_seen_at) FROM auth_sessions")

    client.get("/api/personas")
    assert scalar("SELECT max(last_seen_at) FROM auth_sessions") == first

    # Backdate past the throttle window and it refreshes again.
    sql("UPDATE auth_sessions SET last_seen_at = now() - interval '10 minutes'")
    client.get("/api/personas")
    assert scalar("SELECT max(last_seen_at) FROM auth_sessions") > first
