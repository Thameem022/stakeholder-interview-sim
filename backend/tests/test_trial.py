""""Try SES" guest trial: off by default, and fenced in once on.

Guest rows live under @trial.invalid rather than TEST_PREFIX, so this module
deletes exactly the guests it created (by id) instead of widening the shared
cleanup — the test database is the development database.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings, settings
from tests.db import scalar, sql

_created: list[str] = []


@pytest.fixture
def trial_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "trial_enabled", True)
    yield
    for uid in _created:
        sql("DELETE FROM interview_sessions WHERE user_id = %s", (uid,))
        sql("DELETE FROM users WHERE id = %s AND is_trial", (uid,))
    _created.clear()


@pytest.fixture
def no_openai(monkeypatch):
    async def _no_network(*args, **kwargs):
        raise httpx.ConnectError("blocked in tests")

    monkeypatch.setattr(httpx.AsyncClient, "post", _no_network)


def _start(client, fresh_browser: bool = True) -> dict:
    if fresh_browser:
        client.cookies.clear()
    r = client.post("/api/trial/start")
    assert r.status_code == 200, r.text
    body = r.json()
    _created.append(body["user"]["id"])
    return body


def test_trial_is_off_by_default(client):
    assert client.post("/api/trial/start").status_code == 404


def test_start_signs_in_a_flagged_guest(client, trial_on):
    body = _start(client)
    assert body["briefing"]["persona_id"] == "alex_martinez"
    assert body["briefing"]["target_chunk_id"] == "alex_m_t3_threshold_gap"
    assert body["interview_minutes"] == settings.trial_interview_minutes

    me = client.get("/api/auth/me").json()
    assert me["is_trial"] is True
    assert me["email"].endswith("@trial.invalid")

    lifetime = scalar(
        "SELECT extract(epoch FROM expires_at - created_at) FROM auth_sessions "
        "WHERE user_id = %s",
        (body["user"]["id"],),
    )
    assert lifetime <= settings.trial_session_minutes * 60 + 5


def test_regular_users_are_not_trial(logged_in_client):
    client, _ = logged_in_client
    assert client.get("/api/auth/me").json()["is_trial"] is False


def test_guest_address_cannot_be_signed_into(client, trial_on):
    body = _start(client)
    client.cookies.clear()
    r = client.post(
        "/api/auth/login", json={"email": body["user"]["email"], "password": ""}
    )
    assert r.status_code == 401


def test_start_is_rate_limited_per_ip(client, trial_on):
    for _ in range(5):
        _start(client)
    client.cookies.clear()
    assert client.post("/api/trial/start").status_code == 429


def test_global_cap(client, trial_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "trial_daily_cap", 1)
    _start(client)
    client.cookies.clear()
    assert client.post("/api/trial/start").status_code == 429


# --- one trial per browser ----------------------------------------------------


def _interviewed(uid: str, started: str = "now()") -> str:
    """A guest interview with one saved turn, i.e. a trial actually used."""
    return scalar(
        f"""
        INSERT INTO interview_sessions
            (id, user_id, persona_id, voice_id, started_at, transcript)
        VALUES (gen_random_uuid(), %s, 'alex_martinez', 'sage', {started},
                '[{{"role": "user", "text": "hi", "timestamp": "2026-01-01T00:00:00Z"}}]'::jsonb)
        RETURNING id::text
        """,
        (uid,),
    )


def test_a_browser_gets_one_trial(client, trial_on):
    uid = _start(client)["user"]["id"]
    _interviewed(uid)
    client.post("/api/auth/logout")  # what closing the score report does
    r = client.post("/api/trial/start")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "trial_used"


def test_an_attempt_that_never_got_going_does_not_spend_the_trial(client, trial_on):
    """Bad API key, microphone denied, dropped network: no turn was saved."""
    first = _start(client)["user"]["id"]
    sql(
        """
        INSERT INTO interview_sessions
            (id, user_id, persona_id, voice_id, started_at, transcript)
        VALUES (gen_random_uuid(), %s, 'alex_martinez', 'sage', now(), '[]'::jsonb)
        """,
        (first,),
    )
    client.post("/api/auth/logout")
    second = _start(client, fresh_browser=False)["user"]["id"]
    assert second != first


def test_a_dead_guest_is_replaced_rather_than_resumed(client, trial_on):
    """Out of time without ever speaking: resuming would hand back a trial
    that refuses every call."""
    first = _start(client)["user"]["id"]
    _expired_session(first)
    second = _start(client, fresh_browser=False)["user"]["id"]
    assert second != first


def test_a_refresh_resumes_the_same_trial(client, trial_on):
    first = _start(client)
    again = _start(client, fresh_browser=False)
    assert again["user"]["id"] == first["user"]["id"]
    assert scalar(
        "SELECT count(*) FROM users WHERE id = %s", (first["user"]["id"],)
    ) == 1


def test_the_marker_survives_the_guest_session_expiring(client, trial_on):
    uid = _start(client)["user"]["id"]
    _interviewed(uid)
    sql("UPDATE auth_sessions SET expires_at = now() WHERE user_id = %s", (uid,))
    assert client.post("/api/trial/start").status_code == 409


def test_a_student_with_a_used_marker_is_still_refused(client, trial_on, email):
    from tests.conftest import register, set_password

    _interviewed(_start(client)["user"]["id"])
    marker = client.cookies.get("ses_trial")
    register(client, email)
    assert set_password(client, email).status_code == 200
    client.cookies.set("ses_trial", marker, path="/api/trial")
    assert client.post("/api/trial/start").status_code == 409


def test_guest_is_held_to_alex_and_marked(client, trial_on, no_openai):
    uid = _start(client)["user"]["id"]
    r = client.post("/api/realtime/token", json={"persona_id": "sarah_donnelly"})
    assert r.status_code == 502  # stubbed OpenAI; the row is written before it
    assert scalar(
        "SELECT persona_id FROM interview_sessions WHERE user_id = %s", (uid,)
    ) == "alex_martinez"
    assert scalar(
        "SELECT metadata->>'trial' FROM interview_sessions WHERE user_id = %s", (uid,)
    ) == "true"
    # The cap is timed from started_at, so it must be on the database clock.
    assert scalar(
        "SELECT abs(extract(epoch FROM now() - started_at)) < 60 "
        "FROM interview_sessions WHERE user_id = %s",
        (uid,),
    )


def test_guest_mint_attempts_are_capped(client, trial_on, no_openai):
    _start(client)
    for _ in range(3):
        assert client.post(
            "/api/realtime/token", json={"persona_id": "alex_martinez"}
        ).status_code == 502
    r = client.post("/api/realtime/token", json={"persona_id": "alex_martinez"})
    assert r.status_code == 409


def _expired_session(uid: str) -> str:
    sid = scalar(
        """
        INSERT INTO interview_sessions
            (id, user_id, persona_id, voice_id, started_at, transcript)
        VALUES (gen_random_uuid(), %s, 'alex_martinez', 'sage',
                now() - interval '11 minutes', '[]'::jsonb)
        RETURNING id::text
        """,
        (uid,),
    )
    return sid


def test_turns_are_refused_after_the_cap(client, trial_on):
    uid = _start(client)["user"]["id"]
    sid = _expired_session(uid)

    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": sid, "role": "user", "text": "one more"},
    )
    assert r.status_code == 410

    r = client.post(
        "/api/realtime/retrieve",
        json={"persona_id": "alex_martinez", "query": "thresholds", "session_id": sid},
    )
    assert r.status_code == 410

    r = client.post(
        "/api/realtime/token", json={"persona_id": "alex_martinez"}
    )
    assert r.status_code == 410


def test_closing_call_is_accepted_after_the_cap_without_a_turn(client, trial_on):
    uid = _start(client)["user"]["id"]
    sid = _expired_session(uid)
    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": sid, "role": "user", "text": "sneaked", "ended": True},
    )
    assert r.status_code == 200
    assert r.json()["turns"] == 0
    assert scalar(
        "SELECT ended_at IS NOT NULL FROM interview_sessions WHERE id = %s", (sid,)
    )


def test_regular_user_is_not_time_capped(logged_in_client):
    client, uid = logged_in_client
    sid = scalar(
        """
        INSERT INTO interview_sessions
            (id, user_id, persona_id, voice_id, started_at, transcript)
        VALUES (gen_random_uuid(), %s, 'alex_martinez', 'sage',
                now() - interval '2 hours', '[]'::jsonb)
        RETURNING id::text
        """,
        (uid,),
    )
    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": sid, "role": "user", "text": "still here"},
    )
    assert r.status_code == 200


# --- trial-only persona instructions -----------------------------------------


@pytest.fixture
def sent_instructions(monkeypatch):
    """Capture the instructions mint_token would send to OpenAI."""
    seen: list[str] = []

    async def _capture(self, url, **kwargs):
        seen.append(kwargs["json"]["session"]["instructions"])
        raise httpx.ConnectError("blocked in tests")

    monkeypatch.setattr(httpx.AsyncClient, "post", _capture)
    return seen


def test_guests_get_the_trust_gated_alex_prompt(client, trial_on, sent_instructions):
    _start(client)
    client.post("/api/realtime/token", json={"persona_id": "alex_martinez"})
    assert "TRIAL SESSION: RELOCATION AND THE THRESHOLD GAP" in sent_instructions[0]


def test_students_keep_the_standard_alex_prompt(logged_in_client, sent_instructions):
    client, _ = logged_in_client
    client.post("/api/realtime/token", json={"persona_id": "alex_martinez"})
    assert sent_instructions, "expected a mint attempt"
    assert "TRIAL SESSION" not in sent_instructions[0]
