"""Account creation and login.

Registration is deliberately two-phase: `register` only writes a
pending_registrations row, and an actual users row appears when the temporary
password is exchanged for a real one in `set-password`. An address that never
completes step two never becomes an account.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.auth.errors import auth_error as _fail
from app.auth.passwords import (
    hash_password,
    issue_temp_password,
    normalize_temp_password,
    password_policy_failures,
    verify_password,
)
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

logger = logging.getLogger(__name__)

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Windows are in seconds. Every action is limited on both the address and the
# caller IP: the address limit stops one account being hammered, the IP limit
# stops one caller sweeping many addresses.
_REGISTER_EMAIL = (5, 3600)
_REGISTER_IP = (15, 3600)
_LOGIN_EMAIL = (10, 900)
_LOGIN_IP = (30, 900)
_SET_PASSWORD_EMAIL = (10, 3600)
_SET_PASSWORD_IP = (20, 3600)


class RegisterRequest(BaseModel):
    first_name: str = Field(max_length=100)
    last_name: str = Field(max_length=100)
    email: str = Field(max_length=254)


class SetPasswordRequest(BaseModel):
    email: str = Field(max_length=254)
    temp_password: str = Field(max_length=100)
    new_password: str = Field(max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    remember: bool = False


class UserResponse(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str


def _clean_email(raw: str) -> str:
    """Normalise and validate an address, enforcing the institutional domain.

    The mockup checks the domain in the browser too, but that is presentation:
    this is the check that decides who may register.
    """
    email = raw.strip().lower()
    if not _EMAIL_RE.match(email):
        raise _fail(
            status.HTTP_400_BAD_REQUEST, "invalid_email", "Enter a valid email address."
        )
    if not email.endswith("@" + settings.auth_email_domain.lower()):
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "invalid_domain",
            f"Registration is limited to @{settings.auth_email_domain} addresses.",
        )
    return email


def _user_payload(row) -> dict:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
    }


@router.post("/auth/register")
async def register(payload: RegisterRequest, request: Request) -> dict:
    """Start a registration. Always reports success.

    The response is identical whether the address is new, already registered,
    or not permitted. Anything else turns this endpoint into a way to ask the
    server which addresses have accounts.
    """
    email = _clean_email(payload.email)
    first_name = payload.first_name.strip()
    last_name = payload.last_name.strip()
    if not first_name or not last_name:
        raise _fail(
            status.HTTP_400_BAD_REQUEST,
            "missing_name",
            "First and last name are required.",
        )

    ip = client_ip(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await enforce(conn, f"register:ip:{ip}", *_REGISTER_IP)
        await enforce(conn, f"register:email:{email}", *_REGISTER_EMAIL)

        # Hashed before either early return so that a rejected address costs
        # the same wall time as an accepted one.
        temp_password = issue_temp_password()
        temp_hash = hash_password(normalize_temp_password(temp_password))

        if not settings.registration_permitted(email):
            logger.warning("Registration blocked, not on allowlist: %s", email)
            return {"status": "sent"}

        if await conn.fetchval("SELECT 1 FROM users WHERE email = $1", email):
            logger.info("Registration for an existing account, ignored: %s", email)
            return {"status": "sent"}

        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=settings.auth_temp_password_ttl_hours
        )
        await conn.execute(
            """
            INSERT INTO pending_registrations
                (email, first_name, last_name, temp_password_hash, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (email) WHERE consumed_at IS NULL
            DO UPDATE SET
                first_name            = EXCLUDED.first_name,
                last_name             = EXCLUDED.last_name,
                temp_password_hash    = EXCLUDED.temp_password_hash,
                expires_at            = EXCLUDED.expires_at,
                attempts              = 0,
                delivery_status       = 'not_sent',
                delivery_attempted_at = NULL,
                created_at            = now()
            """,
            email,
            first_name,
            last_name,
            temp_hash,
            expires_at,
        )

    # Stands in for delivery until an email provider is chosen.
    logger.info("Temporary password for %s: %s", email, temp_password)
    return {"status": "sent"}


@router.post("/auth/set-password")
async def set_password(
    payload: SetPasswordRequest, request: Request, response: Response
) -> dict:
    """Exchange a temporary password for a real one, creating the account."""
    email = _clean_email(payload.email)
    temp_password = normalize_temp_password(payload.temp_password)

    ip = client_ip(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await enforce(conn, f"set-password:ip:{ip}", *_SET_PASSWORD_IP)
        await enforce(conn, f"set-password:email:{email}", *_SET_PASSWORD_EMAIL)

        pending = await conn.fetchrow(
            """
            SELECT id, first_name, last_name, temp_password_hash, expires_at, attempts
            FROM pending_registrations
            WHERE email = $1 AND consumed_at IS NULL
            """,
            email,
        )
        if pending is None:
            raise _fail(
                status.HTTP_400_BAD_REQUEST,
                "no_pending_registration",
                "That temporary password is no longer valid. Request a new one.",
            )
        if pending["expires_at"] <= datetime.now(timezone.utc):
            raise _fail(
                status.HTTP_400_BAD_REQUEST,
                "expired",
                "That temporary password has expired. Request a new one.",
            )
        if pending["attempts"] >= settings.auth_max_temp_password_attempts:
            raise _fail(
                status.HTTP_400_BAD_REQUEST,
                "too_many_attempts",
                "Too many incorrect attempts. Request a new temporary password.",
            )

        if not verify_password(pending["temp_password_hash"], temp_password):
            await conn.execute(
                "UPDATE pending_registrations SET attempts = attempts + 1 WHERE id = $1",
                pending["id"],
            )
            raise _fail(
                status.HTTP_401_UNAUTHORIZED,
                "invalid_temp_password",
                "That temporary password is not correct.",
            )

        failures = password_policy_failures(payload.new_password)
        if failures:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "weak_password",
                    "message": "That password does not meet the requirements.",
                    "failed": failures,
                },
            )

        password_hash = hash_password(payload.new_password)

        # One transaction so an account can never exist with its registration
        # still open, nor a registration be consumed without an account.
        async with conn.transaction():
            user = await conn.fetchrow(
                """
                INSERT INTO users (email, first_name, last_name, password_hash)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (email) DO NOTHING
                RETURNING id, email, first_name, last_name
                """,
                email,
                pending["first_name"],
                pending["last_name"],
                password_hash,
            )
            if user is None:
                # An account appeared between the check in register and now.
                raise _fail(
                    status.HTTP_409_CONFLICT,
                    "account_exists",
                    "An account already exists for that address. Sign in instead.",
                )

            await conn.execute(
                "UPDATE pending_registrations SET consumed_at = now() WHERE id = $1",
                pending["id"],
            )
            token, _ = await create_session(conn, user["id"], remember=False)

    set_session_cookie(response, token, remember=False)
    logger.info("Account created: %s", email)
    return _user_payload(user)


@router.post("/auth/login")
async def login(
    payload: LoginRequest, request: Request, response: Response
) -> dict:
    email = payload.email.strip().lower()
    ip = client_ip(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await enforce(conn, f"login:ip:{ip}", *_LOGIN_IP)
        await enforce(conn, f"login:email:{email}", *_LOGIN_EMAIL)

        user = await conn.fetchrow(
            """
            SELECT id, email, first_name, last_name, password_hash
            FROM users
            WHERE email = $1 AND is_active
            """,
            email,
        )
        # verify_password burns the same time on a missing row as on a real
        # one, so "no such account" and "wrong password" are indistinguishable
        # in both content and latency.
        if not verify_password(user["password_hash"] if user else None, payload.password):
            raise _fail(
                status.HTTP_401_UNAUTHORIZED,
                "invalid_credentials",
                "We couldn't sign you in with those credentials.",
            )

        token, _ = await create_session(conn, user["id"], remember=payload.remember)

    set_session_cookie(response, token, remember=payload.remember)
    return _user_payload(user)


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await delete_session(conn, token)
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
