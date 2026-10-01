from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from time import perf_counter
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.ai import guardrails
from app.auth.dependencies import (
    CurrentUser,
    deny_session_access,
    rate_limited,
    require_user,
)
from app.config import settings
from app.db import get_pool
from app.evaluation.sic_scorer import _quote_is_the_students, _student_turn_blob
from app.evaluation.untrusted import INJECTION_GUARD_VERSION
from app.incidents.flags import flag_session
from app.observability.audit import Outcome, audit
from app.research.store import capture_research_copy

logger = logging.getLogger(__name__)

router = APIRouter()

# Two Claude scoring calls over a full transcript — the most expensive request in the
# app. One per interview is normal use; this is roughly ten times that.
_IQR_LIMIT = ("eval-iqr", 20, 3600)

# One Claude scoring call. No frontend caller, so any traffic here is ad-hoc.
_SIC_LIMIT = ("eval-sic", 20, 3600)


# Whisper / turn-splitting artifacts that arrive as isolated short turns.
# Matched against the trimmed turn text — only drops the turn if the WHOLE
# text is a single artifact token. A turn like "Bye, that was helpful" keeps
# the substantive content (the comma-and-rest stops the regex from matching).
_ARTIFACT_RE = re.compile(
    r"^(bye[-\s]?bye|bye|thanks?|thank you|uh|um|you|hmm|mhm)\.?$",
    re.IGNORECASE,
)


def sanitize_transcript(turns: list[dict]) -> list[dict]:
    """Drop isolated speech-to-text artifacts before scoring (item 11).

    Conservative: only removes a turn whose trimmed text matches a single
    artifact token. Any turn containing real content is preserved. Applied
    once before both IQR and SIC see the transcript so noise doesn't
    suppress otherwise strong scores.
    """
    cleaned: list[dict] = []
    for t in turns:
        text = (t.get("text") or "").strip()
        if text and _ARTIFACT_RE.match(text):
            continue
        cleaned.append(t)
    return cleaned



# Proper nouns the speech-to-text layer reliably mangles. Applied to DISPLAYED
# text only, never to what the scorers see: the rubrics already instruct the
# judges to ignore transcription artefacts, and rewriting the scored transcript
# would change what is being graded. This only stops a student's own quote
# coming back to them misspelled.
_PROPER_NOUN_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bthe University of at the Worcester Polytechnic Institute\b", re.I),
     "Worcester Polytechnic Institute"),
    (re.compile(r"\bUniversity of at the Worcester Polytechnic Institute\b", re.I),
     "Worcester Polytechnic Institute"),
    (re.compile(r"\bHar(?:vard|bour|bor)[\s-]?Town\b"), "Harbortown"),
    (re.compile(r"\bEast Har(?:vard|bour|bor)[\s-]?Town\b"), "East Harbor"),
)


def normalize_display_text(text: str | None) -> str | None:
    """Fix obvious proper-noun transcription errors for display.

    Display only. Scoring keeps ignoring artefacts; this never touches the
    transcript the judges receive.
    """
    if not text:
        return text
    for pattern, replacement in _PROPER_NOUN_FIXES:
        text = pattern.sub(replacement, text)
    return text


def _normalize_payload_for_display(payload: dict[str, Any]) -> None:
    """Apply proper-noun normalisation to every string a student reads."""
    for moment in payload.get("moments") or []:
        for field in ("student_quote", "persona_offered", "what_it_produced", "outcome", "headline"):
            if moment.get(field):
                moment[field] = normalize_display_text(moment[field])
    for tier in payload.get("insight_coverage") or []:
        for item in tier.get("items") or []:
            if item.get("evidence_quote"):
                item["evidence_quote"] = normalize_display_text(item["evidence_quote"])
    for turn in (payload.get("metadata") or {}).get("turns") or []:
        turn["text"] = normalize_display_text(turn.get("text"))


def _item_display_state(item: dict[str, Any]) -> str:
    """Earned / opened / not_opened, mirroring the frontend's state model."""
    if item.get("elicited") and item.get("earned_mode") == "earned":
        if item.get("credit_mode") in ("explicit", "explicit_acknowledgment"):
            return "earned"
        return "opened"
    if (
        not item.get("elicited")
        and item.get("type") == "signal"
        and item.get("omission_classification") == "appropriate_non_disclosure"
    ):
        return "opened"
    return "not_opened"


