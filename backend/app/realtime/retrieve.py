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
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.realtime.session import InterviewSession
from app.vector_store import embed_one, search_persona, search_world

logger = logging.getLogger(__name__)

router = APIRouter()


class RetrieveRequest(BaseModel):
    persona_id: str
    query: str


class RetrieveResponse(BaseModel):
    text: str


class TranscriptRequest(BaseModel):
    session_id: str
    role: Literal["user", "assistant"]
    text: str
    ended: Optional[bool] = False


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


@router.post("/realtime/retrieve", response_model=RetrieveResponse)
async def retrieve_context(req: RetrieveRequest) -> RetrieveResponse:
    if not req.persona_id or not req.query.strip():
        raise HTTPException(status_code=400, detail="persona_id and query required")

    try:
        query_vec = await embed_one(req.query)
    except Exception as e:
        logger.warning(f"retrieve embed failed: {e}")
        return RetrieveResponse(text=_format_context([], []))

    results = await asyncio.gather(
        search_persona(req.persona_id, req.query, k=5, query_vec=query_vec),
        search_world(req.query, k=3, query_vec=query_vec),
        return_exceptions=True,
    )

    if isinstance(results[0], Exception):
        logger.warning(f"persona retrieval failed: {results[0]}")
        persona_chunks = []
    else:
        persona_chunks = results[0]

    if isinstance(results[1], Exception):
        logger.warning(f"world retrieval failed: {results[1]}")
        world_chunks = []
    else:
        world_chunks = results[1]

    text = _format_context(persona_chunks, world_chunks)
    return RetrieveResponse(text=text)


@router.post("/realtime/transcript")
async def append_transcript(req: TranscriptRequest) -> dict:
    try:
        sid = UUID(req.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id")

    session = await InterviewSession.load(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    text = req.text.strip()
    if text:
        session.add_turn(req.role, text)

    await session.persist(ended=bool(req.ended))
    return {"ok": True, "turns": len(session.turns)}
