"""Flag a session for review; review and purge flagged sessions.

- A participant may flag their OWN session (e.g. "I shared something I
  shouldn't have"); someone else's answers 404, like every ownership check.
- Instructors and the Support Owner may flag any session.
- Only the Support Owner reviews flags and purges, and a purge always goes
  through a flag — there is no "delete this transcript" endpoint without one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import (
    CurrentUser,
    deny_session_access,
    rate_limited,
    require_role,
    require_user,
)
from app.db import get_pool
from app.incidents.flags import FlagReason, FlagSource, flag_session, purge_flagged_session
from app.observability.audit import audit

router = APIRouter()

_support_owner = require_role("support_owner")
# Enough for a student to report a session or two; not a way to flood reviewers.
_FLAG_LIMIT = ("session-flag", 10, 3600)


class FlagRequest(BaseModel):
    reason: FlagReason
    note: Optional[str] = Field(default=None, max_length=500)


class FlagCreated(BaseModel):
    flag_id: UUID
    status: Literal["open"] = "open"


@router.post(
    "/sessions/{session_id}/flags",
    response_model=FlagCreated,
    status_code=201,
    dependencies=[Depends(rate_limited(*_FLAG_LIMIT))],
)
async def flag(
    session_id: UUID, body: FlagRequest, user: Annotated[CurrentUser, Depends(require_user)]
) -> FlagCreated:
    pool = await get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT participant_id FROM interview_sessions WHERE id = $1", session_id
        )
        source: FlagSource
        if owner == user.participant_id:
            source = "participant"
        elif owner is not None and user.has_role("support_owner"):
            source = "support"
        elif owner is not None and user.has_role("instructor"):
            source = "instructor"
        else:
            deny_session_access(session_id, user, owner)
        flag_id = await flag_session(
            conn, session_id, source=source, reason=body.reason,
            flagged_by=user.id, note=body.note,
        )
    return FlagCreated(flag_id=flag_id)


class FlagView(BaseModel):
    id: UUID
    session_id: Optional[UUID]
    source: str
    reason: str
    note: Optional[str]
    status: str
    created_at: datetime


@router.get("/admin/flags", response_model=list[FlagView])
async def list_flags(
    request: Request, user: Annotated[CurrentUser, Depends(_support_owner)]
) -> list[FlagView]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, session_id, source, reason, note, status, created_at "
            "FROM session_flags WHERE status <> 'purged' ORDER BY created_at"
        )
    audit("admin.flags_read", "success", actor_user_id=user.id, request=request, count=len(rows))
    return [FlagView(**dict(r)) for r in rows]


@router.post("/admin/flags/{flag_id}/review")
async def mark_reviewed(
    flag_id: UUID, request: Request, user: Annotated[CurrentUser, Depends(_support_owner)]
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE session_flags SET status = 'reviewed' WHERE id = $1 AND status = 'open'",
            flag_id,
        )
    if tag.endswith(" 0"):
        raise HTTPException(status_code=404, detail="no open flag with that id")
    audit("admin.flag_reviewed", "success", actor_user_id=user.id, request=request, flag_id=flag_id)
    return {"status": "reviewed"}


@router.post("/admin/flags/{flag_id}/purge")
async def purge(
    flag_id: UUID, request: Request, user: Annotated[CurrentUser, Depends(_support_owner)]
) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await purge_flagged_session(conn, flag_id, purged_by=user.id)
    if result is None:
        audit(
            "admin.session_purged", "failure", actor_user_id=user.id, request=request,
            flag_id=flag_id, reason="no_purgeable_flag",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no purgeable flag with that id",
        )
    audit(
        "admin.session_purged", "success", actor_user_id=user.id, request=request,
        flag_id=flag_id, **result,
    )
    return {"status": "purged", **{k: (str(v) if isinstance(v, UUID) else v) for k, v in result.items()}}
