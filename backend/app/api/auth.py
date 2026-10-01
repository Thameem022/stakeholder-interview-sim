"""Sign-in through Microsoft Entra ID, and the app session that follows.

SR-2026-052 item 1.3 (SEC-IAM-001). There are no local accounts and no
passwords: GET /auth/login sends the browser to Entra, Entra sends it back to
GET /auth/callback with a code, and a verified ID token is resolved to the
person's pseudonymous participant before an app session cookie is issued.

Each sign-in is bound to the browser that started it: /login sets a
short-lived cookie whose hash is stored with the state, nonce and PKCE
verifier, and /callback refuses a state that arrives without it. A callback
URL forwarded to someone else's browser therefore signs nobody in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from app.auth import oidc
from app.auth.errors import auth_error as _fail
from app.auth.ratelimit import client_ip, enforce
from app.auth.sessions import (
    clear_session_cookie,
    create_session,
    delete_session,
    load_session_user,
    set_session_cookie,
    touch_session,
)
from app.config import settings
from app.db import get_pool
from app.observability.audit import audit

logger = logging.getLogger(__name__)

router = APIRouter()

_LOGIN_IP = (30, 900)
_CALLBACK_IP = (30, 900)
_FLOW_TTL = timedelta(minutes=10)
_BINDING_COOKIE = "sis_oidc"
_BINDING_PATH = "/api/auth"


def _user_payload(row) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
    }


def _safe_return_to(raw: Optional[str]) -> str:
    """Only same-site paths: "/score/x", never "//evil.example" or a URL."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw[:500]


