from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.db import get_pool

router = APIRouter()


async def _load_session(session_id: UUID) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT persona_id, transcript, metadata FROM interview_sessions WHERE id = $1",
            session_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return dict(row)


@router.post("/eval/iqr")
async def eval_iqr(session_id: UUID):
    import json

    from app.evaluation.iqr_scorer import IQRScorer, convert_transcript_to_iqr

    session = await _load_session(session_id)
    raw_transcript = session["transcript"]
    transcript_raw = json.loads(raw_transcript) if isinstance(raw_transcript, str) else raw_transcript

    raw_meta = session["metadata"]
    meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})

    raw_payload = {
        "turns": transcript_raw,
        "persona_key": session["persona_id"],
        "session_id": str(session_id),
        "metadata": meta,
    }

    transcript = convert_transcript_to_iqr(raw_payload)
    scorer = IQRScorer()
    result = await scorer.evaluate(transcript)
    return result.model_dump()


@router.post("/eval/sic")
async def eval_sic(session_id: UUID):
    import json

    from app.evaluation.sic_scorer import SICScorer

    session = await _load_session(session_id)
    raw_transcript = session["transcript"]
    transcript_raw = json.loads(raw_transcript) if isinstance(raw_transcript, str) else raw_transcript

    scorer = SICScorer()
    result = await scorer.evaluate(session["persona_id"], transcript_raw)
    return result
