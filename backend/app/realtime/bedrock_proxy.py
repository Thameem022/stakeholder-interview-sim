"""The live interview: browser <-> SES backend <-> Amazon Nova Sonic.

SR-2026-052 item 1.1 (SEC-AI-001). The backend is in the audio path, so the
AWS credential never leaves the server, every AI exchange is logged and
guardrailed, and the transcript is written by the server, not reported by the
browser.

    browser                         this module                      Nova Sonic
    ───────                         ───────────                      ──────────
    WebSocket /api/realtime/stream
      {"type":"start","token"}  ──▶ session cookie + Origin + single-use
                                    stream token (bound to the session)
      binary PCM16 16 kHz       ──▶ audioInput (base64)          ──▶
                                ◀── binary PCM16 24 kHz          ◀── audioOutput
      {"type":"transcript"...}  ◀── persisted turns               ◀── textOutput
                                    retrieve_context → pgvector  ◀── toolUse
                                                                  ──▶ toolResult
      {"type":"end"}            ──▶ contentEnd/promptEnd/sessionEnd

A Nova Sonic stream has a bounded lifetime, so the proxy renews it at a turn
boundary after NOVA_SONIC_STREAM_RENEW_SECONDS, replaying the conversation so
far as text into the new stream. Guardrails check every final persona turn
(intervention ends the interview and flags it) and every student turn
(intervention flags it for review). Audio is relayed in memory only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ai import guardrails
from app.auth.dependencies import CurrentUser, user_for_session_token
from app.config import settings
from app.db import get_pool
from app.incidents.flags import flag_session
from app.observability.audit import audit
from app.personas.prompt_assembly import build_persona_system_prompt
from app.realtime.nova_sonic import (
    RETRIEVE_TOOL_NAME,
    Prompt,
    SpeechStream,
    generation_stage,
    open_nova_sonic,
)
from app.realtime.retrieve import run_retrieval
from app.realtime.session import InterviewSession
from app.realtime.token import hash_stream_token

logger = logging.getLogger(__name__)
router = APIRouter()

# Replaced in tests with a fake stream.
open_speech_stream: Callable[[], Awaitable[SpeechStream]] = open_nova_sonic

_MAX_FRAME_BYTES = 64 * 1024
_START_TIMEOUT = 10.0
_MAX_QUERY_CHARS = 500
_CLOSE_UNAUTHORISED = 4401
_CLOSE_FORBIDDEN_ORIGIN = 4403
_CLOSE_BAD_START = 4400


def _origin_allowed(ws: WebSocket) -> bool:
    """WebSockets bypass CORS and CSRF, so the Origin is checked here: this
    site's own origin, the configured public origin, or a CORS-allowed origin."""
    origin = ws.headers.get("origin")
    if not origin:
        return False
    allowed = set(settings.cors_origins)
    if settings.entra_redirect_uri:
        public = urlsplit(settings.entra_redirect_uri)
        allowed.add(f"{public.scheme}://{public.netloc}")
    host = ws.headers.get("host")
    return origin in allowed or (host is not None and urlsplit(origin).netloc == host)


