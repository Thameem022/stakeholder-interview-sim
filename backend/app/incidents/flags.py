"""Session flags and purges — the code half of the incident runbook.

IRB-27-0033 adverse-event reporting + SR-2026-052 items 0.2 / 3.1
(SEC-INC-001). See deploy/RUNBOOK.md for the human half: who reviews a flag,
when Information Security, Student Affairs and the IRB are told.

Flagging records an incident and raises a high-severity audit event — that
event IS the notification path: the SIEM alert rule on `incident.session_flagged`
pages the named alert owner. Purging removes a flagged session's content
everywhere it exists (course record, evaluations, retrieval telemetry, any
research copy) and leaves the flag behind as the incident record.
"""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

import asyncpg

from app.observability.audit import audit

FlagSource = Literal["participant", "instructor", "guardrail", "support"]
FlagReason = Literal["sensitive_disclosure", "distress", "harmful_ai_output", "other"]


async def flag_session(
    conn: asyncpg.Connection,
    session_id: UUID,
    *,
    source: FlagSource,
    reason: FlagReason,
    flagged_by: Optional[UUID] = None,
    note: Optional[str] = None,
) -> UUID:
    """Record a flag and notify. The entry point for people AND for automated
    guardrail trips (which pass source="guardrail" and no flagged_by)."""
    flag_id = await conn.fetchval(
        """
        INSERT INTO session_flags (session_id, source, reason, note, flagged_by)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        session_id, source, reason, note, flagged_by,
    )
    notify_flag(flag_id, session_id, source, reason, flagged_by)
    return flag_id


def notify_flag(
    flag_id: UUID,
    session_id: UUID,
    source: str,
    reason: str,
    flagged_by: Optional[UUID],
) -> None:
    # The note is never forwarded: it may describe what was disclosed. The
    # reviewer reads it in the application, not in an alert.
    audit(
        "incident.session_flagged",
        "success",
        actor_user_id=flagged_by,
        severity="high",
        flag_id=flag_id,
        session_id=session_id,
        source=source,
        reason=reason,
    )


async def purge_flagged_session(
    conn: asyncpg.Connection, flag_id: UUID, purged_by: Optional[UUID]
) -> Optional[dict]:
    """Purge the session a flag points at. None if the flag is unknown, already
    purged, or its session is gone. Everything happens in one transaction."""
    async with conn.transaction():
        flag = await conn.fetchrow(
            "SELECT id, session_id, status FROM session_flags WHERE id = $1 FOR UPDATE",
            flag_id,
        )
        if flag is None or flag["status"] == "purged" or flag["session_id"] is None:
            return None
        sid = flag["session_id"]

        evaluations = await conn.execute(
            "DELETE FROM session_evaluations WHERE session_id = $1", sid
        )
        retrievals = await conn.execute("DELETE FROM retrieval_events WHERE session_id = $1", sid)
        research = await conn.execute(
            "DELETE FROM research.session_records WHERE session_id = $1", sid
        )
        await conn.execute(
            """
            UPDATE interview_sessions
               SET transcript = '[]'::jsonb, metadata = NULL, purged_at = now()
             WHERE id = $1
            """,
            sid,
        )
        await conn.execute(
            """
            UPDATE session_flags
               SET status = 'purged', purged_at = now(), purged_by = $2
             WHERE session_id = $1 AND status <> 'purged'
            """,
            sid, purged_by,
        )

    def _count(tag: str) -> int:
        return int(tag.rsplit(" ", 1)[-1])

    return {
        "session_id": sid,
        "evaluations_deleted": _count(evaluations),
        "retrieval_events_deleted": _count(retrievals),
        "research_copies_deleted": _count(research),
    }
