"""
Interview session state — created at token-mint time, hydrated per turn for
transcript appends.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import status

from app.auth.errors import auth_error
from app.db import get_pool


@dataclass
class Turn:
    role: str
    text: str
    timestamp: str


@dataclass
class InterviewSession:
    id: UUID
    user_id: UUID
    persona_id: str
    voice_id: str
    started_at: datetime
    turns: List[Turn] = field(default_factory=list)

    def add_turn(self, role: str, text: str) -> None:
        self.turns.append(
            Turn(role=role, text=text, timestamp=datetime.utcnow().isoformat() + "Z")
        )

    async def create(self) -> None:
        """Insert a brand-new, empty session.

        Creation and update are separate statements on purpose. They used to be
        one upsert, and that conflation was exploitable: minting a token with
        an existing session id ran the DO UPDATE branch with this object's
        empty transcript, wiping whatever was already stored.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO interview_sessions
                    (id, user_id, persona_id, voice_id, started_at, transcript)
                VALUES ($1, $2, $3, $4, $5, '[]'::jsonb)
                """,
                self.id,
                self.user_id,
                self.persona_id,
                self.voice_id,
                self.started_at,
            )

    async def persist(self, ended: bool = False) -> None:
        """Write the transcript back, only if this session is still ours.

        The `user_id` predicate on the UPDATE itself — not just on the earlier
        load — is what closes the load-then-write gap: even if ownership
        changed in between, the write cannot land on someone else's row.
        """
        pool = await get_pool()
        transcript_json = json.dumps([t.__dict__ for t in self.turns])
        async with pool.acquire() as conn:
            tag = await conn.execute(
                """
                UPDATE interview_sessions
                   SET transcript = $3::jsonb,
                       -- Ending is sticky. A turn that lands after the client
                       -- has ended the interview must not reopen it, which the
                       -- old `utcnow() if ended else None` did on every append.
                       ended_at = CASE WHEN $4 THEN now() ELSE ended_at END
                 WHERE id = $1 AND user_id = $2
                """,
                self.id,
                self.user_id,
                transcript_json,
                ended,
            )
        # asyncpg returns the command tag, e.g. "UPDATE 1" / "UPDATE 0".
        if tag.rsplit(" ", 1)[-1] == "0":
            raise auth_error(
                status.HTTP_404_NOT_FOUND, "session_not_found", "Session not found."
            )

    @classmethod
    async def load(
        cls, session_id: UUID, user_id: UUID
    ) -> Optional["InterviewSession"]:
        """Hydrate a session owned by `user_id`. None if absent or not theirs.

        `user_id` is required rather than an optional filter — a default would
        be an invitation for a future call site to skip the check silently.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, user_id, persona_id, voice_id, started_at, transcript
                FROM interview_sessions
                WHERE id = $1 AND user_id = $2
                """,
                session_id,
                user_id,
            )
        if row is None:
            return None

        raw_transcript = row["transcript"]
        if isinstance(raw_transcript, str):
            transcript_list = json.loads(raw_transcript)
        else:
            transcript_list = raw_transcript or []

        turns = [
            Turn(role=t["role"], text=t["text"], timestamp=t["timestamp"])
            for t in transcript_list
        ]

        return cls(
            id=row["id"],
            user_id=row["user_id"],
            persona_id=row["persona_id"],
            voice_id=row["voice_id"] or "",
            started_at=row["started_at"],
            turns=turns,
        )
