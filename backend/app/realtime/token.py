"""
Realtime ephemeral-token mint endpoint.

Builds the full Realtime session config (persona instructions, voice, server VAD,
the `retrieve_context` tool) and exchanges the long-lived OPENAI_API_KEY for a
short-lived ephemeral key the browser uses for the WebRTC SDP exchange.

The browser never sees OPENAI_API_KEY.
"""

from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter
from typing import Annotated, Any, Dict, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, rate_limited, require_user
from app.config import settings
from app.observability.audit import audit
from app.personas.prompt_assembly import build_persona_system_prompt
from app.personas.voices import VOICE_MAP
from app.realtime.notice import NOTICE_VERSION
from app.realtime.session import InterviewSession

logger = logging.getLogger(__name__)

router = APIRouter()

OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

# Each mint is a credential good for a whole realtime session — the highest
# cost per call in the app. One interview needs one; the headroom absorbs
# dropped connections and microphone-permission retries.
_TOKEN_LIMIT = ("realtime-token", 30, 3600)

RETRIEVE_TOOL: Dict[str, Any] = {
    "type": "function",
    "name": "retrieve_context",
    "description": (
        "Look up grounded facts about this stakeholder persona or about the "
        "Harbortown world. Call this whenever the user asks a specific factual "
        "question — names, places, plans, history, statistics, opinions on file. "
        "Skip for greetings and small talk."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Concise search query summarizing what to look up",
            }
        },
        "required": ["query"],
    },
}


class TokenRequest(BaseModel):
    persona_id: str
    voice_id: Optional[str] = None
    # There is deliberately no `session_id` here. It used to be accepted and
    # fed straight into an upsert, so passing someone else's id wiped their
    # transcript. No client has ever sent it, so the field is simply gone
    # rather than guarded; Pydantic ignores unknown fields, so a stale bundle
    # sending one is a no-op rather than a 422.
    #
    # TODO(remove after one release cycle): legacy field kept so cached
    # frontend bundles don't 422 mid-rollout. The simulator now always runs
    # in no-barge-in mode regardless of this value.
    turn_based: Optional[bool] = None
    # The pre-session notice version the student acknowledged. Must match the
    # current one (app/realtime/notice.py) or no interview starts.
    notice_version: Optional[str] = None


class TokenResponse(BaseModel):
    ephemeral_key: str
    session_id: str
    model: str


def _build_session_config(persona_id: str, voice_id: str) -> Dict[str, Any]:
    instructions = build_persona_system_prompt(persona_id)

    # No-barge-in turn detection. interrupt_response=false means user audio
    # during the assistant's turn is ignored server-side, so the persona can
    # never be cut off. Combined with the browser-side mic gating in
    # webrtc.ts, this guarantees clean turn-taking.
    turn_detection: Dict[str, Any] = {
        "type": "server_vad",
        "threshold": 0.95,
        "prefix_padding_ms": 400,
        "silence_duration_ms": 1500,
        "create_response": True,
        "interrupt_response": False,
    }

    return {
        "type": "realtime",
        "model": settings.openai_realtime_model,
        "instructions": instructions,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": "whisper-1"},
                "turn_detection": turn_detection,
            },
            "output": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "voice": voice_id,
                # ~20% slower than default. Realtime API supports 0.25–1.5;
                # applied between turns, not mid-response. Gives students a bit
                # more processing time on dense answers (item 7).
                "speed": 0.9,
            },
        },
        "tools": [RETRIEVE_TOOL],
        "tool_choice": "auto",
    }


@router.post(
    "/realtime/token",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limited(*_TOKEN_LIMIT))],
)
async def mint_token(
    req: TokenRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> TokenResponse:
    if not req.persona_id:
        raise HTTPException(status_code=400, detail="persona_id required")

    # Enforced here, not just in the UI: no session row, no credential, until
    # the current notice has been acknowledged.
    if req.notice_version != NOTICE_VERSION:
        audit(
            "interview.notice", "denied", actor_user_id=user.id,
            participant_id=user.participant_id, presented_version=req.notice_version,
        )
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "notice_not_acknowledged",
                "message": "Please read and acknowledge the notice before starting.",
                "notice_version": NOTICE_VERSION,
            },
        )

    sid = uuid4()
    voice_id = req.voice_id or VOICE_MAP.get(req.persona_id, "alloy")
    session_config = _build_session_config(req.persona_id, voice_id)

    # The session row is written before the OpenAI call, not after: an ephemeral
    # key is billable, so it should not be minted for a request that is about to
    # fail on our side.
    session = InterviewSession(
        id=sid,
        participant_id=user.participant_id,
        notice_version=req.notice_version,
        persona_id=req.persona_id,
        voice_id=voice_id,
        started_at=datetime.utcnow(),
    )
    await session.create()

    def _audit_mint(outcome, **fields) -> None:
        audit(
            "ai.realtime_session", outcome, actor_user_id=user.id,
            participant_id=user.participant_id,
            session_id=sid, persona_id=req.persona_id, voice_id=voice_id,
            model=settings.openai_realtime_model,
            latency_ms=round((perf_counter() - started) * 1000), **fields,
        )

    started = perf_counter()
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                OPENAI_CLIENT_SECRETS_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={"session": session_config},
            )
        except httpx.HTTPError as e:
            logger.exception(f"openai client_secrets transport error: {e}")
            _audit_mint("failure", error_type=type(e).__name__)
            raise HTTPException(status_code=502, detail=f"openai transport error: {e}")

    if resp.status_code >= 400:
        _audit_mint("failure", provider_status=resp.status_code)
        logger.error(
            f"openai client_secrets {resp.status_code}: {resp.text[:400]}"
        )
        raise HTTPException(
            status_code=502,
            detail=f"openai client_secrets failed: {resp.status_code}",
        )

    data = resp.json()
    ephemeral_key = data.get("value")
    if not ephemeral_key:
        _audit_mint("failure", error_type="missing_ephemeral_key")
        logger.error(f"openai client_secrets missing 'value': {data}")
        raise HTTPException(status_code=502, detail="openai response missing ephemeral key")

    _audit_mint("success")
    logger.info(
        f"minted ephemeral key for session={sid} persona={req.persona_id} "
        f"participant={user.participant_id}"
    )
    return TokenResponse(
        ephemeral_key=ephemeral_key,
        session_id=str(sid),
        model=settings.openai_realtime_model,
    )
