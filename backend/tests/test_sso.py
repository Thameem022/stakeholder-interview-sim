"""SR-2026-052 item 1.3 (SEC-IAM-001): Entra ID single sign-on.

Drives the real flow — /api/auth/login, Entra, /api/auth/callback — against an
in-process fake Entra (tests/fake_entra.py) that checks PKCE and signs ID
tokens, then tampers with every claim the app must verify.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import oidc
from app.config import Settings, check_sso, get_settings, settings
from tests import fake_entra
from tests.conftest import create_account
from tests.db import participant_of, scalar, sql
from tests.test_audit import audit_log  # noqa: F401  (fixture)

RETURN_TO = "/__sso_test/score/abc"


@pytest.fixture
def entra(monkeypatch):
    fake = fake_entra.FakeEntra()
    s = get_settings()
    monkeypatch.setattr(s, "entra_tenant_id", fake_entra.TENANT)
    monkeypatch.setattr(s, "entra_client_id", fake_entra.CLIENT_ID)
    monkeypatch.setattr(s, "entra_client_secret", "test-secret")
    monkeypatch.setattr(s, "entra_redirect_uri", fake_entra.REDIRECT_URI)
    monkeypatch.setattr(s, "entra_authority", fake_entra.AUTHORITY)
    monkeypatch.setattr(s, "entra_require_app_role", True)
    monkeypatch.setattr(oidc, "_get_json", fake.get_json)
    monkeypatch.setattr(oidc, "_post_form", fake.post_form)
    oidc.reset_caches()
    yield fake
    oidc.reset_caches()


def _start(client, return_to: str = RETURN_TO):
    r = client.get("/api/auth/login", params={"return_to": return_to}, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r


def sign_in(client, entra, *, email: str | None = None, start=None, **claims):
    """The whole round trip. Returns the callback response."""
    email = email or f"sis-test-{uuid.uuid4().hex[:10]}@wpi.edu"
    login = start or _start(client)
    code, state = entra.approve(login.headers["location"], email=email, **claims)
    return client.get(
        "/api/auth/callback", params={"code": code, "state": state}, follow_redirects=False
    )


def _error(r) -> str | None:
    loc = r.headers.get("location", "")
    q = parse_qs(urlsplit(loc).query)
    return q.get("error", [None])[0] if urlsplit(loc).path == "/login" else None


def _session_cookie(r) -> bool:
    return any(
        settings.auth_cookie_name in h and "Max-Age=0" not in h
        for h in r.headers.get_list("set-cookie")
    )


# --- starting a sign-in ------------------------------------------------------------------


def test_login_sends_the_browser_to_entra_with_pkce(client, entra):
    r = _start(client)
    url = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == entra.discovery()["authorization_endpoint"]
    assert q["client_id"] == fake_entra.CLIENT_ID
    assert q["redirect_uri"] == fake_entra.REDIRECT_URI
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert "openid" in q["scope"].split()
    assert len(q["state"]) >= 32 and len(q["nonce"]) >= 32 and len(q["code_challenge"]) == 43
    binding = next(h for h in r.headers.get_list("set-cookie") if h.startswith("sis_oidc="))
    assert "HttpOnly" in binding and "Path=/api/auth" in binding


def test_without_entra_configuration_nobody_can_sign_in(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "entra_client_id", "")
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302 and _error(r) == "sso_not_configured"


@pytest.mark.parametrize("evil", ["//evil.example/x", "https://evil.example", "\\\\evil", "javascript:alert(1)"])
def test_return_to_cannot_leave_the_site(client, entra, evil):
    r = sign_in(client, entra, start=_start(client, return_to=evil))
    assert r.headers["location"] == "/"


# --- completing it -----------------------------------------------------------------------


def test_a_first_sign_in_creates_an_account_and_a_pseudonym(client, entra, audit_log):  # noqa: F811
    email = f"sis-test-{uuid.uuid4().hex[:10]}@wpi.edu"
    r = sign_in(client, entra, email=email, roles=["Student"])

    assert r.status_code == 302 and r.headers["location"] == RETURN_TO
    assert _session_cookie(r)
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == email

    user_id = me.json()["id"]
    pid = participant_of(user_id)
    assert pid and pid != user_id
    assert scalar("SELECT entra_subject IS NOT NULL FROM identity.users WHERE id = %s", (user_id,))

    (event,) = [e for e in audit_log.named("auth.login") if e["outcome"] == "success"]
    assert event["method"] == "sso" and event["participant_id"] == pid
    assert email not in "\n".join(audit_log.lines)


def test_the_session_resolves_to_the_pseudonym_for_student_work(client, entra, monkeypatch):
    """Sign in, then start an interview: the work lands on the pseudonym."""
    import httpx

    from app.realtime.notice import NOTICE_VERSION

    async def _ok(*a, **k):
        return httpx.Response(200, json={"value": "ek_test"})

    sign_in(client, entra)
    user_id = client.get("/api/auth/me").json()["id"]
    monkeypatch.setattr(httpx.AsyncClient, "post", _ok)
    r = client.post("/api/realtime/token",
                    json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION})
    assert r.status_code == 200, r.text
    owner = scalar("SELECT participant_id::text FROM interview_sessions WHERE id = %s",
                   (r.json()["session_id"],))
    assert owner == participant_of(user_id)


def test_signing_in_again_resumes_the_same_pseudonym(client, entra):
    email = f"sis-test-{uuid.uuid4().hex[:10]}@wpi.edu"
    sign_in(client, entra, email=email)
    first = participant_of(client.get("/api/auth/me").json()["id"])
    client.cookies.clear()
    sign_in(client, entra, email=email)
    assert participant_of(client.get("/api/auth/me").json()["id"]) == first
    assert scalar("SELECT count(*) FROM identity.users WHERE email = %s", (email,)) == 1


def test_an_account_from_before_sso_is_linked_and_keeps_its_work(client, entra, owned_session):
    email = f"sis-test-{uuid.uuid4().hex[:10]}@wpi.edu"
    legacy = create_account(email)
    sql("UPDATE identity.users SET entra_subject = NULL WHERE id = %s", (legacy,))
    sid = owned_session(legacy)

    sign_in(client, entra, email=email)
    me = client.get("/api/auth/me").json()
    assert me["id"] == legacy
    assert client.get(f"/api/export/sessions/{sid}").status_code == 200


def test_app_roles_come_from_entra_and_are_replaced_each_sign_in(client, entra):
    email = f"sis-test-{uuid.uuid4().hex[:10]}@wpi.edu"
    sign_in(client, entra, email=email, roles=["Instructor", "StudyPersonnel", "NotARole"])
    user_id = client.get("/api/auth/me").json()["id"]
    roles = lambda: scalar(  # noqa: E731
        "SELECT array_agg(role ORDER BY role) FROM identity.account_roles WHERE user_id = %s",
        (user_id,),
    )
    assert roles() == ["instructor", "study_personnel"]
    assert client.get("/api/research/records").status_code == 200

    client.cookies.clear()
    sign_in(client, entra, email=email, roles=["Student"])
    assert roles() is None
    assert client.get("/api/research/records").status_code == 403


def test_a_disabled_account_cannot_sign_in(client, entra):
    email = f"sis-test-{uuid.uuid4().hex[:10]}@wpi.edu"
    user = create_account(email)
    sql("UPDATE identity.users SET is_active = false WHERE id = %s", (user,))
    r = sign_in(client, entra, email=email,
                oid=scalar("SELECT entra_subject FROM identity.users WHERE id = %s", (user,)))
    assert _error(r) == "account_disabled" and not _session_cookie(r)


# --- what Entra must vouch for ------------------------------------------------------------


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ({"iss": "https://login.microsoftonline.com/other-tenant/v2.0"}, "invalid_token"),
        ({"aud": "someone-elses-app"}, "invalid_token"),
        ({"exp": int(datetime.now(timezone.utc).timestamp()) - 300}, "invalid_token"),
        ({"nbf": int(datetime.now(timezone.utc).timestamp()) + 3600}, "invalid_token"),
        ({"nonce": "a-nonce-from-another-sign-in"}, "invalid_token"),
        ({"tid": "99999999-9999-9999-9999-999999999999"}, "invalid_token"),
        ({"oid": ...}, "invalid_token"),
        ({"exp": ...}, "invalid_token"),
    ],
    ids=["issuer", "audience", "expired", "not-yet-valid", "nonce", "tenant", "no-oid", "no-exp"],
)
def test_a_token_with_a_bad_claim_is_refused(client, entra, audit_log, tamper, expected):  # noqa: F811
    entra.token_overrides = tamper
    r = sign_in(client, entra)
    assert _error(r) == expected and not _session_cookie(r)
    assert client.get("/api/auth/me").status_code == 401
    (event,) = audit_log.named("auth.login")
    assert event["outcome"] == "failure" and event["reason"] == expected


@pytest.mark.parametrize("forgery", ["other-key", "unknown-kid", "hs256", "none"])
def test_a_forged_or_unsigned_token_is_refused(client, entra, forgery):
    if forgery == "other-key":
        entra.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif forgery == "unknown-kid":
        entra.header_overrides = {"kid": "not-a-published-key"}
    else:
        entra.header_overrides = {"alg": forgery.upper() if forgery == "hs256" else "none"}
    r = sign_in(client, entra)
    assert _error(r) == "invalid_token" and not _session_cookie(r)


def test_only_the_institutional_domain_is_accepted(client, entra):
    r = sign_in(client, entra, email="someone@gmail.com")
    assert _error(r) == "wrong_domain" and not _session_cookie(r)


def test_a_user_with_no_app_role_assignment_is_refused(client, entra):
    r = sign_in(client, entra, roles=[])
    assert _error(r) == "not_assigned" and not _session_cookie(r)


def test_the_role_requirement_can_be_relaxed_for_development(client, entra, monkeypatch):
    monkeypatch.setattr(get_settings(), "entra_require_app_role", False)
    assert _session_cookie(sign_in(client, entra, roles=[]))


# --- the flow itself ------------------------------------------------------------------------


def test_a_callback_is_single_use(client, entra):
    login = _start(client)
    code, state = entra.approve(login.headers["location"], email="sis-test-once@wpi.edu")
    first = client.get("/api/auth/callback", params={"code": code, "state": state},
                       follow_redirects=False)
    assert _session_cookie(first)
    client.cookies.clear()
    replay = client.get("/api/auth/callback", params={"code": code, "state": state},
                        follow_redirects=False)
    assert _error(replay) == "unknown_or_expired_state"


def test_a_callback_from_a_different_browser_is_refused(client, entra):
    """Login CSRF: the victim's browser never started this sign-in."""
    login = _start(client)
    code, state = entra.approve(login.headers["location"], email="sis-test-x@wpi.edu")
    client.cookies.clear()  # the callback URL opened in another browser
    r = client.get("/api/auth/callback", params={"code": code, "state": state},
                   follow_redirects=False)
    assert _error(r) == "browser_mismatch" and not _session_cookie(r)


def test_an_expired_sign_in_is_refused(client, entra):
    login = _start(client)
    sql("UPDATE identity.oidc_logins SET expires_at = %s WHERE return_to = %s",
        (datetime.now(timezone.utc) - timedelta(seconds=1), RETURN_TO))
    code, state = entra.approve(login.headers["location"], email="sis-test-late@wpi.edu")
    r = client.get("/api/auth/callback", params={"code": code, "state": state},
                   follow_redirects=False)
    assert _error(r) == "unknown_or_expired_state"


def test_an_entra_refusal_is_reported_not_crashed(client, entra):
    r = client.get("/api/auth/callback", params={"error": "access_denied", "state": "x"},
                   follow_redirects=False)
    assert _error(r) == "idp_access_denied"


def test_the_pkce_verifier_reaches_the_token_endpoint(client, entra):
    sign_in(client, entra)
    (req,) = entra.token_requests
    assert req["grant_type"] == "authorization_code" and len(req["code_verifier"]) >= 43
    assert req["client_secret"] == "test-secret"


def test_logout_ends_the_app_session(client, entra):
    sign_in(client, entra)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_the_session_cookie_is_httponly_and_lax(client, entra):
    r = sign_in(client, entra)
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith(settings.auth_cookie_name))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age" not in cookie  # a browser-session cookie


