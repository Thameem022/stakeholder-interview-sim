"""The written interview: the same stakeholder, in a typed chat.

SR-2026-052 item 2.1 (SEC-ACC-001). A student who is deaf or hard of hearing,
has a speech disability, has no quiet place to talk, or simply prefers to
write holds the interview here instead of by voice. Nothing about the
assignment changes:

- the same persona system prompt and the same `retrieve_context` grounding;
- the same notice gate, ownership checks, guardrails and audit trail;
- the same transcript shape in `interview_sessions.transcript`, scored by the
  same `/api/eval/iqr` — the only trace of the mode is `interview_sessions.mode`.

    POST /api/realtime/text/start               {persona_id, notice_version} → {session_id}
    POST /api/realtime/text/{session_id}/turns  {text} → {reply, ended, reason}
    POST /api/realtime/text/{session_id}/end

The persona is Claude on Bedrock, through the official SDK, with the backend
holding the credentials exactly as for voice. Each turn rebuilds the
conversation from the stored transcript, so the server — not the browser — is
the record of what was said.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

import anthropic
from anthropic import AsyncAnthropicBedrockMantle, BetaFallbackState, BetaRefusalFallbackMiddleware
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
    BetaToolChoiceAutoParam,
    BetaToolChoiceNoneParam,
    BetaToolParam,
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai import guardrails
from app.auth.dependencies import CurrentUser, deny_session_access, rate_limited, require_user
from app.config import settings
from app.db import get_pool
from app.incidents.flags import flag_session
from app.observability.audit import audit
from app.personas.prompt_assembly import build_persona_system_prompt, get_available_personas
from app.personas.voices import DEFAULT_VOICE, VOICE_MAP
from app.realtime.nova_sonic import RETRIEVE_TOOL_NAME, RETRIEVE_TOOL_SPEC
from app.realtime.retrieve import run_retrieval
from app.realtime.session import InterviewSession
from app.realtime.token import start_session

logger = logging.getLogger(__name__)
router = APIRouter()

# Same budget as starting a voice interview.
_START_LIMIT = ("text-start", 30, 3600)
# A student types far slower than this; it stops a script, not a person.
_TURN_LIMIT = ("text-turn", 240, 3600)

# A long message is fine; a pasted document is not an interview question.
MAX_TURN_CHARS = 2000
# Student turns per interview. A 30-minute voice interview has ~40.
MAX_STUDENT_TURNS = 150
# Bounded tool loop: at most this many retrieval rounds per reply, then the
# persona must answer with what it has.
_MAX_TOOL_ROUNDS = 3
_MAX_QUERY_CHARS = 500
_REPLY_MAX_TOKENS = 2000

WRITTEN_MODE_NOTE = """INTERVIEW FORMAT — WRITTEN:
- This interview is being held in writing, as a typed chat, instead of by voice.
- Reply as you would speak in person: plain conversational prose, in your own voice.
- No Markdown, headings, bullet lists, emoji or stage directions.
- Keep each reply to the length of a spoken answer — usually a few sentences."""

# The voice tool's definition, in Messages API form, so both modes ground the
# persona identically.
_SPEC: dict[str, Any] = RETRIEVE_TOOL_SPEC["toolSpec"]  # type: ignore[assignment]
RETRIEVE_TOOL: BetaToolParam = {
    "name": RETRIEVE_TOOL_NAME,
    "description": _SPEC["description"],
    "input_schema": json.loads(_SPEC["inputSchema"]["json"]),
}

_client: Optional[AsyncAnthropicBedrockMantle] = None


def persona_client() -> AsyncAnthropicBedrockMantle:
    """The SDK client (replaced in tests with one over a mock transport)."""
    global _client
    if _client is None:
        _client = AsyncAnthropicBedrockMantle(
            aws_region=settings.aws_region,
            max_retries=2,
            timeout=60.0,
            middleware=[BetaRefusalFallbackMiddleware(
                [{"model": settings.bedrock_text_persona_fallback_model}]
            )],
        )
    return _client


class PersonaUnavailable(Exception):
    """No usable reply: declined by every model, truncated, or empty."""


class StartRequest(BaseModel):
    persona_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    notice_version: Optional[str] = None


class StartResponse(BaseModel):
    session_id: str
    persona_id: str


class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TURN_CHARS)


class TurnResponse(BaseModel):
    # None when the reply was withheld (guardrail) — `ended` says why.
    reply: Optional[str]
    ended: bool = False
    reason: Optional[str] = None


# One reply at a time per interview. The UI never sends a second message while
# the first is being answered; a client that does gets a 409 rather than two
# replies racing to write the same transcript. (Per process — a second worker
# would let it through, and the cost is one lost turn in the caller's own record.)
_in_flight: set[UUID] = set()


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def _audit(user: CurrentUser, session: InterviewSession, outcome: str, **fields: Any) -> None:
    audit(
        "ai.text_turn", outcome,  # type: ignore[arg-type]
        actor_user_id=user.id, participant_id=user.participant_id, session_id=session.id,
        persona_id=session.persona_id, model=settings.bedrock_text_persona_model, **fields,
    )


def history_messages(session: InterviewSession) -> list[BetaMessageParam]:
    """The transcript as alternating Messages API turns, starting with the student.

    Consecutive turns by the same speaker (a reply that was never produced)
    are joined, so the conversation is always a valid alternation.
    """
    messages: list[BetaMessageParam] = []
    for turn in session.turns:
        role: Literal["user", "assistant"] = "user" if turn.role == "user" else "assistant"
        if not messages and role == "assistant":
            continue
        if messages and messages[-1]["role"] == role:
            previous = messages[-1]["content"]
            messages[-1]["content"] = f"{previous}\n\n{turn.text}"
        else:
            messages.append({"role": role, "content": turn.text})
    return messages


async def _serve_tool(user: CurrentUser, session: InterviewSession, block: Any) -> str:
    if block.name != RETRIEVE_TOOL_NAME:
        return "(unknown tool)"
    query = str((block.input or {}).get("query") or "").strip() if isinstance(block.input, dict) else ""
    # The model's tool arguments are steered by the student, so they are
    # validated like any user input (as on the voice path).
    if not 0 < len(query) <= _MAX_QUERY_CHARS:
        return "(invalid query)"
    try:
        return await run_retrieval(user, session.id, session.persona_id, query)
    except Exception as e:
        logger.warning("retrieval for text tool call failed: %s", type(e).__name__)
        return "(no context available)"


async def persona_reply(user: CurrentUser, session: InterviewSession) -> tuple[str, dict[str, Any]]:
    """The persona's next reply to the conversation so far, plus call metadata."""
    client = persona_client()
    system: list[BetaTextBlockParam] = [{
        "type": "text",
        "text": f"{build_persona_system_prompt(session.persona_id)}\n\n{WRITTEN_MODE_NOTE}",
        # Identical for every turn of the interview; only the messages grow.
        "cache_control": {"type": "ephemeral"},
    }]
    messages = history_messages(session)
    output_config: BetaOutputConfigParam = {"effort": settings.bedrock_text_persona_effort}
    auto: BetaToolChoiceAutoParam = {"type": "auto"}
    none: BetaToolChoiceNoneParam = {"type": "none"}
    meta: dict[str, Any] = {"tool_calls": 0, "input_tokens": 0, "output_tokens": 0}

    for round_ in range(_MAX_TOOL_ROUNDS + 1):
        with BetaFallbackState():
            response = await client.beta.messages.create(
                model=settings.bedrock_text_persona_model,
                max_tokens=_REPLY_MAX_TOKENS,
                system=system,
                messages=messages,
                tools=[RETRIEVE_TOOL],
                # The last round may not call the tool again: answer now.
                tool_choice=auto if round_ < _MAX_TOOL_ROUNDS else none,
                output_config=output_config,
            )
        usage = getattr(response, "usage", None)
        meta["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        meta["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
        meta["model_used"] = response.model

        if response.stop_reason == "refusal":
            raise PersonaUnavailable("refusal")
        uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "tool_use" and uses:
            # The whole assistant turn goes back, tool_use blocks included.
            messages.append({"role": "assistant", "content": response.content})  # type: ignore[typeddict-item]
            results: list[BetaContentBlockParam] = []
            for block in uses:
                meta["tool_calls"] += 1
                results.append({
                    "type": "tool_result", "tool_use_id": block.id,
                    "content": await _serve_tool(user, session, block),
                })
            messages.append({"role": "user", "content": results})
            continue
        text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise PersonaUnavailable(response.stop_reason or "empty")
        # A reply cut at max_tokens is still the persona's words; keep it.
        return text, meta
    raise PersonaUnavailable("tool_rounds")


async def _guard(user: CurrentUser, session: InterviewSession, text: str,
                 source: guardrails.Source) -> bool:
    """Check one turn; on intervention flag the session. True if it intervened."""
    try:
        verdict = await guardrails.check(text, source)
    except Exception as e:
        logger.warning("guardrail check failed: %s", type(e).__name__)
        return False
    if not verdict.intervened:
        return False
    reason = "harmful_ai_output" if source == "OUTPUT" else "sensitive_disclosure"
    pool = await get_pool()
    async with pool.acquire() as conn:
        await flag_session(
            conn, session.id, source="guardrail", reason=reason,  # type: ignore[arg-type]
            note=f"Guardrail intervened on {source.lower()}: " + ", ".join(verdict.policies),
        )
    audit(
        "ai.guardrail", "denied", actor_user_id=user.id, participant_id=user.participant_id,
        session_id=session.id, stage="text_" + source.lower(), policies=list(verdict.policies),
    )
    return True


async def _own_text_session(session_id: UUID, user: CurrentUser) -> InterviewSession:
    # Someone else's session is a 404, same as one that does not exist.
    session = await InterviewSession.load(session_id, user.participant_id)
    if session is None:
        deny_session_access(session_id, user, await InterviewSession.owner_of(session_id))
    if session.mode != "text":
        raise _conflict("not_a_text_interview", "This interview is not a written one.")
    return session


def _over_time(session: InterviewSession) -> bool:
    started = session.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return elapsed > settings.realtime_max_session_minutes * 60


@router.post(
    "/realtime/text/start",
    response_model=StartResponse,
    dependencies=[Depends(rate_limited(*_START_LIMIT))],
)
async def start_text_interview(
    req: StartRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> StartResponse:
    if req.persona_id not in {p["key"] for p in get_available_personas()}:
        raise HTTPException(status_code=400, detail="unknown persona_id")
    session = await start_session(
        user, req.persona_id, req.notice_version, mode="text",
        # Unused in writing; recorded so the row looks like any other.
        voice_id=VOICE_MAP.get(req.persona_id, DEFAULT_VOICE),
    )
    _audit(user, session, "success", stage="start")
    return StartResponse(session_id=str(session.id), persona_id=session.persona_id)


@router.post(
    "/realtime/text/{session_id}/turns",
    response_model=TurnResponse,
    dependencies=[Depends(rate_limited(*_TURN_LIMIT))],
)
async def text_turn(
    session_id: UUID, req: TurnRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> TurnResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    session = await _own_text_session(session_id, user)
    if session.ended_at is not None:
        raise _conflict("interview_ended", "This interview has ended.")
    if _over_time(session):
        await session.persist(ended=True)
        _audit(user, session, "success", stage="end", reason="time_limit",
               turn_count=len(session.turns))
        return TurnResponse(reply=None, ended=True, reason="time_limit")
    if sum(1 for t in session.turns if t.role == "user") >= MAX_STUDENT_TURNS:
        raise _conflict("turn_limit", "This interview has reached its length limit. Please end it.")
    if session.id in _in_flight:
        raise _conflict("turn_in_progress", "Please wait for the reply to your last message.")

    _in_flight.add(session.id)
    started = time.monotonic()
    try:
        # The student's words are the record whether or not a reply follows.
        session.add_turn("user", text)
        await session.persist()
        # Checked alongside the reply: a disclosure is flagged for review, and
        # the interview carries on (as in voice).
        input_check = asyncio.create_task(_guard(user, session, text, "INPUT"))
        try:
            reply, meta = await persona_reply(user, session)
        except (PersonaUnavailable, anthropic.APIError) as e:
            await asyncio.gather(input_check, return_exceptions=True)
            _audit(user, session, "failure", stage="turn", error_type=type(e).__name__,
                   latency_ms=round((time.monotonic() - started) * 1000))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "persona_unavailable",
                        "message": "The stakeholder could not answer just now. "
                                   "Your message was saved — try rephrasing or sending it again."},
            )
        await asyncio.gather(input_check, return_exceptions=True)

        if await _guard(user, session, reply, "OUTPUT"):
            # The reply is withheld — never shown, never in the transcript —
            # and, as in voice, the interview stops for review.
            await session.persist(ended=True)
            _audit(user, session, "denied", stage="turn", reason="guardrail",
                   turn_count=len(session.turns), **meta)
            return TurnResponse(reply=None, ended=True, reason="guardrail")

        session.add_turn("assistant", reply)
        await session.persist()
        _audit(user, session, "success", stage="turn", turn_count=len(session.turns),
               latency_ms=round((time.monotonic() - started) * 1000), **meta)
        return TurnResponse(reply=reply)
    finally:
        _in_flight.discard(session.id)


@router.post("/realtime/text/{session_id}/end")
async def end_text_interview(
    session_id: UUID, user: Annotated[CurrentUser, Depends(require_user)]
) -> dict:
    session = await _own_text_session(session_id, user)
    await session.persist(ended=True)
    _audit(user, session, "success", stage="end", reason="client_ended",
           turn_count=len(session.turns))
    return {"ok": True, "turns": len(session.turns)}
