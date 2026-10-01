"""The research store: consent decisions and consented session copies.

Everything here reads and writes the `research` schema only. Coursework code
calls exactly one function, capture_research_copy(), and gets nothing back —
so no coursework response can depend on, or reveal, a consent decision.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from app.config import settings
from app.db import get_pool

logger = logging.getLogger(__name__)


async def current_consent(conn: asyncpg.Connection, participant_id: UUID) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        """
        SELECT consented, consent_version, decided_at, withdrawn_at
        FROM research.research_consent WHERE participant_id = $1
        """,
        participant_id,
    )


async def record_decision(
    conn: asyncpg.Connection, participant_id: UUID, consented: bool, version: str
) -> str:
    """Store a decision. Returns given | declined | withdrawn.

    Withdrawal deletes every research copy for the participant in the same
    transaction: a withdrawn participant must not linger in the research store.
    """
    async with conn.transaction():
        previous = await current_consent(conn, participant_id)
        await conn.execute(
            """
            INSERT INTO research.research_consent
                (participant_id, consented, consent_version, decided_at, withdrawn_at)
            VALUES ($1, $2, $3, now(), NULL)
            ON CONFLICT (participant_id) DO UPDATE SET
                consented       = EXCLUDED.consented,
                consent_version = EXCLUDED.consent_version,
                decided_at      = now(),
                withdrawn_at    = CASE
                    WHEN research_consent.consented AND NOT EXCLUDED.consented THEN now()
                    WHEN EXCLUDED.consented THEN NULL
                    ELSE research_consent.withdrawn_at
                END
            """,
            participant_id,
            consented,
            version,
        )
        if consented:
            return "given"
        await conn.execute(
            "DELETE FROM research.session_records WHERE participant_id = $1", participant_id
        )
        return "withdrawn" if previous is not None and previous["consented"] else "declined"


async def capture_research_copy(
    session_id: UUID,
    participant_id: UUID,
    persona_id: Optional[str],
    transcript: list[dict],
    evaluation: dict[str, Any],
) -> None:
    """Copy a scored session into the research store if — and only if — the
    participant has consented and research is enabled.

    Returns nothing and never raises, so the coursework caller behaves
    identically either way. Re-scoring a session replaces its copy.
    """
    if not settings.research_enabled:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO research.session_records
                    (session_id, participant_id, pseudonymous_code, persona_id,
                     transcript, evaluation, consent_version)
                SELECT $1, c.participant_id, p.pseudonymous_code, $3,
                       $4::jsonb, $5::jsonb, c.consent_version
                FROM research.research_consent c
                JOIN public.participants p USING (participant_id)
                WHERE c.participant_id = $2 AND c.consented
                ON CONFLICT (session_id) DO UPDATE SET
                    transcript      = EXCLUDED.transcript,
                    evaluation      = EXCLUDED.evaluation,
                    consent_version = EXCLUDED.consent_version,
                    captured_at     = now()
                """,
                session_id,
                participant_id,
                persona_id,
                json.dumps(transcript),
                json.dumps(evaluation),
            )
    except Exception as e:
        logger.warning("research capture failed for session %s: %s", session_id, type(e).__name__)
