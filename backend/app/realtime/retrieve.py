"""
Realtime support endpoints — RAG fulfillment for the `retrieve_context` tool,
and per-turn transcript persistence.

The browser drives the realtime session directly via WebRTC. The backend only
gets called when the model invokes the retrieve tool, or when the browser wants
to record a finished turn.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, rate_limited, require_user
from app.db import get_pool
from app.realtime.session import InterviewSession
from app.vector_store import embed_one, search_persona, search_world

logger = logging.getLogger(__name__)

router = APIRouter()

# Driven by the model's own tool calls, so roughly 20-40 per interview. This
# ceiling exists to stop a runaway tool-call loop, not to manage spend — each
# call is one text-embedding-3-small request.
_RETRIEVE_LIMIT = ("realtime-retrieve", 300, 3600)

# Two writes per conversational turn, no external cost. Bounds table growth
# from a stuck client, nothing more.
_TRANSCRIPT_LIMIT = ("realtime-transcript", 600, 3600)


class RetrieveRequest(BaseModel):
    persona_id: str
    query: str
    # Optional so an older client, or an ad-hoc call, still retrieves normally —
    # the telemetry row just cannot be joined back to an interview.
    session_id: Optional[str] = None


class RetrieveResponse(BaseModel):
    text: str


class TranscriptRequest(BaseModel):
    session_id: str
    role: Literal["user", "assistant"]
    text: str
    ended: Optional[bool] = False


# Strong references to in-flight telemetry writes. asyncio only holds a weak
# reference to a running task, so a task nobody keeps can be garbage collected
# mid-flight — which shows up much later as "some events are missing".
_bg_tasks: set[asyncio.Task] = set()


async def _insert_retrieval_event(fields: dict[str, Any]) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO retrieval_events (
                    session_id, persona_id, query, embed_ms, search_ms, total_ms,
                    persona_top_scores, world_top_scores,
                    persona_chunk_ids, world_chunk_ids,
                    k_persona, k_world, error
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                fields["session_id"], fields["persona_id"], fields["query"],
                fields["embed_ms"], fields["search_ms"], fields["total_ms"],
                fields["persona_top_scores"], fields["world_top_scores"],
                fields["persona_chunk_ids"], fields["world_chunk_ids"],
                fields["k_persona"], fields["k_world"], fields["error"],
            )
    except Exception as e:
        logger.warning(f"failed to record retrieval event: {e}")


def _record_retrieval_event(**fields: Any) -> None:
    """Fire-and-forget. Never awaited, so a slow or failing write cannot add
    latency to a persona's reply or surface to the student mid-interview."""
    raw_session = fields.get("session_id")
    session_id: Optional[UUID] = None
    if raw_session:
        try:
            session_id = UUID(str(raw_session))
        except ValueError:
            # An unparseable id is not worth dropping the row over; the timing
            # is still valid telemetry, it just cannot be joined to a session.
            session_id = None
    fields["session_id"] = session_id

    try:
        task = asyncio.create_task(_insert_retrieval_event(fields))
    except RuntimeError:
        return  # no running loop — nothing to record onto
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _scores(chunks) -> list[float]:
    return [float(c.get("score", 0.0)) for c in chunks]


def _ids(chunks) -> list[str]:
    return [str(c.get("chunk_id", "")) for c in chunks]


def _format_context(persona_chunks, world_chunks) -> str:
    """Same shape the old WebSocket proxy used, so persona behavior is unchanged."""
    lines = ["Relevant context for the current question:"]

    if persona_chunks:
        lines.append("\n[Persona-specific context]")
        for i, c in enumerate(persona_chunks, 1):
            lines.append(f"  ({c.get('source', 'persona')}#{i}) {c['text'][:600]}")

    if world_chunks:
        lines.append("\n[Harbortown world context]")
        for i, c in enumerate(world_chunks, 1):
            section = c.get("section_title") or "Section"
            lines.append(f"  (world#{i} — {section}) {c['text'][:600]}")

    if not persona_chunks and not world_chunks:
        lines.append("\n(no relevant context found)")

    return "\n".join(lines)


@router.post(
    "/realtime/retrieve",
    response_model=RetrieveResponse,
    dependencies=[Depends(rate_limited(*_RETRIEVE_LIMIT))],
)
async def retrieve_context(req: RetrieveRequest) -> RetrieveResponse:
    if not req.persona_id or not req.query.strip():
        raise HTTPException(status_code=400, detail="persona_id and query required")

    k_persona, k_world = 5, 3
    started = perf_counter()

    try:
        query_vec = await embed_one(req.query)
    except Exception as e:
        logger.warning(f"retrieve embed failed: {e}")
        elapsed = (perf_counter() - started) * 1000
        _record_retrieval_event(
            session_id=req.session_id, persona_id=req.persona_id, query=req.query,
            embed_ms=elapsed, search_ms=None, total_ms=elapsed,
            persona_top_scores=[], world_top_scores=[],
            persona_chunk_ids=[], world_chunk_ids=[],
            k_persona=k_persona, k_world=k_world, error=f"embed: {e}",
        )
        return RetrieveResponse(text=_format_context([], []))

    embed_ms = (perf_counter() - started) * 1000

    search_started = perf_counter()
    results = await asyncio.gather(
        search_persona(req.persona_id, req.query, k=k_persona, query_vec=query_vec),
        search_world(req.query, k=k_world, query_vec=query_vec),
        return_exceptions=True,
    )
    search_ms = (perf_counter() - search_started) * 1000

    errors: list[str] = []
    if isinstance(results[0], Exception):
        logger.warning(f"persona retrieval failed: {results[0]}")
        errors.append(f"persona: {results[0]}")
        persona_chunks = []
    else:
        persona_chunks = results[0]

    if isinstance(results[1], Exception):
        logger.warning(f"world retrieval failed: {results[1]}")
        errors.append(f"world: {results[1]}")
        world_chunks = []
    else:
        world_chunks = results[1]

    _record_retrieval_event(
        session_id=req.session_id, persona_id=req.persona_id, query=req.query,
        embed_ms=embed_ms, search_ms=search_ms,
        total_ms=(perf_counter() - started) * 1000,
        persona_top_scores=_scores(persona_chunks), world_top_scores=_scores(world_chunks),
        persona_chunk_ids=_ids(persona_chunks), world_chunk_ids=_ids(world_chunks),
        k_persona=k_persona, k_world=k_world,
        error="; ".join(errors) or None,
    )

    text = _format_context(persona_chunks, world_chunks)
    return RetrieveResponse(text=text)


@router.post(
    "/realtime/transcript",
    dependencies=[Depends(rate_limited(*_TRANSCRIPT_LIMIT))],
)
async def append_transcript(
    req: TranscriptRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> dict:
    try:
        sid = UUID(req.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id")

    # Someone else's session is indistinguishable from a nonexistent one, so a
    # valid id cannot be confirmed by probing. persist() re-checks ownership on
    # the write itself, which is what makes the gap between here and there safe.
    session = await InterviewSession.load(sid, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    text = req.text.strip()
    if text:
        session.add_turn(req.role, text)

    await session.persist(ended=bool(req.ended))
    return {"ok": True, "turns": len(session.turns)}