def _reconcile_moments_with_coverage(payload: dict[str, Any]) -> None:
    """Resolve each moment's cited knowledge item against its real grade.

    IQR and SIC are scored in parallel, so the judge writing the moments cannot
    know what the coverage grader decided. It names an item; this resolves that
    name to the state the other tab will show, and the report renders the verb
    from the state. Without this the two tabs are free to contradict each other
    on the same item.
    """
    states: dict[str, str] = {}
    for tier in payload.get("insight_coverage") or []:
        for item in tier.get("items") or []:
            label = item.get("display_label")
            if label:
                states[label] = _item_display_state(item)

    for moment in payload.get("moments") or []:
        label = moment.get("out_of_reach_item")
        if not label:
            continue
        state = states.get(label)
        if state is None:
            # The judge invented a label, or coverage failed entirely. Drop the
            # reference rather than render an item the other tab has never
            # heard of.
            logger.warning(
                "moment cited unknown knowledge item %r; dropping the reference", label
            )
            moment["out_of_reach_item"] = None
            continue
        moment["out_of_reach_state"] = state


def _feedback_text(payload: dict[str, Any]) -> str:
    """Every string the report shows, for the guardrail check."""
    parts: list[str] = []
    for d in payload.get("dimensions") or []:
        parts.append(str(d.get("assessment") or ""))
    for m in payload.get("moments") or []:
        parts.extend(str(m.get(k) or "") for k in (
            "headline", "persona_offered", "what_it_produced", "outcome",
            "technique_name", "technique_stem",
        ))
    for tier in payload.get("insight_coverage") or []:
        parts.extend(str(tier.get(k) or "") for k in ("consequence_text", "quick_win", "actionable_tip"))
    return "\n".join(p for p in parts if p)


def _drop_moments_with_unverified_quotes(payload: dict[str, Any], turns: list[dict]) -> None:
    """Remove any moment whose student_quote the student never said.

    A moment is built around "you said X" — so a quote that is not in a student
    turn is a fabrication, whether the judge hallucinated it or was talked into
    it by text in the transcript. SIC already drops such quotes; a moment
    without its quote has nothing left to stand on, so the whole moment goes.
    """
    blob = _student_turn_blob(turns)
    kept = []
    for moment in payload.get("moments") or []:
        if _quote_is_the_students(moment.get("student_quote") or "", blob):
            kept.append(moment)
        else:
            # Count only: the quote itself is interview text.
            logger.warning(
                "IQR moment quote not found in student turns; dropping moment (dimension=%s)",
                moment.get("dimension"),
            )
    payload["moments"] = kept


SCORER_METADATA = {
    "provider": "aws-bedrock",
    "iqr_model": settings.bedrock_scoring_model,
    "iqr_fallback_model": settings.bedrock_scoring_fallback_model,
    "sic_model": settings.bedrock_scoring_model,
    "sic_fallback_model": settings.bedrock_scoring_fallback_model,
    "scoring_effort": settings.bedrock_scoring_effort,
    "scorer_version": "2.0",
    "injection_guard_version": INJECTION_GUARD_VERSION,
    # Set by the deployment (see deploy/). "unknown" locally, which is honest:
    # a row that cannot name the code that produced it should say so.
    "git_sha": os.getenv("GIT_SHA", "unknown"),
}


async def _load_session(session_id: UUID, user: CurrentUser) -> dict:
    """Load a session the caller owns.

    A session belonging to someone else answers exactly like one that does not
    exist, so holding a UUID never confirms it names anything real. The
    distinction is logged instead — see deny_session_access.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT persona_id, transcript, metadata
            FROM interview_sessions
            WHERE id = $1 AND participant_id = $2
            """,
            session_id,
            user.participant_id,
        )
        if row is None:
            owner = await conn.fetchval(
                "SELECT participant_id FROM interview_sessions WHERE id = $1", session_id
            )
            deny_session_access(session_id, user, owner)
    return dict(row)


def _parse_transcript(raw) -> list:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or []


