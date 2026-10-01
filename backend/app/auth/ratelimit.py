"""Postgres-backed rate limiting for the auth endpoints.

Counters live in the database rather than in process memory because in-memory
counters silently stop working the moment uvicorn runs more than one worker —
the kind of regression nobody notices until it matters.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import HTTPException, Request, status


def client_ip(request: Request) -> str:
    """Caller IP, honouring the reverse proxy.

    Apache terminates TLS in front of uvicorn, so request.client.host is
    127.0.0.1 for every caller and X-Forwarded-For carries the real address.
    That header is trusted only because nothing reaches uvicorn except through
    the proxy; exposing the port directly would make it spoofable.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _audit_rate_limited(bucket: str) -> None:
    # Imported here: the audit module uses client_ip from this one.
    from app.observability.audit import audit

    # Buckets are "<action>:<scope>:<value>". The value is an address or an IP
    # for the auth endpoints, so only a user id is passed through as-is.
    action, _, rest = bucket.partition(":")
    scope, _, value = rest.partition(":")
    actor = None
    if scope == "user":
        try:
            actor = UUID(value)
        except ValueError:
            pass
    audit("auth.rate_limited", "denied", actor_user_id=actor, action=action, scope=scope)


async def enforce(
    conn: asyncpg.Connection, bucket: str, limit: int, window_seconds: int
) -> None:
    """Record an attempt against `bucket`, raising 429 once over `limit`."""
    used = await conn.fetchval(
        """
        SELECT count(*) FROM auth_rate_limits
        WHERE bucket = $1
          AND occurred_at > now() - make_interval(secs => $2::double precision)
        """,
        bucket,
        float(window_seconds),
    )
    if used >= limit:
        _audit_rate_limited(bucket)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Too many attempts. Please wait and try again.",
            },
        )

    await conn.execute("INSERT INTO auth_rate_limits (bucket) VALUES ($1)", bucket)
    # Prune this bucket only — bounded work on an indexed range, so the table
    # stays small without a separate cleanup job.
    await conn.execute(
        """
        DELETE FROM auth_rate_limits
        WHERE bucket = $1
          AND occurred_at <= now() - make_interval(secs => $2::double precision)
        """,
        bucket,
        float(window_seconds),
    )
