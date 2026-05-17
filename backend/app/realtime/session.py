"""
Interview session state — persisted between client/upstream WebSocket lifecycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List
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
