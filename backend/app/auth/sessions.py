"""Session tokens and the session cookie.

The cookie carries a high-entropy random token; only its SHA-256 is stored, so
a database leak yields nothing that can be replayed. The token is random rather
than derived, which is why no signing secret is involved — there is nothing in
it to forge.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import Response

from app.config import settings


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    # Plain SHA-256 is right here: the input is 256 bits of entropy, so there
    # is no dictionary to slow an attacker down with. Argon2 would only add
    # latency to every authenticated request.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session(
    conn: asyncpg.Connection, user_id: UUID, remember: bool
) -> tuple[str, datetime]:
    token = generate_session_token()
    ttl = (
        timedelta(days=settings.auth_session_remember_days)
        if remember
        else timedelta(hours=settings.auth_session_default_hours)
    )
    expires_at = datetime.now(timezone.utc) + ttl
    await conn.execute(
        "INSERT INTO auth_sessions (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
        user_id,
        hash_session_token(token),
        expires_at,
    )
    return token, expires_at


async def load_session_user(
    conn: asyncpg.Connection, token: str
) -> asyncpg.Record | None:
    """Resolve a raw cookie token to its user, or None if unusable.

    Expiry and is_active are filtered in SQL so a disabled account or a lapsed
    session is indistinguishable from an unknown token.
    """
    return await conn.fetchrow(
        """
        SELECT u.id, u.email, u.first_name, u.last_name, s.id AS session_id
        FROM auth_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1
          AND s.expires_at > now()
          AND u.is_active
        """,
        hash_session_token(token),
    )


async def touch_session(
    conn: asyncpg.Connection,
    session_id: UUID,
    min_interval_seconds: float | None = None,
) -> None:
    """Record activity on a session.

    `min_interval_seconds` skips the write when the row was touched recently.
    That is safe because `last_seen_at` is informational — session validity
    comes from `expires_at`, fixed at creation, so a missed touch changes
    nothing about who can authenticate. Worth having: a single interview drives
    ~100 authenticated requests, and unthrottled that is ~100 updates and WAL
    records against one row.

    The check is a predicate on the UPDATE rather than a separate read, so the
    throttled path still costs exactly one round trip.
    """
    if min_interval_seconds is None:
        await conn.execute(
            "UPDATE auth_sessions SET last_seen_at = now() WHERE id = $1", session_id
        )
        return

    await conn.execute(
        """
        UPDATE auth_sessions
           SET last_seen_at = now()
         WHERE id = $1
           AND last_seen_at < now() - make_interval(secs => $2::double precision)
        """,
        session_id,
        float(min_interval_seconds),
    )


async def delete_session(conn: asyncpg.Connection, token: str) -> None:
    await conn.execute(
        "DELETE FROM auth_sessions WHERE token_hash = $1", hash_session_token(token)
    )


async def delete_expired_sessions(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM auth_sessions WHERE expires_at <= now()")


def set_session_cookie(response: Response, token: str, remember: bool) -> None:
    """Attach the session cookie.

    Without `remember`, max_age is omitted so the browser drops the cookie when
    it closes; the row still carries its own shorter expiry, so the server side
    is authoritative either way.
    """
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
        max_age=settings.auth_session_remember_days * 86400 if remember else None,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )
