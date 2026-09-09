"""Auth endpoint tests.

These run against the local database configured by DATABASE_URL. Every test
uses an address under TEST_PREFIX and the fixture removes those rows either
side of each test, so a failed run cannot poison the next one.

Fixtures and DB helpers live in conftest.py / db.py so test_authz.py can share
them.
"""

from __future__ import annotations

import pytest

from app.config import settings
from tests.conftest import GOOD_PASSWORD, TEMP
from tests.conftest import register as _register
from tests.conftest import set_password as _set_password
from tests.db import TEST_PREFIX
from tests.db import scalar as _scalar
from tests.db import sql as _sql

# --- registration -----------------------------------------------------------


def test_register_creates_pending_row_not_a_user(client, email):
    assert _register(client, email).json() == {"status": "sent"}
    assert _scalar("SELECT count(*) FROM pending_registrations WHERE email=%s", (email,)) == 1
    assert _scalar("SELECT count(*) FROM users WHERE email=%s", (email,)) == 0


def test_register_rejects_non_institutional_domain(client):
    r = _register(client, f"{TEST_PREFIX}x@northeastern.edu")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_domain"


def test_register_is_case_insensitive_about_the_address(client, email):
    _register(client, email.upper())
    assert _scalar("SELECT count(*) FROM pending_registrations WHERE email=%s", (email,)) == 1


def test_repeat_registration_replaces_rather_than_duplicates(client, email):
    _register(client, email)
    _sql("UPDATE pending_registrations SET attempts=4 WHERE email=%s", (email,))
    _register(client, email)
    assert _scalar("SELECT count(*) FROM pending_registrations WHERE email=%s", (email,)) == 1
    assert _scalar("SELECT attempts FROM pending_registrations WHERE email=%s", (email,)) == 0


def test_registering_an_existing_account_looks_identical(client, email):
    _register(client, email)
    _set_password(client, email)
    r = _register(client, email)
    # Same body as a fresh registration, and no new pending row was opened.
    assert r.status_code == 200 and r.json() == {"status": "sent"}
    assert _scalar(
        "SELECT count(*) FROM pending_registrations WHERE email=%s AND consumed_at IS NULL",
        (email,),
    ) == 0


# --- set-password -----------------------------------------------------------


def test_set_password_creates_account_consumes_registration_and_signs_in(client, email):
    _register(client, email)
    r = _set_password(client, email)

    assert r.status_code == 200
    assert r.json()["email"] == email
    assert r.json()["first_name"] == "Alex"
    assert settings.auth_cookie_name in r.cookies
    assert _scalar("SELECT count(*) FROM users WHERE email=%s", (email,)) == 1
    assert _scalar(
        "SELECT consumed_at IS NOT NULL FROM pending_registrations WHERE email=%s", (email,)
    ) is True


def test_temp_password_ignores_dashes_and_case(client, email):
    _register(client, email)
    assert _set_password(client, email, temp=TEMP.replace("-", "").lower()).status_code == 200


def test_wrong_temp_password_is_401_and_counts_the_attempt(client, email):
    _register(client, email)
    r = _set_password(client, email, temp="AAA-BBBB-CC")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_temp_password"
    assert _scalar("SELECT attempts FROM pending_registrations WHERE email=%s", (email,)) == 1
    assert _scalar("SELECT count(*) FROM users WHERE email=%s", (email,)) == 0


def test_expired_temp_password_is_rejected(client, email):
    _register(client, email)
    _sql(
        "UPDATE pending_registrations SET expires_at = now() - interval '1 hour' WHERE email=%s",
        (email,),
    )
    r = _set_password(client, email)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "expired"


def test_attempt_cap_locks_the_registration(client, email):
    _register(client, email)
    _sql(
        "UPDATE pending_registrations SET attempts=%s WHERE email=%s",
        (settings.auth_max_temp_password_attempts, email),
    )
    r = _set_password(client, email)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "too_many_attempts"


@pytest.mark.parametrize(
    "password,missing",
    [
        ("Short1!", f"{settings.auth_min_password_length}+ characters"),
        ("alllowercase1!", "One uppercase letter"),
        ("NoNumbersHere!!", "One number"),
        ("NoSymbolsHere123", "One symbol"),
    ],
)
def test_password_policy_is_enforced_server_side(client, email, password, missing):
    _register(client, email)
    r = _set_password(client, email, new=password)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "weak_password"
    assert missing in r.json()["detail"]["failed"]
    assert _scalar("SELECT count(*) FROM users WHERE email=%s", (email,)) == 0


def test_set_password_without_a_registration_is_rejected(client, email):
    r = _set_password(client, email)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "no_pending_registration"


# --- login / me / logout ----------------------------------------------------


