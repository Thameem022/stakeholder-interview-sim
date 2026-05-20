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


class TokenResponse(BaseModel):
    ephemeral_key: str
    session_id: str
    model: str


def _build_session_config(persona_id: str, voice_id: str) -> Dict[str, Any]:
    instructions = build_persona_system_prompt(persona_id)
    return {
        "type": "realtime",
        "model": settings.openai_realtime_model,
        "instructions": instructions,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 700,
                },
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
    session_config = _build_session_config(req.persona_id, voice_id)

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

    logger.info(f"minted ephemeral key for session={sid} persona={req.persona_id}")
    return TokenResponse(
        ephemeral_key=ephemeral_key,
        session_id=str(sid),
        model=settings.openai_realtime_model,
    )