def test_me_without_or_with_a_forged_cookie_is_401(client):
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set(settings.auth_cookie_name, "forged")
    assert client.get("/api/auth/me").status_code == 401


# --- no local password path remains ------------------------------------------------------------

_APP = Path(__file__).resolve().parents[1] / "app"
_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_no_password_endpoints_exist(client):
    from app.main import app

    paths = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    for gone in ("/api/auth/register", "/api/auth/set-password"):
        assert not any(p == gone for _, p in paths)
    assert ("POST", "/api/auth/login") not in paths
    assert client.post("/api/auth/login", json={"email": "a@wpi.edu", "password": "x"}).status_code == 405


@pytest.mark.parametrize("root", [_APP, _FRONTEND])
def test_no_password_code_remains(root):
    if not root.is_dir():
        pytest.skip(f"{root} not checked out")
    banned = re.compile(
        r"password_hash|hash_password|verify_password|temp_password|set-password|"
        r"setPassword|argon2|/auth/register|registerAccount|AUTH_DEV_TEMP",
        re.I,
    )
    hits = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*")
        if p.suffix in {".py", ".ts", ".tsx"}
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if banned.search(line)
    ]
    assert hits == [], f"local-password code still present: {hits}"


def test_identity_schema_holds_no_credential():
    assert scalar(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'identity' "
        "AND column_name ILIKE '%%password%%'"
    ) == 0


# --- production configuration -----------------------------------------------------------------


def _prod(**over) -> Settings:
    base = dict(
        environment="prod", entra_tenant_id="t", entra_client_id="c", entra_client_secret="s",
        entra_redirect_uri="https://ses.example/api/auth/callback",
        entra_authority="https://login.microsoftonline.com",
    )
    return Settings(**{**base, **over})


def test_production_accepts_a_complete_entra_configuration():
    check_sso(_prod())


@pytest.mark.parametrize(
    "over,match",
    [
        ({"entra_client_secret": ""}, "ENTRA_CLIENT_SECRET"),
        ({"entra_tenant_id": ""}, "ENTRA_TENANT_ID"),
        ({"entra_authority": "http://127.0.0.1:9999"}, "ENTRA_AUTHORITY"),
        ({"entra_redirect_uri": "http://ses.example/api/auth/callback"}, "https"),
    ],
)
def test_production_refuses_an_unsafe_entra_configuration(over, match):
    with pytest.raises(RuntimeError, match=match):
        check_sso(_prod(**over))