def _parse_metadata(raw) -> dict:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


async def _persist_evaluation(
    session_id: UUID, payload: dict[str, Any], scorer_metadata: dict[str, Any] | None = None
) -> None:
    """Insert one row per evaluation run. Failures are logged but never raised —
    a DB write should not break the user-visible score.

    No ownership check here: callers reach this only after _load_session has
    already proved the session is theirs.
    """
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
                json.dumps(scorer_metadata or SCORER_METADATA),
            )
    except Exception as e:
        logger.warning(f"failed to persist evaluation for session {session_id}: {e}")


@router.post("/eval/iqr", dependencies=[Depends(rate_limited(*_IQR_LIMIT))])
async def eval_iqr(session_id: UUID, user: Annotated[CurrentUser, Depends(require_user)]):
    """Run IQR and SIC scorers in parallel; persist the merged result; return it."""
    from app.evaluation.iqr_scorer import (
        IQR_WEIGHTS_VERSION,
        IQRScorer,
        convert_transcript_to_iqr,
    )
    from app.evaluation.sic_scorer import SICScorer

    session = await _load_session(session_id, user)
    turns = _parse_transcript(session["transcript"])
    # Strip whisper/turn-splitting artifacts before scoring so noise can't
    # depress IQR/SIC results (item 11).
    turns = sanitize_transcript(turns)
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

    started = perf_counter()
    iqr_result, sic_result = await asyncio.gather(
        iqr_scorer.evaluate(iqr_transcript),
        sic_scorer.evaluate(persona_id, turns),
        return_exceptions=True,
    )

    # Which models graded this run, with which prompts, and whether it worked.
    # Turn counts, never turn text.
    audit(
        "ai.scoring",
        "failure" if isinstance(iqr_result, BaseException) else "success",
        actor_user_id=user.id, participant_id=user.participant_id,
        session_id=session_id,
        persona_id=persona_id,
        scorers=["iqr", "sic"],
        turn_count=len(turns),
        latency_ms=round((perf_counter() - started) * 1000),
        iqr_model_used=iqr_scorer.last_model_used,
        sic_model_used=sic_scorer.last_model_used,
        iqr_prompt_version=iqr_scorer.prompt_version,
        sic_prompt_version=sic_scorer.prompt_version,
        injection_guard_version=INJECTION_GUARD_VERSION,
        iqr_error_type=type(iqr_result).__name__ if isinstance(iqr_result, BaseException) else None,
        sic_error_type=type(sic_result).__name__ if isinstance(sic_result, BaseException) else None,
    )

    # Exception *type* only, in the log and in the response. A scorer failure
    # (an output-parsing error especially) carries the model's raw output in
    # its message, and that output quotes the interview.
    if isinstance(iqr_result, BaseException):
        logger.error(
            "IQR scoring failed for session %s: %s", session_id, type(iqr_result).__name__
        )
        raise HTTPException(status_code=500, detail="IQR scoring failed. Please try again.")

    payload = iqr_result.model_dump()

    # Surface the conversation turns to the frontend score report. The IQR
    # transcript already normalizes role→speaker labels (Student / persona).
    payload.setdefault("metadata", {})
    payload["metadata"]["turns"] = [
        {"turn_id": t.turn_id, "speaker": t.speaker, "text": t.text}
        for t in iqr_transcript.turns
    ]

    if isinstance(sic_result, BaseException):
        logger.warning(
            "SIC scoring failed for session %s: %s", session_id, type(sic_result).__name__
        )
        payload["insight_coverage"] = []
        payload["sic_error"] = type(sic_result).__name__
    else:
        payload["insight_coverage"] = sic_result if isinstance(sic_result, list) else []

    # Both halves are in: settle what the moments claim against what coverage
    # actually graded, then fix displayed proper nouns. Order matters — the
    # reconciliation matches on labels, which normalisation must not touch.
    _drop_moments_with_unverified_quotes(payload, turns)
    _reconcile_moments_with_coverage(payload)
    _normalize_payload_for_display(payload)

    # Bedrock Guardrails on what the student is about to read. Harmful
    # feedback is not stored or shown: the session is flagged for review
    # (the transcript stays for the reviewer) and the student is told so.
    verdict = await guardrails.check(_feedback_text(payload), "OUTPUT")
    if verdict.intervened:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await flag_session(
                conn, session_id, source="guardrail", reason="harmful_ai_output",
                note="Guardrail intervened on scoring feedback: " + ", ".join(verdict.policies),
            )
        audit(
            "ai.guardrail", "denied", actor_user_id=user.id, participant_id=user.participant_id,
            session_id=session_id, stage="feedback", policies=list(verdict.policies),
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "feedback_under_review",
                "message": "Your feedback needs a quick review before it can be shown.",
            },
        )

    await _persist_evaluation(
        session_id,
        payload,
        {
            **SCORER_METADATA,
            # Read off the scorers rather than hardcoded, so bumping a default
            # prompt path to v3 cannot leave these rows claiming v2.
            "iqr_prompt_version": iqr_scorer.prompt_version,
            "sic_prompt_version": sic_scorer.prompt_version,
            # Which model actually graded this run — the scorers downgrade to
            # the fallback on error, and that has to be visible afterwards.
            "iqr_model_used": iqr_scorer.last_model_used,
            "sic_model_used": sic_scorer.last_model_used,
            # Which weighting produced overall_score, so a stored row can never
            # claim a formula it was not scored with.
            "iqr_weights_version": IQR_WEIGHTS_VERSION,
        },
    )
    # Silent and unconditional from this side: the research store decides
    # whether the participant consented, and nothing comes back, so this
    # response is identical either way.
    await capture_research_copy(
        session_id, user.participant_id, persona_id,
        _parse_transcript(session["transcript"]), payload,
    )
    return payload


