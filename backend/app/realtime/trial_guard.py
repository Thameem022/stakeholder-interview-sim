"""Limits that apply only to "Try SES" guests (CurrentUser.is_trial).

Every function here is a no-op for a regular account, which is what keeps the
trial from changing anything for students. The 10-minute cap is enforced on
our endpoints from `interview_sessions.started_at`: the browser ends the call
at 10:00, and anything that keeps talking past that loses retrieval and can no
longer write turns. The final ended=true append is still accepted so the
transcript is saved and can be scored.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import status

from app.auth.dependencies import CurrentUser
from app.auth.errors import auth_error
from app.config import settings
from app.db import get_pool
from app.personas.trial_briefing import TRIAL_ALEX_INSTRUCTIONS, TRIAL_PERSONA_ID

# Slack for network latency and the last turn finishing after the client timer.
_GRACE_SECONDS = 30


# A mint can fail after the row is written (OpenAI error, microphone denied and
# retried), so a guest gets a few attempts. The clock does not restart: the cap
# in check_trial_time runs from their first one.
_TRIAL_MAX_MINTS = 3


async def check_trial_mint(user: CurrentUser) -> None:
    """Called before the session row is created."""
    if not user.is_trial:
        return
    await check_trial_time(user)
    pool = await get_pool()
    async with pool.acquire() as conn:
        used = await conn.fetchval(
            "SELECT count(*) FROM interview_sessions WHERE user_id = $1", user.id
        )
    if used >= _TRIAL_MAX_MINTS:
        raise auth_error(
            status.HTTP_409_CONFLICT,
            "trial_used",
            "This trial has already been used.",
        )


def trial_persona(user: CurrentUser, requested: str) -> str:
    """Guests always interview Alex, whatever the client sends."""
    return TRIAL_PERSONA_ID if user.is_trial else requested


def trial_instructions(user: CurrentUser, instructions: str) -> str:
    """Guests' Alex earns the Threshold Gap through trust; students' does not
    change. Appended last so it wins where it overrides general disclosure."""
    if not user.is_trial:
        return instructions
    return f"{instructions}\n\n{TRIAL_ALEX_INSTRUCTIONS}"


async def mark_trial_session(user: CurrentUser, session_id: UUID) -> None:
    if not user.is_trial:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE interview_sessions
               SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"trial": true}'::jsonb,
                   -- mint_token passes a naive utcnow(), which asyncpg reads as
                   -- local time, so the stored value is off by the host's UTC
                   -- offset. The cap is timed from this column, so trial rows
                   -- take the database clock instead.
                   started_at = now()
             WHERE id = $1 AND user_id = $2
            """,
            session_id,
            user.id,
        )


async def trial_status(conn, user_id: UUID) -> tuple[bool, bool]:
    """(used, exhausted) for a guest.

    used: they actually interviewed, i.e. at least one turn was saved. That,
    not opening the briefing or minting a token, is what spends the browser's
    one trial, so a failed attempt (bad key, microphone denied, dropped
    network) never locks anyone out.

    exhausted: this guest can no longer start a call (out of attempts or out of
    time), so resuming it would hand back a dead trial.
    """
    row = await conn.fetchrow(
        """
        SELECT coalesce(bool_or(jsonb_array_length(coalesce(transcript, '[]'::jsonb)) > 0), false)
                   AS used,
               count(*) AS mints,
               coalesce(now() > min(started_at) + make_interval(secs => $2::double precision), false)
                   AS expired
          FROM interview_sessions
         WHERE user_id = $1
        """,
        user_id,
        float(settings.trial_interview_minutes * 60 + _GRACE_SECONDS),
    )
    return row["used"], row["mints"] >= _TRIAL_MAX_MINTS or row["expired"]


async def check_trial_time(user: CurrentUser) -> None:
    """410 once a guest is past the cap, timed from their FIRST interview row.

    Anchoring on the first row rather than the requested session means neither
    a retry mint nor leaving the session id off a request restarts the clock.
    """
    if not user.is_trial:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        expired = await conn.fetchval(
            """
            SELECT now() > min(started_at) + make_interval(secs => $2::double precision)
              FROM interview_sessions
             WHERE user_id = $1
            """,
            user.id,
            float(settings.trial_interview_minutes * 60 + _GRACE_SECONDS),
        )
    # NULL when there is no row yet: nothing to time.
    if expired:
        raise auth_error(
            status.HTTP_410_GONE,
            "trial_time_up",
            "The trial interview has reached its time limit.",
        )
