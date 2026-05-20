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

from app.db import get_pool


@dataclass
class Turn:
    role: str
    text: str
    timestamp: str


@dataclass
class InterviewSession:
    id: UUID
    persona_id: str
    voice_id: str
    started_at: datetime
    turns: List[Turn] = field(default_factory=list)

    def add_turn(self, role: str, text: str) -> None:
        self.turns.append(
            Turn(role=role, text=text, timestamp=datetime.utcnow().isoformat() + "Z")
        )

    async def persist(self, ended: bool = False) -> None:
        pool = await get_pool()
        transcript_json = json.dumps([t.__dict__ for t in self.turns])
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO interview_sessions (id, persona_id, voice_id, started_at, ended_at, transcript)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                  ended_at = EXCLUDED.ended_at,
                  transcript = EXCLUDED.transcript
                """,
                self.id,
                self.persona_id,
                self.voice_id,
                self.started_at,
                datetime.utcnow() if ended else None,
                transcript_json,
            )

    @classmethod
    async def load(cls, session_id: UUID) -> Optional["InterviewSession"]:
        """Hydrate a session from the DB. Returns None if not found."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, persona_id, voice_id, started_at, transcript
                FROM interview_sessions
                WHERE id = $1
                """,
                session_id,
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
            persona_id=row["persona_id"],
            voice_id=row["voice_id"] or "",
            started_at=row["started_at"],
            turns=turns,
        )