@router.post("/eval/sic", dependencies=[Depends(rate_limited(*_SIC_LIMIT))])
async def eval_sic(session_id: UUID, user: Annotated[CurrentUser, Depends(require_user)]):
    """Run SIC scorer standalone (ad-hoc use; the frontend calls /eval/iqr)."""
    from app.evaluation.sic_scorer import SICScorer

    session = await _load_session(session_id, user)
    turns = sanitize_transcript(_parse_transcript(session["transcript"]))

    scorer = SICScorer()
    started = perf_counter()
    outcome: Outcome = "failure"
    error_type = None
    try:
        result = await scorer.evaluate(session["persona_id"], turns)
        outcome = "success"
        return result
    except Exception as e:
        error_type = type(e).__name__
        raise
    finally:
        audit(
            "ai.scoring", outcome, actor_user_id=user.id, participant_id=user.participant_id,
            session_id=session_id, persona_id=session["persona_id"],
            scorers=["sic"], turn_count=len(turns),
            latency_ms=round((perf_counter() - started) * 1000),
            sic_model_used=scorer.last_model_used,
            sic_prompt_version=scorer.prompt_version,
            injection_guard_version=INJECTION_GUARD_VERSION,
            sic_error_type=error_type,
        )


@router.get("/eval/sessions/{session_id}/latest")
async def get_latest_evaluation(
    session_id: UUID, user: Annotated[CurrentUser, Depends(require_user)]
):
    """Return the most recent persisted evaluation for a session, or 404 if none.

    Not rate limited: this is one indexed read with no external cost, and
    metering it would mean a database write per cheap read.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ownership comes from the join rather than a participant_id column on
        # session_evaluations — a second copy of the same fact would be free to
        # drift, to save a primary-key lookup.
        row = await conn.fetchrow(
            """
            SELECT e.evaluation, e.created_at
            FROM session_evaluations e
            JOIN interview_sessions s ON s.id = e.session_id
            WHERE e.session_id = $1 AND s.participant_id = $2
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            session_id,
            user.participant_id,
        )

        if row is None:
            # Someone else's session gets the same 404 as one never scored, but
            # the attempt is recorded (deny_session_access audits it).
            owner = await conn.fetchval(
                "SELECT participant_id FROM interview_sessions WHERE id = $1", session_id
            )
            if owner is not None and owner != user.participant_id:
                deny_session_access(session_id, user, owner)

    if row is None:
        raise HTTPException(status_code=404, detail="no evaluation found for session")

    raw = row["evaluation"]
    return json.loads(raw) if isinstance(raw, str) else raw