async def _consume_token(token: object, participant_id: UUID) -> Optional[UUID]:
    if not isinstance(token, str) or not token:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            UPDATE realtime_stream_tokens SET used_at = now()
             WHERE token_hash = $1 AND participant_id = $2
               AND used_at IS NULL AND expires_at > now()
            RETURNING session_id
            """,
            hash_stream_token(token), participant_id,
        )


@router.websocket("/realtime/stream")
async def realtime_stream(ws: WebSocket) -> None:
    if not _origin_allowed(ws):
        audit("ai.realtime_session", "denied", reason="origin", origin=ws.headers.get("origin"))
        await ws.close(code=_CLOSE_FORBIDDEN_ORIGIN)
        return
    cookie = ws.cookies.get(settings.auth_cookie_name)
    user = await user_for_session_token(cookie) if cookie else None
    if user is None:
        audit("ai.realtime_session", "denied", reason="no_session")
        await ws.close(code=_CLOSE_UNAUTHORISED)
        return

    await ws.accept()
    try:
        start = await asyncio.wait_for(ws.receive_json(), timeout=_START_TIMEOUT)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await ws.close(code=_CLOSE_BAD_START)
        return
    sid = await _consume_token(start.get("token") if isinstance(start, dict) else None, user.participant_id)
    session = await InterviewSession.load(sid, user.participant_id) if sid else None
    if session is None:
        audit(
            "ai.realtime_session", "denied", actor_user_id=user.id,
            participant_id=user.participant_id, reason="invalid_stream_token",
        )
        await ws.close(code=_CLOSE_UNAUTHORISED)
        return

    await InterviewBridge(ws, user, session).run()


class InterviewBridge:
    def __init__(self, ws: WebSocket, user: CurrentUser, session: InterviewSession) -> None:
        self.ws = ws
        self.user = user
        self.session = session
        self.stream: Optional[SpeechStream] = None
        self.prompt: Optional[Prompt] = None
        self.stream_opened_at = 0.0
        self.renewals = 0
        self.lock = asyncio.Lock()
        self.swapping = False
        self.pending_audio: list[str] = []
        self.content: dict[str, dict] = {}
        self.stop = asyncio.Event()
        self.end_reason = "client_ended"
        self.outcome = "success"
        self.error_type: Optional[str] = None
        self.tasks: set[asyncio.Task] = set()

    # --- lifecycle -----------------------------------------------------------------

    async def run(self) -> None:
        started = time.monotonic()
        try:
            await self._open_stream(history=False)
        except Exception as e:
            self._fail("stream_open_failed", e)
            await self._finish(started)
            return
        self._audit("success", stage="stream_open")
        await self._send_json({"type": "ready", "session_id": str(self.session.id)})

        browser = asyncio.create_task(self._from_browser())
        model = asyncio.create_task(self._from_model())
        limit = asyncio.create_task(asyncio.sleep(settings.realtime_max_session_minutes * 60))
        stopper = asyncio.create_task(self.stop.wait())
        done, pending = await asyncio.wait(
            {browser, model, limit, stopper}, return_when=asyncio.FIRST_COMPLETED
        )
        if limit in done:
            self.end_reason = "time_limit"
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await self._finish(started)

    async def _finish(self, started: float) -> None:
        for task in list(self.tasks):
            task.cancel()
        await self._close_stream()
        try:
            await self.session.persist(ended=True)
        except Exception as e:
            logger.warning("transcript persist failed at end: %s", type(e).__name__)
        try:
            await self._send_json({"type": "ended", "reason": self.end_reason})
            await self.ws.close()
        except Exception:
            pass
        self._audit(
            self.outcome, stage="stream_end", reason=self.end_reason,
            duration_s=round(time.monotonic() - started), turn_count=len(self.session.turns),
            renewals=self.renewals, error_type=self.error_type,
        )

    def _fail(self, reason: str, e: BaseException) -> None:
        self.outcome, self.end_reason, self.error_type = "failure", reason, type(e).__name__
        logger.warning("realtime stream %s: %s", reason, type(e).__name__)

    def _audit(self, outcome: str, **fields) -> None:
        audit(
            "ai.realtime_session", outcome,  # type: ignore[arg-type]
            actor_user_id=self.user.id, participant_id=self.user.participant_id,
            session_id=self.session.id, persona_id=self.session.persona_id,
            voice_id=self.session.voice_id, model=settings.bedrock_speech_model_id, **fields,
        )

    # --- the Nova Sonic stream -------------------------------------------------------

    async def _open_stream(self, history: bool) -> None:
        stream = await open_speech_stream()
        prompt = Prompt(self.session.voice_id)
        system = build_persona_system_prompt(self.session.persona_id)
        events: list[dict] = [prompt.session_start(), prompt.prompt_start()]
        events += prompt.text("SYSTEM", system)
        if history:
            # A renewed stream picks the conversation up where it was.
            for turn in self.session.turns:
                role = "USER" if turn.role == "user" else "ASSISTANT"
                events += prompt.text(role, turn.text)
        events.append(prompt.audio_start())
        for event in events:
            await stream.send(event)
        self.stream, self.prompt, self.stream_opened_at = stream, prompt, time.monotonic()

    async def _close_stream(self) -> None:
        stream, prompt = self.stream, self.prompt
        self.stream = None
        if stream is None or prompt is None:
            return
        try:
            for event in prompt.finish():
                await stream.send(event)
        except Exception:
            pass
        await stream.close()

    async def _renew(self) -> None:
        async with self.lock:
            self.swapping = True
        try:
            await self._close_stream()
            await self._open_stream(history=True)
            self.renewals += 1
        finally:
            async with self.lock:
                self.swapping = False
                queued, self.pending_audio = self.pending_audio, []
            for b64 in queued:
                await self._send_audio(b64)

    async def _send_events(self, events: list[dict]) -> None:
        async with self.lock:
            if self.stream is not None:
                for event in events:
                    await self.stream.send(event)

    async def _send_audio(self, b64: str) -> None:
        async with self.lock:
            if self.swapping or self.stream is None or self.prompt is None:
                self.pending_audio.append(b64)
                return
            await self.stream.send(self.prompt.audio_chunk(b64))

    # --- browser → model -------------------------------------------------------------

    async def _from_browser(self) -> None:
        while True:
            try:
                message = await self.ws.receive()
            except WebSocketDisconnect:
                self.end_reason = "client_disconnected"
                return
            if message.get("type") == "websocket.disconnect":
                self.end_reason = "client_disconnected"
                return
            frame = message.get("bytes")
            if frame is not None:
                # 16-bit samples: an odd length or an oversized frame is not audio.
                if len(frame) % 2 or len(frame) > _MAX_FRAME_BYTES:
                    continue
                await self._send_audio(base64.b64encode(frame).decode("ascii"))
                continue
            try:
                control = json.loads(message.get("text") or "{}")
            except ValueError:
                continue
            if control.get("type") == "end":
                self.end_reason = "client_ended"
                return

    # --- model → browser ---------------------------------------------------------------

    async def _from_model(self) -> None:
        while self.stream is not None:
            stream = self.stream
            renew = False
            try:
                async for event in stream.events():
                    if await self._handle(event):
                        renew = True
                        break
            except Exception as e:
                if self.stream is stream:  # not a stream we closed on purpose
                    self._fail("model_stream_error", e)
                    await self._send_json({"type": "error", "message": "The interview connection was lost."})
                return
            if renew:
                try:
                    await self._renew()
                except Exception as e:
                    self._fail("stream_renew_failed", e)
                    return
                continue
            if self.stream is stream:
                self._fail("model_stream_closed", RuntimeError("closed"))
                return

    async def _handle(self, event: dict) -> bool:
        """Handle one Nova Sonic event. True when the stream should be renewed now."""
        if "contentStart" in event:
            cs = event["contentStart"]
            self.content[cs.get("contentId", "")] = {
                "role": cs.get("role"), "type": cs.get("type"), "stage": generation_stage(cs),
            }
            if cs.get("role") == "ASSISTANT" and cs.get("type") == "AUDIO":
                await self._send_json({"type": "assistant_audio_start"})
        elif "textOutput" in event:
            await self._on_text(event["textOutput"])
        elif "audioOutput" in event:
            try:
                await self.ws.send_bytes(base64.b64decode(event["audioOutput"]["content"]))
            except Exception:
                pass
        elif "toolUse" in event:
            self._spawn(self._serve_tool(event["toolUse"]))
        elif "contentEnd" in event:
            ce = event["contentEnd"]
            meta = self.content.pop(ce.get("contentId", ""), {})
            if meta.get("role") == "ASSISTANT" and meta.get("type") == "AUDIO":
                if ce.get("stopReason") == "INTERRUPTED":
                    await self._send_json({"type": "assistant_interrupted"})
                else:
                    await self._send_json({"type": "assistant_turn_end"})
                    age = time.monotonic() - self.stream_opened_at
                    return age >= settings.nova_sonic_stream_renew_seconds
        return False

    async def _on_text(self, out: dict) -> None:
        text = (out.get("content") or "").strip()
        if not text or text.startswith('{ "interrupted"') or text.startswith('{"interrupted"'):
            return
        meta = self.content.get(out.get("contentId", ""), {})
        role = out.get("role") or meta.get("role")
        if role == "USER":
            await self._record("user", text)
            self._spawn(self._guard(text, "INPUT"))
        elif role == "ASSISTANT":
            if meta.get("stage") == "SPECULATIVE":
                await self._send_json({"type": "transcript", "role": "assistant", "text": text, "final": False})
                return
            await self._record("assistant", text)
            self._spawn(self._guard(text, "OUTPUT"))

    async def _record(self, role: str, text: str) -> None:
        self.session.add_turn(role, text)
        await self._send_json({"type": "transcript", "role": role, "text": text, "final": True})
        try:
            await self.session.persist()
        except Exception as e:
            logger.warning("transcript persist failed: %s", type(e).__name__)

    async def _serve_tool(self, use: dict) -> None:
        tool_use_id = use.get("toolUseId", "")
        result = "(no context available)"
        if use.get("toolName") == RETRIEVE_TOOL_NAME:
            try:
                query = str(json.loads(use.get("content") or "{}").get("query") or "").strip()
            except (ValueError, AttributeError):
                query = ""
            # The model's tool arguments are steered by the student, so they
            # are validated like any user input (see the HTTP endpoint).
            if 0 < len(query) <= _MAX_QUERY_CHARS:
                try:
                    result = await run_retrieval(
                        self.user, self.session.id, self.session.persona_id, query
                    )
                except Exception as e:
                    logger.warning("retrieval for tool call failed: %s", type(e).__name__)
            else:
                result = "(invalid query)"
        if self.prompt is not None:
            await self._send_events(self.prompt.tool_result(tool_use_id, result))

    async def _guard(self, text: str, source: guardrails.Source) -> None:
        try:
            verdict = await guardrails.check(text, source)
        except Exception as e:
            logger.warning("guardrail check failed: %s", type(e).__name__)
            return
        if not verdict.intervened:
            return
        reason = "harmful_ai_output" if source == "OUTPUT" else "sensitive_disclosure"
        pool = await get_pool()
        async with pool.acquire() as conn:
            await flag_session(
                conn, self.session.id, source="guardrail", reason=reason,  # type: ignore[arg-type]
                note=f"Guardrail intervened on {source.lower()}: " + ", ".join(verdict.policies),
            )
        audit(
            "ai.guardrail", "denied", actor_user_id=self.user.id, participant_id=self.user.participant_id,
            session_id=self.session.id, stage="live_" + source.lower(), policies=list(verdict.policies),
        )
        if source == "OUTPUT":
            # The persona said something it must not have: stop the interview.
            self.end_reason = "guardrail"
            self.stop.set()

    # --- helpers -------------------------------------------------------------------------

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _send_json(self, message: dict) -> None:
        try:
            await self.ws.send_json(message)
        except Exception:
            pass
