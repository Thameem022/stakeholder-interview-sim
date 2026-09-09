"""Route-level authentication and per-user metering.

`require_user` is the only thing in the app that raises 401 on a non-auth
route. Ownership failures deliberately answer 404 instead, so a 401 reaching
the browser carries exactly one meaning: the session is gone. The frontend
relies on that being unambiguous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, status

from app.auth.errors import auth_error
from app.auth.ratelimit import enforce
from app.auth.sessions import load_session_user, touch_session
from app.config import settings
from app.db import get_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    first_name: str
    last_name: str
    session_id: UUID


async def require_user(request: Request) -> CurrentUser:
    """Resolve the session cookie, or fail the request.

    The failure shape matches /api/auth/me exactly, so a route newly placed
    behind this needs no new frontend handling.
    """
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise auth_error(
            status.HTTP_401_UNAUTHORIZED, "not_authenticated", "Not signed in."
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await load_session_user(conn, token)
        if row is None:
            raise auth_error(
                status.HTTP_401_UNAUTHORIZED, "not_authenticated", "Not signed in."
            )
        await touch_session(
            conn,
            row["session_id"],
            min_interval_seconds=settings.auth_session_touch_interval_seconds,
        )

    return CurrentUser(
        id=row["id"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        session_id=row["session_id"],
    )


def rate_limited(action: str, limit: int, window_seconds: int):
    """Per-user metering for endpoints that spend money or write a lot.

    Keyed on the user rather than the IP: the auth endpoints key on IP because
    no user exists yet, but here one does, and behind campus NAT an IP limit
    would let one runaway client throttle everyone sharing the address.

    Note this runs BEFORE the endpoint body, so a request for a session that
    does not exist still consumes budget. That is deliberate — otherwise
    probing for valid session ids costs an attacker nothing.
    """

    async def _dep(user: Annotated[CurrentUser, Depends(require_user)]) -> None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await enforce(conn, f"{action}:user:{user.id}", limit, window_seconds)

    return _dep


def deny_session_access(session_id: UUID, requester: UUID, owner: UUID | None) -> None:
    """Log an ownership denial, then raise the same 404 as 'does not exist'.

    The response must not distinguish "someone else's" from "no such session" —
    that is what stops a UUID from being confirmable. The distinction is still
    worth having when debugging, so it goes to the log, which is also the only
    place a probing attempt would ever show up.
    """
    if owner is not None:
        logger.warning(
            "ownership denied: session=%s owner=%s requester=%s",
            session_id,
            owner,
            requester,
        )
    raise auth_error(
        status.HTTP_404_NOT_FOUND, "session_not_found", "Session not found."
    )
