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
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.personas.prompt_assembly import build_persona_system_prompt
from app.personas.voices import VOICE_MAP
from app.realtime.session import InterviewSession

logger = logging.getLogger(__name__)

router = APIRouter()

OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

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
    session_id: Optional[str] = None
    # When true, the OpenAI session is configured so user audio during an
    # in-progress response is IGNORED server-side (interrupt_response=false).
    # Combined with the browser-side mic gating, this guarantees the persona
    # can't be cut off mid-turn.
    turn_based: bool = False


class TokenResponse(BaseModel):
    ephemeral_key: str
    session_id: str
    model: str


def _build_session_config(
    persona_id: str, voice_id: str, turn_based: bool = False
) -> Dict[str, Any]:
    instructions = build_persona_system_prompt(persona_id)

    # Turn-detection config:
    # - interrupt_response=false in turn-based mode → user audio during the
    #   assistant's turn is ignored by the server (no accidental cancels).
    # - threshold: VAD activation threshold (0–1). Default 0.5 picks up
    #   breath, typing, and HVAC. In turn-based mode we raise it to 0.8 so
    #   only clear speech triggers a user turn. Open-mic stays at 0.5 so
    #   normal back-and-forth still feels responsive.
    # - prefix_padding_ms: audio included before VAD trigger, so the first
    #   syllable isn't clipped.
    # - silence_duration_ms: how long of silence before the buffer commits.
    #   700ms in both modes — snappier replies, especially the first one.
    # - create_response=true so user replies still auto-trigger a response —
    #   no buttons.
    turn_detection: Dict[str, Any] = {
        "type": "server_vad",
        "threshold": 0.8 if turn_based else 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 700,
        "create_response": True,
        "interrupt_response": not turn_based,
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
            },
        },
        "tools": [RETRIEVE_TOOL],
        "tool_choice": "auto",
    }


@router.post("/realtime/token", response_model=TokenResponse)
async def mint_token(req: TokenRequest) -> TokenResponse:
    if not req.persona_id:
        raise HTTPException(status_code=400, detail="persona_id required")

    try:
        sid = UUID(req.session_id) if req.session_id else uuid4()
    except ValueError:
        sid = uuid4()

    voice_id = req.voice_id or VOICE_MAP.get(req.persona_id, "alloy")
    session_config = _build_session_config(
        req.persona_id, voice_id, turn_based=req.turn_based
    )

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
            raise HTTPException(status_code=502, detail=f"openai transport error: {e}")

    if resp.status_code >= 400:
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
        logger.error(f"openai client_secrets missing 'value': {data}")
        raise HTTPException(status_code=502, detail="openai response missing ephemeral key")

    session = InterviewSession(
        id=sid,
        persona_id=req.persona_id,
        voice_id=voice_id,
        started_at=datetime.utcnow(),
    )
    await session.persist()

    logger.info(
        f"minted ephemeral key for session={sid} persona={req.persona_id} "
        f"turn_based={req.turn_based}"
    )
    return TokenResponse(
        ephemeral_key=ephemeral_key,
        session_id=str(sid),
        model=settings.openai_realtime_model,
    )
