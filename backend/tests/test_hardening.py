"""SR-2026-052 section 4: retrieval ownership, CSRF, and CORS.

Retrieval tests stub the embedding and search calls, so nothing here reaches
OpenAI or depends on the loaded corpora.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.csrf import CSRF_HEADER
from app.config import Settings, check_cors_origins, settings
from tests.conftest import GOOD_PASSWORD, register, set_password, sign_in_as
from tests.db import scalar

# --- 4.1 retrieval is bound to the caller's own session ----------------------


@pytest.fixture
def stub_retrieval(monkeypatch):
    """Replace embedding + search with stubs; record whether they ran."""
    import app.realtime.retrieve as retrieve

    calls: list[str] = []

    async def _embed(query):
        calls.append("embed")
        return [0.0]

    async def _search_persona(persona_id, query, k, query_vec):
        calls.append("persona")
        return [{"text": "persona chunk", "chunk_id": "p1", "score": 0.9}]

    async def _search_world(query, k, query_vec):
        calls.append("world")
        return []

    monkeypatch.setattr(retrieve, "embed_one", _embed)
    monkeypatch.setattr(retrieve, "search_persona", _search_persona)
    monkeypatch.setattr(retrieve, "search_world", _search_world)
    return calls


def _retrieve(client, sid, persona="alex_martinez", query="budget?"):
    return client.post(
        "/api/realtime/retrieve",
        json={"persona_id": persona, "query": query, "session_id": str(sid)},
    )


def test_owner_can_retrieve_within_their_own_session(
    logged_in_client, owned_session, stub_retrieval
):
    client, user_a = logged_in_client
    sid = owned_session(user_a)
    r = _retrieve(client, sid)
    assert r.status_code == 200, r.text
    assert "persona chunk" in r.json()["text"]


def test_retrieval_against_another_users_session_is_refused(
    client, logged_in_client, owned_session, stub_retrieval
):
    _, user_a = logged_in_client
    sid = owned_session(user_a)

    email_b = f"sis-test-{uuid.uuid4().hex[:12]}@wpi.edu"
    register(client, email_b)
    assert set_password(client, email_b).status_code == 200
    sign_in_as(client, email_b, GOOD_PASSWORD)

    r = _retrieve(client, sid)
    # 404, not 403: must not confirm the id names a real session.
    assert r.status_code == 404
    # Refused before any embedding, search or telemetry happened.
    assert stub_retrieval == []
    assert scalar(
        "SELECT count(*) FROM retrieval_events WHERE session_id = %s", (str(sid),)
    ) == 0


def test_retrieval_for_an_unknown_session_is_404(logged_in_client, stub_retrieval):
    client, _ = logged_in_client
    assert _retrieve(client, uuid.uuid4()).status_code == 404
    assert stub_retrieval == []


def test_retrieval_requires_a_session_id(logged_in_client, stub_retrieval):
    client, _ = logged_in_client
    r = client.post(
        "/api/realtime/retrieve", json={"persona_id": "alex_martinez", "query": "x"}
    )
    assert r.status_code == 422
    assert stub_retrieval == []


def test_retrieval_cannot_switch_persona_mid_session(
    logged_in_client, owned_session, stub_retrieval
):
    client, user_a = logged_in_client
    sid = owned_session(user_a)  # alex_martinez
    assert _retrieve(client, sid, persona="someone_else").status_code == 400
    assert stub_retrieval == []


# --- 4.2 CSRF ---------------------------------------------------------------


def test_first_response_issues_a_readable_csrf_cookie(client):
    client.cookies.clear()
    r = client.get("/api/health")
    set_cookie = r.headers.get("set-cookie", "")
    assert settings.csrf_cookie_name in set_cookie
    # The page must be able to read it to echo it back.
    assert "httponly" not in set_cookie.lower()


def test_write_without_csrf_header_is_rejected(logged_in_client, owned_session):
    client, user_a = logged_in_client
    sid = owned_session(user_a)
    client.csrf_auto = False
    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": str(sid), "role": "user", "text": "forged"},
        # What a cross-site page would send: our cookies, a foreign Origin, and
        # no way to read the CSRF cookie to put it in a header.
        headers={"Origin": "https://attacker.example"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_failed"
    assert "forged" not in scalar(
        "SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid),)
    )


def test_write_with_mismatched_csrf_header_is_rejected(logged_in_client):
    client, _ = logged_in_client
    client.csrf_auto = False
    r = client.post(
        "/api/auth/logout", headers={CSRF_HEADER: "not-the-cookie-value"}
    )
    assert r.status_code == 403


def test_login_itself_needs_the_csrf_token(client, email):
    """Login CSRF: a cross-site page must not be able to sign the victim in."""
    register(client, email)
    assert set_password(client, email).status_code == 200
    client.cookies.clear()
    client.csrf_auto = False
    r = client.post("/api/auth/login", json={"email": email, "password": GOOD_PASSWORD})
    assert r.status_code == 403


def test_safe_methods_do_not_need_the_token(logged_in_client):
    client, _ = logged_in_client
    client.csrf_auto = False
    assert client.get("/api/personas").status_code == 200


# --- 4.2 CORS ---------------------------------------------------------------


def test_foreign_origin_gets_no_cors_grant(client):
    r = client.options(
        "/api/realtime/transcript",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": CSRF_HEADER,
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_production_has_no_cors_origins_by_default():
    assert Settings(environment="prod", cors_allow_origins="").cors_origins == []


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://127.0.0.1:5173", "http://[::1]:5173", "*"],
)
def test_production_refuses_to_boot_with_a_loopback_origin(origin):
    with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
        check_cors_origins(Settings(environment="prod", cors_allow_origins=origin))


def test_production_accepts_its_own_origin():
    s = Settings(
        environment="prod",
        cors_allow_origins="https://stakeholder-engagement-simulator.wpi.edu",
    )
    check_cors_origins(s)
    assert s.cors_origins == ["https://stakeholder-engagement-simulator.wpi.edu"]


def test_production_csrf_cookie_is_host_locked():
    assert Settings(environment="prod").csrf_cookie_name.startswith("__Host-")
