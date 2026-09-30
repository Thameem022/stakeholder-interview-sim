"""
Realtime ephemeral-token mint endpoint.

Builds the full Realtime session config (persona instructions, voice, server VAD,
the `recall` tool) and exchanges the long-lived OPENAI_API_KEY for a short-lived
ephemeral key the browser uses for the WebRTC SDP exchange.

The browser never sees OPENAI_API_KEY.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Dict, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, rate_limited, require_user
from app.config import settings
from app.db import get_pool
from app.personas.prompt_assembly import build_persona_system_prompt
from app.personas.voices import VOICE_MAP
from app.realtime.session import InterviewSession

logger = logging.getLogger(__name__)

router = APIRouter()

OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"

# Each mint is a credential good for a whole realtime session — the highest
# cost per call in the app. One interview needs one; the headroom absorbs
# dropped connections and microphone-permission retries.
_TOKEN_LIMIT = ("realtime-token", 30, 3600)

async def _persona_topics(persona_id: str) -> list[tuple[str, str]]:
    """(topic, gloss) for the tags this persona actually holds material under.

    Per-persona rather than a global list on purpose: the four personas share
    only part of their tag vocabulary, and offering one of them a tag it has
    nothing filed under invites a call that can only come back empty.

    LEFT JOIN, not INNER: the glossary is loaded from the same files as the
    catalogue but into a different table, so it can lag by one load. A tag
    without a gloss still belongs in the enum — it just goes undescribed —
    whereas dropping it would silently shrink what the persona can be asked
    about.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.topic, COALESCE(g.gloss, '') AS gloss
            FROM (
                SELECT DISTINCT unnest(topics) AS topic
                FROM persona_knowledge
                WHERE persona_id = $1
            ) t
            LEFT JOIN topic_glossary g ON g.topic = t.topic
            ORDER BY t.topic
            """,
            persona_id,
        )
    return [(r["topic"], r["gloss"]) for r in rows]


async def _tier_one_lines(persona_id: str) -> list[str]:
    """Tier-1 `in_voice` text — what this persona volunteers without being dug at.

    Inlined into the instructions so the ordinary run of questions is answered
    from the prompt with no tool call, and therefore no round trip, in the
    middle of a spoken turn. `recall` is then only for what lies past it.

    `claim` is not read here and must not be: see the note in
    app/realtime/recall.py. The prompt gets the sayable form or nothing.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT in_voice
            FROM persona_knowledge
            WHERE persona_id = $1 AND tier = 1
            ORDER BY id
            """,
            persona_id,
        )
    return [r["in_voice"] for r in rows if r["in_voice"]]


def _build_recall_tool(topics: list[tuple[str, str]]) -> Dict[str, Any]:
    
    """The recall tool, with its vocabulary defined rather than merely listed.

    The enum constrains what the model may pass but says nothing about what any
    of it means: `capacity` and `institutional_process` are only self-evident to
    whoever authored the catalogue. Each tag is glossed in the parameter
    description so the choice is informed rather than a guess at English.

    The glosses go on the parameter rather than the tool description because
    they describe how to fill this one argument, and that is where a model
    looks when filling it.
    """
    names = [t for t, _ in topics]
    described = "\n".join(f"- {t}: {g}" for t, g in topics if g)

    detail = (
        "One or two topics closest to what was asked about."
    )
    if described:
        detail += " The topics mean:\n" + described

    return {
        "type": "function",
        "name": "recall",
        "description": (
            "YOUR OWN knowledge, experience and opinions — what you have "
            "lived, seen, worried about, and concluded. Call this when the "
            "interviewer asks what you think, what you have been through, or "
            "for a detail about your own situation that your instructions do "
            "not already cover. Use world_lookup instead for public facts "
            "about the town itself. Choose the closest one or two topics; do "
            "not list every topic that might apply. Skip it for greetings, "
            "small talk, and anything your instructions already answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {"type": "string", "enum": names},
                    "description": detail,
                }
            },
            "required": ["topics"],
        },
    }


WORLD_LOOKUP_TOOL: Dict[str, Any] = {
    "type": "function",
    "name": "world_lookup",
    "description": (
        "PUBLIC information about Harbortown itself — places, studies, "
        "organizations, history, infrastructure, who does what in the town. "
        "Facts anyone living there could look up, not your personal view of "
        "them. Call this when the interviewer asks about the town rather than "
        "about you. Use recall instead for your own knowledge, experience and "
        "opinions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What to look up, in plain words. Name the place, "
                    "organization or study if you know it."
                ),
            }
        },
        "required": ["query"],
    },
}


def _tier_one_block(lines: list[str]) -> str:
    """Render the Tier-1 material as an appended section.

    Appended to the assembled prompt rather than threaded through
    build_persona_system_prompt()'s `persona_context` kwarg: that parameter is
    documented for per-turn injection and is shared with the eval path, and
    this is session-level material that should not move when that path changes.
    """
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return (
        "\n\n## What you already know\n\n"
        "These are yours to say plainly when they come up. You do not need to "
        "look any of them up:\n\n"
        f"{body}\n"
    )


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


class TokenResponse(BaseModel):
    ephemeral_key: str
    session_id: str
    model: str


async def _build_session_config(persona_id: str, voice_id: str) -> Dict[str, Any]:
    instructions = build_persona_system_prompt(persona_id)
    instructions += _tier_one_block(await _tier_one_lines(persona_id))

    topics = await _persona_topics(persona_id)

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
        # A persona with no catalogue rows gets no recall tool rather than
        # one whose enum is empty: an empty enum is a parameter the model
        # can never satisfy, so every call it makes would be rejected.
        # world_lookup has no such dependency — the corpus is shared.
        "tools": ([_build_recall_tool(topics)] if topics else [])
        + [WORLD_LOOKUP_TOOL],
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

    sid = uuid4()
    voice_id = req.voice_id or VOICE_MAP.get(req.persona_id, "alloy")
    session_config = await _build_session_config(req.persona_id, voice_id)

    # The session row is written before the OpenAI call, not after: an ephemeral
    # key is billable, so it should not be minted for a request that is about to
    # fail on our side.
    session = InterviewSession(
        id=sid,
        user_id=user.id,
        persona_id=req.persona_id,
        voice_id=voice_id,
        started_at=datetime.utcnow(),
    )
    await session.create()

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

    logger.info(
        f"minted ephemeral key for session={sid} persona={req.persona_id} user={user.id}"
    )
    return TokenResponse(
        ephemeral_key=ephemeral_key,
        session_id=str(sid),
        model=settings.openai_realtime_model,
    )
