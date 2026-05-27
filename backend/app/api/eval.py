from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.db import get_pool

logger = logging.getLogger(__name__)

router = APIRouter()


SCORER_METADATA = {
    "iqr_model": "gpt-4o",
    "iqr_fallback_model": "gpt-4o-mini",
    "sic_model": "gpt-4o",
    "sic_fallback_model": "gpt-4o-mini",
    "scorer_version": "1.0",
}


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


def _parse_transcript(raw) -> list:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or []


def _parse_metadata(raw) -> dict:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


async def _persist_evaluation(session_id: UUID, payload: dict[str, Any]) -> None:
    """Insert one row per evaluation run. Failures are logged but never raised —
    a DB write should not break the user-visible score."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_evaluations (session_id, evaluation, scorer_metadata)
                VALUES ($1, $2::jsonb, $3::jsonb)
                """,
                session_id,
                json.dumps(payload),
                json.dumps(SCORER_METADATA),
            )
    except Exception as e:
        logger.warning(f"failed to persist evaluation for session {session_id}: {e}")


@router.post("/eval/iqr")
async def eval_iqr(session_id: UUID):
    """Run IQR and SIC scorers in parallel; persist the merged result; return it."""
    from app.evaluation.iqr_scorer import IQRScorer, convert_transcript_to_iqr
    from app.evaluation.sic_scorer import SICScorer

    session = await _load_session(session_id)
    turns = _parse_transcript(session["transcript"])
    meta = _parse_metadata(session["metadata"])
    persona_id = session["persona_id"]

    iqr_transcript = convert_transcript_to_iqr(
        {
            "turns": turns,
            "persona_key": persona_id,
            "session_id": str(session_id),
            "metadata": meta,
        }
    )

    iqr_scorer = IQRScorer()
    sic_scorer = SICScorer()

    iqr_result, sic_result = await asyncio.gather(
        iqr_scorer.evaluate(iqr_transcript),
        sic_scorer.evaluate(persona_id, turns),
        return_exceptions=True,
    )

    if isinstance(iqr_result, Exception):
        logger.exception(f"IQR scoring failed for session {session_id}: {iqr_result}")
        raise HTTPException(status_code=500, detail=f"IQR scoring failed: {iqr_result}")

    payload = iqr_result.model_dump()

    if isinstance(sic_result, Exception):
        logger.warning(f"SIC scoring failed for session {session_id}: {sic_result}")
        payload["insight_coverage"] = []
        payload["sic_error"] = str(sic_result)
    else:
        payload["insight_coverage"] = sic_result if isinstance(sic_result, list) else []

    await _persist_evaluation(session_id, payload)
    return payload


@router.post("/eval/sic")
async def eval_sic(session_id: UUID):
    """Run SIC scorer standalone (ad-hoc use; the frontend calls /eval/iqr)."""
    from app.evaluation.sic_scorer import SICScorer

    session = await _load_session(session_id)
    turns = _parse_transcript(session["transcript"])

    scorer = SICScorer()
    result = await scorer.evaluate(session["persona_id"], turns)
    return result


@router.get("/eval/sessions/{session_id}/latest")
async def get_latest_evaluation(session_id: str):
    """Return the most recent persisted evaluation for a session, or 404 if none."""
    try:
        sid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid session_id")

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT evaluation, created_at
            FROM session_evaluations
            WHERE session_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            sid,
        )

    if row is None:
        raise HTTPException(status_code=404, detail="no evaluation found for session")

    raw = row["evaluation"]
    return json.loads(raw) if isinstance(raw, str) else raw
