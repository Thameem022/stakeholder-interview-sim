"""
OpenAI Realtime API proxy.

Bidirectionally bridges the client WebSocket and the OpenAI Realtime WebSocket.
Handles:
- Session initialization with persona instructions + voice
- Per-turn RAG context injection (system message before each response.create)
- Barge-in (client cancel → upstream response.cancel)
- Transcript persistence
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict

import websockets
from fastapi import WebSocket

from app.config import settings
from app.personas.prompt_assembly import build_persona_system_prompt
from app.personas.voices import VOICE_MAP
from app.realtime.session import InterviewSession
from app.vector_store import embed_one, search_persona, search_world

logger = logging.getLogger(__name__)

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


def _build_rag_context(persona_chunks, world_chunks) -> str:
    """Format retrieved chunks as a system-message context block."""
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

    return "\n".join(lines)


async def _send_to_upstream(upstream, event: Dict[str, Any]) -> None:
    await upstream.send(json.dumps(event))


async def _send_to_client(client_ws: WebSocket, event: Dict[str, Any]) -> None:
    await client_ws.send_json(event)


async def _initialize_session(upstream, persona_id: str, voice_id: str) -> None:
    """Send session.update with persona instructions, voice, and audio formats (GA shape)."""
    instructions = build_persona_system_prompt(persona_id)

    session_config = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": "whisper-1"},
                    "turn_detection": None,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": voice_id,
                },
            },
        },
    }
    await _send_to_upstream(upstream, session_config)


async def _inject_rag_and_respond(
    upstream, persona_id: str, user_text: str, state: Dict[str, Any]
) -> None:
    """Run RAG retrieval and inject context, then trigger response generation."""
    t_start = time.perf_counter()

    try:
        query_vec = await embed_one(user_text)
    except Exception as e:
        logger.warning(f"embed failed: {e}")
        query_vec = None
    t_embed = time.perf_counter()

    if query_vec is None:
        persona_chunks, world_chunks = [], []
    else:
        results = await asyncio.gather(
            search_persona(persona_id, user_text, k=5, query_vec=query_vec),
            search_world(user_text, k=3, query_vec=query_vec),
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
    t_search = time.perf_counter()

    context_text = _build_rag_context(persona_chunks, world_chunks)

    await _send_to_upstream(upstream, {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "system",
            "content": [{"type": "input_text", "text": context_text}],
        },
    })

    await _send_to_upstream(upstream, {"type": "response.create"})
    state["t_response_create"] = time.perf_counter()

    embed_ms = int((t_embed - t_start) * 1000)
    search_ms = int((t_search - t_embed) * 1000)
    total_ms = int((time.perf_counter() - t_start) * 1000)
    logger.info(
        f"rag-timings embed={embed_ms}ms search={search_ms}ms total_pre_response={total_ms}ms"
    )


async def _client_to_upstream(
    client_ws: WebSocket,
    upstream,
    session: InterviewSession,
    state: Dict[str, Any],
) -> None:
    """Forward client messages (audio chunks, commit, cancel, end) to upstream."""
    try:
        while True:
            msg = await client_ws.receive_json()
            msg_type = msg.get("type")

            if msg_type == "input_audio_buffer.append":
                await _send_to_upstream(upstream, {
                    "type": "input_audio_buffer.append",
                    "audio": msg["audio"],
                })

            elif msg_type == "commit":
                state["awaiting_transcription"] = True
                await _send_to_upstream(upstream, {"type": "input_audio_buffer.commit"})

            elif msg_type == "cancel":
                await _send_to_upstream(upstream, {"type": "response.cancel"})

            elif msg_type == "end":
                await session.persist(ended=True)
                await upstream.close()
                break

            else:
                logger.warning(f"unknown client message type: {msg_type}")
    except Exception as e:
        logger.info(f"client_to_upstream exit: {e}")


async def _upstream_to_client(
    client_ws: WebSocket,
    upstream,
    session: InterviewSession,
    state: Dict[str, Any],
) -> None:
    """Forward upstream events (audio deltas, transcripts) to client; trigger RAG injection per turn."""
    assistant_text_buffer: list[str] = []

    try:
        async for raw in upstream:
            event = json.loads(raw)
            event_type = event.get("type")

            if event_type in ("response.output_audio.delta", "response.audio.delta"):
                t_resp = state.pop("t_response_create", None)
                if t_resp is not None:
                    ms = int((time.perf_counter() - t_resp) * 1000)
                    logger.info(f"realtime-timings response_create_to_first_audio={ms}ms")
                await _send_to_client(client_ws, {
                    "type": "audio_delta",
                    "audio": event.get("delta", ""),
                })

            elif event_type in (
                "response.output_audio_transcript.delta",
                "response.audio_transcript.delta",
            ):
                assistant_text_buffer.append(event.get("delta", ""))
                await _send_to_client(client_ws, {
                    "type": "assistant_transcript_delta",
                    "delta": event.get("delta", ""),
                })

            elif event_type == "response.done":
                final_text = "".join(assistant_text_buffer).strip()
                if final_text:
                    session.add_turn("assistant", final_text)
                    await session.persist()
                assistant_text_buffer = []
                await _send_to_client(client_ws, {"type": "response_done"})

            elif event_type == "conversation.item.input_audio_transcription.completed":
                user_text = event.get("transcript", "").strip()
                if user_text:
                    session.add_turn("user", user_text)
                    await _send_to_client(client_ws, {
                        "type": "user_transcript",
                        "text": user_text,
                    })
                    if state.pop("awaiting_transcription", False):
                        await _inject_rag_and_respond(
                            upstream, session.persona_id, user_text, state
                        )

            elif event_type == "error":
                logger.error(f"upstream error: {event}")
                await _send_to_client(client_ws, {
                    "type": "error",
                    "error": event.get("error", {}),
                })

    except Exception as e:
        logger.info(f"upstream_to_client exit: {e}")


async def run_realtime_session(
    client_ws: WebSocket,
    session: InterviewSession,
) -> None:
    """Open upstream WS, run both forward/reverse pumps until either side closes."""
    voice_id = session.voice_id or VOICE_MAP.get(session.persona_id, "alloy")

    # GA Realtime API: no OpenAI-Beta header (the v1 beta shape was disabled).
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
    }
    url = f"{OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}"

    logger.info(f"opening upstream WS to {url} (voice={voice_id})")

    try:
        upstream_cm = websockets.connect(url, additional_headers=headers)
    except TypeError:
        # websockets <14 used `extra_headers` instead of `additional_headers`
        upstream_cm = websockets.connect(url, extra_headers=headers)

    try:
        async with upstream_cm as upstream:
            logger.info("upstream connected; sending session.update")
            await _initialize_session(upstream, session.persona_id, voice_id)
            logger.info("session.update sent; notifying client session_ready")
            await _send_to_client(client_ws, {"type": "session_ready"})

            state: Dict[str, Any] = {"awaiting_transcription": False}

            await asyncio.gather(
                _client_to_upstream(client_ws, upstream, session, state),
                _upstream_to_client(client_ws, upstream, session, state),
                return_exceptions=True,
            )
    except Exception as e:
        logger.exception(f"upstream connection failed: {e}")
        try:
            await _send_to_client(client_ws, {
                "type": "error",
                "error": {"message": f"upstream connection failed: {e}"},
            })
        except Exception:
            pass
        raise
