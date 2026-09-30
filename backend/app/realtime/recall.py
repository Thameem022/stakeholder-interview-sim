"""
Realtime support endpoint — fulfillment for the `recall` tool.

The persona's Tier-1 material is inlined into the session instructions at mint
time (see app/realtime/token.py), so ordinary questions need no tool call at
all. This endpoint exists for the rest: when the interviewer pushes past what
the persona was handed up front, the model calls `recall(topics)` and gets back
either the sayable line for an item it has earned, or the deflection for one it
has not.

Contrast with /realtime/retrieve, which is a vector search over chunks. This is
a tag lookup over curated rows — no embedding, no network hop, one indexed
query.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Annotated, List, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, rate_limited, require_user
from app.db import get_pool
from app.realtime.events import record_recall_event
from app.realtime.session import InterviewSession

logger = logging.getLogger(__name__)

router = APIRouter()

# Model-driven like /realtime/retrieve, so a comparable ceiling. Unlike
# retrieve, a call costs one indexed query and no OpenAI request, so this bound
# is about stopping a runaway tool-call loop rather than about spend.
_RECALL_LIMIT = ("realtime-recall", 300, 3600)

# At most four items come back per call. The cap is a speech-length limit, not
# a relevance one: everything returned is read aloud by a voice model, and more
# than four fragments produces a monologue no interviewer would sit through.
_RECALL_LIMIT_ROWS = 4

# `claim` is deliberately absent from the query below, from RecallItem, and
# from the log row. It is the grader's statement of the fact, written as a flat
# assertion ("The sea wall bid came in 40% over"), and a voice model handed one
# reads it back verbatim — which both breaks character and hands the student
# the answer they were supposed to earn. `in_voice` is the sayable version of
# the same fact and is the only form that may cross this boundary. If you are
# adding a column here, this is the one to leave out.
_RECALL_SQL = """
    SELECT id, tier, topics,
           CASE WHEN tier <= GREATEST($3, 1) THEN in_voice ELSE deflection END AS text,
           (tier <= GREATEST($3, 1)) AS earned
    FROM persona_knowledge
    WHERE persona_id = $1 AND topics && $2::text[]
    ORDER BY tier, id
    LIMIT {limit}
""".format(limit=_RECALL_LIMIT_ROWS)


class RecallRequest(BaseModel):
    session_id: UUID
    topics: List[str]


class RecallItem(BaseModel):
    id: str
    tier: int
    text: str
    earned: bool


class RecallResponse(BaseModel):
    posture: Literal["open", "contracted"]
    items: List[RecallItem]


@router.post(
    "/realtime/recall",
    response_model=RecallResponse,
    dependencies=[Depends(rate_limited(*_RECALL_LIMIT))],
)
async def recall(
    req: RecallRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> RecallResponse:
    started = perf_counter()

    topics = [t.strip() for t in req.topics if t and t.strip()]
    if not topics:
        raise HTTPException(status_code=400, detail="topics required")

    # Someone else's session is indistinguishable from a nonexistent one — 404,
    # not 403, matching /realtime/transcript. A 403 would confirm the id is
    # real, which is exactly what makes session ids probeable.
    session = await InterviewSession.load(req.session_id, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    # persona_id comes off the session row, never off the request body. Taking
    # it from the body would let any caller read any persona's catalogue
    # through a session they happen to own.
    persona_id = session.persona_id

    # TODO: read from interview_sessions (column `disclosure_mode`, added in
    # migration 0007). Hardcoded until the classifier that maintains it exists.
    mode = 1

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_RECALL_SQL, persona_id, topics, mode)

    items = [
        RecallItem(
            id=r["id"],
            tier=r["tier"],
            # `deflection` is nullable, so `text` can be NULL for an unearned
            # item that has no written deflection. Unreachable today — every
            # row without one is Tier 1, and GREATEST($3, 1) floors the
            # threshold so Tier 1 is always earned — but an empty string is a
            # silent persona, so make the fallback explicit rather than None.
            text=r["text"] or "(no comment on file)",
            earned=r["earned"],
        )
        for r in rows
    ]

    # One row per call, shared with world_lookup. `path` stays null: recall
    # has a single lookup strategy, so there is nothing to distinguish.
    record_recall_event(
        session_id=req.session_id,
        tool="recall",
        topics=topics,
        mode=mode,
        ids_returned=[i.id for i in items],
        earned=[i.earned for i in items],
        latency_ms=(perf_counter() - started) * 1000,
    )

    return RecallResponse(
        posture="contracted" if mode == 0 else "open",
        items=items,
    )