def test_login_succeeds_and_me_returns_the_user(client, email):
    _register(client, email)
    _set_password(client, email)
    client.cookies.clear()

    r = client.post("/api/auth/login", json={"email": email, "password": GOOD_PASSWORD})
    assert r.status_code == 200
    assert settings.auth_cookie_name in r.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_login_is_case_insensitive_about_the_address(client, email):
    _register(client, email)
    _set_password(client, email)
    client.cookies.clear()
    r = client.post(
        "/api/auth/login", json={"email": email.upper(), "password": GOOD_PASSWORD}
    )
    assert r.status_code == 200


def test_wrong_password_and_unknown_account_are_indistinguishable(client, email):
    _register(client, email)
    _set_password(client, email)
    client.cookies.clear()

    wrong = client.post("/api/auth/login", json={"email": email, "password": "Wrong!12345678"})
    unknown = client.post(
        "/api/auth/login",
        json={"email": f"{TEST_PREFIX}nobody@wpi.edu", "password": "Wrong!12345678"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_inactive_account_cannot_sign_in(client, email):
    _register(client, email)
    _set_password(client, email)
    _sql("UPDATE users SET is_active=false WHERE email=%s", (email,))
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": GOOD_PASSWORD})
    assert r.status_code == 401


def test_remember_sets_a_persistent_cookie(client, email):
    _register(client, email)
    _set_password(client, email)
    client.cookies.clear()

    plain = client.post("/api/auth/login", json={"email": email, "password": GOOD_PASSWORD})
    assert "max-age" not in plain.headers["set-cookie"].lower()

    client.cookies.clear()
    remembered = client.post(
        "/api/auth/login",
        json={"email": email, "password": GOOD_PASSWORD, "remember": True},
    )
    header = remembered.headers["set-cookie"].lower()
    assert f"max-age={settings.auth_session_remember_days * 86400}" in header


def test_session_cookie_is_httponly_and_lax(client, email):
    _register(client, email)
    r = _set_password(client, email)
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_me_without_a_cookie_is_401(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_a_forged_token(client):
    client.cookies.set(settings.auth_cookie_name, "not-a-real-token")
    assert client.get("/api/auth/me").status_code == 401


def test_logout_invalidates_the_session_server_side(client, email):
    _register(client, email)
    _set_password(client, email)
    assert client.get("/api/auth/me").status_code == 200

    token = client.cookies.get(settings.auth_cookie_name)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    # Replaying the old token must fail even though the client discarded it.
    client.cookies.set(settings.auth_cookie_name, token)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_without_a_session_is_still_ok(client):
    assert client.post("/api/auth/logout").status_code == 200


# --- rate limiting ----------------------------------------------------------


def test_login_attempts_are_rate_limited(client, email):
    _register(client, email)
    _set_password(client, email)
    client.cookies.clear()

    codes = [
        client.post(
            "/api/auth/login", json={"email": email, "password": "Wrong!12345678"}
        ).status_code
        for _ in range(15)
    ]
    assert 429 in codes
    assert codes.index(429) >= 5, "cut in implausibly early"


def test_registration_is_rate_limited_per_address(client, email):
    codes = [_register(client, email).status_code for _ in range(8)]
    assert 429 in codes


# --- registration allowlist -------------------------------------------------


@pytest.fixture
def allowlist(monkeypatch):
    """Reconfigure the allowlist for one test.

    Settings are cached for the process, so the cache has to be dropped either
    side — otherwise the override leaks into whatever runs next.
    """
    from app.config import get_settings

    def _apply(value: str, mode: str = "fixed"):
        monkeypatch.setenv("AUTH_REGISTRATION_ALLOWLIST", value)
        monkeypatch.setenv("AUTH_TEMP_PASSWORD_MODE", mode)
        get_settings.cache_clear()

    yield _apply
    get_settings.cache_clear()


def _pending_rows(email: str) -> int:
    return _scalar("SELECT count(*) FROM pending_registrations WHERE email=%s", (email,))


def test_address_off_the_allowlist_is_blocked_but_looks_accepted(client, email, allowlist):
    allowlist(f"{TEST_PREFIX}someone-else@wpi.edu")
    r = _register(client, email)
    # Indistinguishable from success — the caller learns nothing.
    assert r.status_code == 200 and r.json() == {"status": "sent"}
    assert _pending_rows(email) == 0


def test_address_on_the_allowlist_is_accepted(client, email, allowlist):
    allowlist(email)
    _register(client, email)
    assert _pending_rows(email) == 1


def test_domain_wildcard_admits_the_whole_domain(client, email, allowlist):
    allowlist("*@wpi.edu")
    _register(client, email)
    assert _pending_rows(email) == 1


def test_empty_allowlist_admits_nobody_while_the_password_is_fixed(client, email, allowlist):
    allowlist("")
    assert _register(client, email).json() == {"status": "sent"}
    assert _pending_rows(email) == 0


def test_allowlist_is_not_consulted_once_passwords_are_random(client, email, allowlist):
    # With a per-registration password, knowing the constant is worthless, so
    # the gate is no longer needed.
    allowlist("", mode="random")
    _register(client, email)
    assert _pending_rows(email) == 1
