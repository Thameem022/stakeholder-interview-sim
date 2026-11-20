""""Try SES" — a single guest interview with no account.

`start` creates a throwaway users row (is_trial) and a short session cookie, so
every downstream route, ownership check and scorer works exactly as it does for
a student. What a guest may do with that session is narrowed elsewhere, keyed
on `CurrentUser.is_trial`: Alex only and one interview (realtime/token.py),
and a hard time cap on the per-turn endpoints (realtime/retrieve.py).

With TRIAL_ENABLED off the route answers 404, indistinguishable from not
existing, so shipping this code changes nothing until it is switched on.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response, status

from app.auth.errors import auth_error
from app.auth.passwords import hash_password
from app.auth.ratelimit import client_ip, enforce
from app.auth.sessions import create_session, load_session_user, set_session_cookie
from app.config import settings
from app.db import get_pool
from app.personas.trial_briefing import TRIAL_BRIEFING
from app.realtime.trial_guard import trial_status

logger = logging.getLogger(__name__)

router = APIRouter()

_TRIAL_IP = (5, 3600)

# Scoped to /api/trial so it rides along on nothing else.
TRIAL_MARKER_COOKIE = "ses_trial"
_TRIAL_MARKER_DAYS = 365


def _parse_uuid(raw: str | None) -> UUID | None:
    try:
        return UUID(raw) if raw else None
    except ValueError:
        return None


def _payload(user) -> dict:
    return {
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "is_trial": True,
        },
        "briefing": TRIAL_BRIEFING,
        "interview_minutes": settings.trial_interview_minutes,
    }


@router.post("/trial/start")
async def start_trial(request: Request, response: Response) -> dict:
    if not settings.trial_enabled:
        raise auth_error(status.HTTP_404_NOT_FOUND, "not_found", "Not found.")

    ip = client_ip(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        # One trial per browser. The marker names this browser's last guest and
        # outlives its session. A trial only counts as spent once that guest
        # actually interviewed (trial_status), so an attempt that never got
        # going (bad key, microphone denied, network) can simply be retried —
        # nobody should have to clear site data to try again.
        #
        # This is a deterrent, not a wall: clearing cookies or a private window
        # gets past it. The per-IP and daily limits below are the backstop.
        marker = _parse_uuid(request.cookies.get(TRIAL_MARKER_COOKIE))
        if marker is not None:
            used, exhausted = await trial_status(conn, marker)
            if used:
                raise auth_error(
                    status.HTTP_409_CONFLICT,
                    "trial_used",
                    "You've already used your free trial on this browser.",
                )
            # Same guest still signed in (a page refresh) and still able to
            # start a call: hand back their own trial rather than a new one.
            token = request.cookies.get(settings.auth_cookie_name)
            current = await load_session_user(conn, token) if token else None
            if (
                not exhausted
                and current is not None
                and current["is_trial"]
                and current["id"] == marker
            ):
                return _payload(current)

        await enforce(conn, f"trial-start:ip:{ip}", *_TRIAL_IP)
        await enforce(conn, "trial-start:global", settings.trial_daily_cap, 86400)

        # The password is random and never shown to anyone, so the guest
        # address cannot be signed into; the cookie is the only way in. The
        # .invalid TLD is reserved, so it can never collide with a real address.
        async with conn.transaction():
            user = await conn.fetchrow(
                """
                INSERT INTO users (email, first_name, last_name, password_hash, is_trial)
                VALUES ($1, 'Guest', '', $2, true)
                RETURNING id, email, first_name, last_name
                """,
                f"trial-{uuid4().hex}@trial.invalid",
                hash_password(secrets.token_urlsafe(32)),
            )
            token, _ = await create_session(
                conn,
                user["id"],
                remember=False,
                ttl=timedelta(minutes=settings.trial_session_minutes),
            )

    set_session_cookie(response, token, remember=False)
    response.set_cookie(
        key=TRIAL_MARKER_COOKIE,
        value=str(user["id"]),
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/api/trial",
        max_age=_TRIAL_MARKER_DAYS * 86400,
    )
    logger.info("Trial started: user=%s", user["id"])
    return _payload(user)
