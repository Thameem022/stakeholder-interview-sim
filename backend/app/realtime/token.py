"""Start an interview: create the session and issue a stream token.

SR-2026-052 items 1.1 / 1.6. The browser never receives an AI-provider
credential of any kind. It gets a short-lived, single-use token that opens
the backend's own audio stream (/api/realtime/stream, app/realtime/bedrock_proxy.py);
the backend holds the AWS credentials and talks to Bedrock.

The token is bound to the session and its participant, lives 60 seconds, and
is consumed when the stream opens. Only its hash is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, rate_limited, require_user
from app.config import settings
from app.db import get_pool
from app.observability.audit import audit
from app.personas.voices import DEFAULT_VOICE, VOICE_MAP
from app.realtime.notice import NOTICE_VERSION
from app.realtime.session import InterviewSession

router = APIRouter()

# One interview needs one; the headroom absorbs dropped connections and
# microphone-permission retries.
_TOKEN_LIMIT = ("realtime-token", 30, 3600)
STREAM_TOKEN_TTL = timedelta(seconds=60)


class TokenRequest(BaseModel):
    persona_id: str
    voice_id: Optional[str] = None
    # There is deliberately no `session_id` here. It used to be accepted and
    # fed straight into an upsert, so passing someone else's id wiped their
    # transcript. Pydantic ignores unknown fields, so a stale bundle sending
    # one is a no-op rather than a 422.
    turn_based: Optional[bool] = None
    # The pre-session notice version the student acknowledged. Must match the
    # current one (app/realtime/notice.py) or no interview starts.
    notice_version: Optional[str] = None


class TokenResponse(BaseModel):
    session_id: str
    stream_token: str
    expires_in: int
    voice_id: str


def hash_stream_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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

    # Enforced here, not just in the UI: no session row, no token, until the
    # current notice has been acknowledged.
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

    # Only voices this deployment has chosen; a client cannot name others.
    persona_voice = VOICE_MAP.get(req.persona_id, DEFAULT_VOICE)
    voice_id = req.voice_id if req.voice_id in set(VOICE_MAP.values()) else persona_voice

    sid = uuid4()
    session = InterviewSession(
        id=sid,
        participant_id=user.participant_id,
        notice_version=req.notice_version,
        persona_id=req.persona_id,
        voice_id=voice_id,
        started_at=datetime.now(timezone.utc),
    )
    await session.create()

    token = secrets.token_urlsafe(32)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO realtime_stream_tokens (token_hash, session_id, participant_id, expires_at)
            VALUES ($1, $2, $3, $4)
            """,
            hash_stream_token(token), sid, user.participant_id,
            datetime.now(timezone.utc) + STREAM_TOKEN_TTL,
        )

    audit(
        "ai.realtime_session", "success", actor_user_id=user.id,
        participant_id=user.participant_id, session_id=sid, persona_id=req.persona_id,
        voice_id=voice_id, model=settings.bedrock_speech_model_id, stage="token_issued",
    )
    return TokenResponse(
        session_id=str(sid),
        stream_token=token,
        expires_in=int(STREAM_TOKEN_TTL.total_seconds()),
        voice_id=voice_id,
    )