def _to_login_page(code: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={quote(code)}", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(_BINDING_COOKIE, path=_BINDING_PATH)
    return response


@router.get("/auth/login")
async def login(request: Request, return_to: Optional[str] = None) -> RedirectResponse:
    """Start a sign-in: remember state/nonce/PKCE server-side, go to Entra."""
    if not settings.sso_configured:
        return _to_login_page("sso_not_configured")

    pool = await get_pool()
    async with pool.acquire() as conn:
        await enforce(conn, f"sso-login:ip:{client_ip(request)}", *_LOGIN_IP)
        state, nonce, binding = oidc.new_secret(), oidc.new_secret(), oidc.new_secret()
        verifier = oidc.new_code_verifier()
        await conn.execute(
            """
            INSERT INTO identity.oidc_logins
                (state_hash, binding_hash, nonce, code_verifier, return_to, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            oidc.sha256_hex(state), oidc.sha256_hex(binding), nonce, verifier,
            _safe_return_to(return_to), datetime.now(timezone.utc) + _FLOW_TTL,
        )

    try:
        url = await oidc.authorization_url(state=state, nonce=nonce, verifier=verifier)
    except Exception as e:
        logger.warning("Entra discovery failed: %s", type(e).__name__)
        return _to_login_page("sso_unavailable")

    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        _BINDING_COOKIE, binding, max_age=int(_FLOW_TTL.total_seconds()),
        httponly=True, secure=settings.is_production, samesite="lax", path=_BINDING_PATH,
    )
    return response


@router.get("/auth/callback")
async def callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
) -> RedirectResponse:
    """Finish a sign-in. Every failure lands on /login?error=<code>, audited."""

    def refuse(reason: str, user_id=None, participant_id=None) -> RedirectResponse:
        audit(
            "auth.login", "failure", actor_user_id=user_id, participant_id=participant_id,
            request=request, method="sso", reason=reason,
        )
        return _to_login_page(reason)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await enforce(conn, f"sso-callback:ip:{client_ip(request)}", *_CALLBACK_IP)

        if error:
            # Entra's own refusal (cancelled, not assigned, MFA failed, ...).
            return refuse("idp_" + "".join(c for c in error if c.isalnum() or c == "_")[:40])
        if not code or not state:
            return refuse("missing_code")

        # Single use: the row is consumed whether or not what follows succeeds.
        flow = await conn.fetchrow(
            "DELETE FROM identity.oidc_logins WHERE state_hash = $1 RETURNING *",
            oidc.sha256_hex(state),
        )
        if flow is None or flow["expires_at"] <= datetime.now(timezone.utc):
            return refuse("unknown_or_expired_state")
        binding = request.cookies.get(_BINDING_COOKIE)
        if not binding or oidc.sha256_hex(binding) != flow["binding_hash"]:
            return refuse("browser_mismatch")

        try:
            token = await oidc.exchange_code(code, flow["code_verifier"])
            claims = await oidc.validate_id_token(token, nonce=flow["nonce"])
            who = oidc.identity_from_claims(claims)
        except oidc.OIDCError as e:
            return refuse(e.code)
        except Exception as e:
            logger.warning("Entra sign-in failed: %s", type(e).__name__)
            return refuse("sso_unavailable")

        async with conn.transaction():
            user = await _resolve_account(conn, who)
            if user is None:
                return refuse("account_disabled")
            await _sync_roles(conn, user["id"], who.roles)
            token_value, _ = await create_session(conn, user["id"], remember=False)

    audit(
        "auth.login", "success", actor_user_id=user["id"], participant_id=user["participant_id"],
        request=request, method="sso", roles=sorted(who.roles),
    )
    response = RedirectResponse(flow["return_to"], status_code=status.HTTP_302_FOUND)
    response.delete_cookie(_BINDING_COOKIE, path=_BINDING_PATH)
    set_session_cookie(response, token_value, remember=False)
    return response


async def _resolve_account(conn, who: oidc.SignInIdentity):
    """The account for this Entra identity — and so its pseudonym, which is
    what lets a student resume across days. Created on first sign-in."""
    user = await conn.fetchrow(
        "SELECT id, participant_id, is_active FROM identity.users WHERE entra_subject = $1",
        who.subject,
    )
    if user is None:
        # An account from before SSO: linked once, by the institutional address
        # Entra vouches for, so its existing work stays with the student.
        user = await conn.fetchrow(
            """
            UPDATE identity.users SET entra_subject = $1
            WHERE email = $2 AND entra_subject IS NULL
            RETURNING id, participant_id, is_active
            """,
            who.subject, who.email,
        )
    if user is None:
        participant_id = await conn.fetchval(
            "INSERT INTO participants DEFAULT VALUES RETURNING participant_id"
        )
        user = await conn.fetchrow(
            """
            INSERT INTO identity.users (email, first_name, last_name, participant_id, entra_subject)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, participant_id, is_active
            """,
            who.email, who.first_name, who.last_name, participant_id, who.subject,
        )
    if not user["is_active"]:
        return None
    await conn.execute(
        """
        UPDATE identity.users
           SET email = $2, first_name = $3, last_name = $4,
               last_login_at = now(), updated_at = now()
         WHERE id = $1
        """,
        user["id"], who.email, who.first_name, who.last_name,
    )
    return user


async def _sync_roles(conn, user_id, roles: frozenset[str]) -> None:
    """Entra app-role assignments are the source of truth: replaced every sign-in."""
    await conn.execute("DELETE FROM identity.account_roles WHERE user_id = $1", user_id)
    for role in sorted(roles):
        await conn.execute(
            "INSERT INTO identity.account_roles (user_id, role, granted_by) VALUES ($1, $2, 'entra')",
            user_id, role,
        )


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await load_session_user(conn, token)
            await delete_session(conn, token)
        if row is not None:
            audit(
                "auth.logout", "success", actor_user_id=row["id"],
                participant_id=row["participant_id"], request=request,
            )
    clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/auth/me")
async def me(request: Request) -> dict:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise _fail(status.HTTP_401_UNAUTHORIZED, "not_authenticated", "Not signed in.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await load_session_user(conn, token)
        if row is None:
            raise _fail(
                status.HTTP_401_UNAUTHORIZED, "not_authenticated", "Not signed in."
            )
        await touch_session(conn, row["session_id"])

    return _user_payload(row)
