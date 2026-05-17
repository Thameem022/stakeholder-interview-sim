from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.realtime.openai_proxy import run_realtime_session
from app.realtime.session import InterviewSession

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/interview/{session_id}")
async def interview_ws(ws: WebSocket, session_id: str):
    await ws.accept()

    try:
        try:
            sid = UUID(session_id)
        except ValueError:
            sid = uuid4()

        start_msg = await ws.receive_json()
        if start_msg.get("type") != "start":
            await ws.close(code=1008, reason="first message must be 'start'")
            return

        persona_id = start_msg.get("persona_id")
        voice_id = start_msg.get("voice_id", "")

        if not persona_id:
            await ws.close(code=1008, reason="persona_id required")
            return

        session = InterviewSession(
            id=sid,
            persona_id=persona_id,
            voice_id=voice_id,
            started_at=datetime.utcnow(),
        )

        await session.persist()
        await ws.send_json({"type": "session_started", "session_id": str(sid)})

        await run_realtime_session(ws, session)

        await session.persist(ended=True)

    except WebSocketDisconnect:
        logger.info(f"client disconnected from session {session_id}")
    except Exception as e:
        logger.exception(f"ws error for session {session_id}: {e}")
        try:
            await ws.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass
