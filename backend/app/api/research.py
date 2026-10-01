"""Research participation and research-data access (IRB-27-0033, SEC-PRI-001).

Two audiences, kept apart:

- Any signed-in student: read the consent text and record or change their OWN
  choice. Nothing else here is reachable without a role.
- Approved study personnel: read consented research records and export them —
  but only against an approval recorded beforehand by a named export approver
  (a different person), single-use and expiring. Every refusal is logged in
  research.export_log and the audit trail, not just every success.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.dependencies import CurrentUser, require_role, require_user
from app.config import settings
from app.db import get_pool
from app.observability.audit import audit
from app.research.consent import CONSENT_TEXT, CONSENT_VERSION
from app.research.store import current_consent, record_decision

router = APIRouter()

_study_personnel = require_role("study_personnel")
_export_approver = require_role("export_approver")


# --- the student's own choice -------------------------------------------------


class ConsentView(BaseModel):
    enabled: bool
    version: Optional[str] = None
    title: Optional[str] = None
    points: Optional[list[str]] = None
    yes_label: Optional[str] = None
    no_label: Optional[str] = None
    # The caller's own decision: None until they have made one.
    consented: Optional[bool] = None


class ConsentDecision(BaseModel):
    consented: bool
    version: str = Field(max_length=64)


def _disabled() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "research_disabled", "message": "Research participation is not open."},
    )


@router.get("/research/consent", response_model=ConsentView)
async def get_my_consent(user: Annotated[CurrentUser, Depends(require_user)]) -> ConsentView:
    if not settings.research_enabled:
        return ConsentView(enabled=False)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await current_consent(conn, user.participant_id)
    # A decision made against older wording counts as no decision: the student
    # is asked again with the current text.
    decided = row is not None and row["consent_version"] == CONSENT_VERSION
    return ConsentView(
        enabled=True,
        **CONSENT_TEXT.model_dump(),
        consented=row["consented"] if row is not None and decided else None,
    )


@router.put("/research/consent", response_model=ConsentView)
async def set_my_consent(
    decision: ConsentDecision,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_user)],
) -> ConsentView:
    if not settings.research_enabled:
        raise _disabled()
    if decision.version != CONSENT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "consent_version_mismatch",
                "message": "The consent text has changed. Please read it again.",
                "version": CONSENT_VERSION,
            },
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        outcome = await record_decision(
            conn, user.participant_id, decision.consented, decision.version
        )
    audit(
        "research.consent", "success", actor_user_id=user.id,
        participant_id=user.participant_id, request=request,
        decision=outcome, consent_version=decision.version,
    )
    return ConsentView(enabled=True, **CONSENT_TEXT.model_dump(), consented=decision.consented)


# --- study personnel: read ------------------------------------------------------


class RecordSummary(BaseModel):
    pseudonymous_code: str
    session_id: UUID
    persona_id: Optional[str]
    turn_count: int
    has_evaluation: bool
    captured_at: datetime


@router.get("/research/records", response_model=list[RecordSummary])
async def list_research_records(
    request: Request, user: Annotated[CurrentUser, Depends(_study_personnel)]
) -> list[RecordSummary]:
    """Consented records only — the join re-checks consent at read time, so a
    withdrawal is honoured even before its copies are cleaned up."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.pseudonymous_code, r.session_id, r.persona_id,
                   jsonb_array_length(r.transcript) AS turn_count,
                   r.evaluation IS NOT NULL AS has_evaluation, r.captured_at
            FROM research.session_records r
            JOIN research.research_consent c
              ON c.participant_id = r.participant_id AND c.consented
            ORDER BY r.captured_at
            """
        )
    audit(
        "research.read", "success", actor_user_id=user.id, request=request,
        record_count=len(rows),
    )
    return [RecordSummary(**dict(r)) for r in rows]


# --- export approvals (named approver) -------------------------------------------


class ApprovalRequest(BaseModel):
    purpose: str = Field(min_length=10, max_length=500)


class ApprovalView(BaseModel):
    id: UUID
    approver_name: str
    purpose: str
    expires_at: datetime


@router.post("/research/export-approvals", response_model=ApprovalView, status_code=201)
async def approve_export(
    body: ApprovalRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(_export_approver)],
) -> ApprovalView:
    approver_name = f"{user.first_name} {user.last_name}".strip()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.research_export_approval_hours)
    pool = await get_pool()
    async with pool.acquire() as conn:
        approval_id = await conn.fetchval(
            """
            INSERT INTO research.export_approvals
                (approver_user_id, approver_name, purpose, expires_at)
            VALUES ($1, $2, $3, $4) RETURNING id
            """,
            user.id, approver_name, body.purpose, expires_at,
        )
    audit(
        "research.export_approval", "success", actor_user_id=user.id, request=request,
        approval_id=approval_id, expires_at=expires_at.isoformat(),
    )
    return ApprovalView(
        id=approval_id, approver_name=approver_name, purpose=body.purpose, expires_at=expires_at
    )


# --- export -------------------------------------------------------------------


class ExportRequest(BaseModel):
    # Optional on purpose: a request without one must reach the handler so the
    # refusal is logged, rather than dying as an unlogged 422.
    approval_id: Optional[UUID] = None


async def _refuse(conn, request: Request, user: CurrentUser, reason: str, approval_id) -> HTTPException:
    await conn.execute(
        """
        INSERT INTO research.export_log (requested_by_user_id, approval_id, outcome, reason)
        VALUES ($1, $2, 'refused', $3)
        """,
        user.id, approval_id, reason,
    )
    audit(
        "export.research", "denied", actor_user_id=user.id, request=request,
        approval_id=approval_id, reason=reason,
    )
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "export_not_approved",
            "message": "This export needs a current approval from a named approver.",
            "reason": reason,
        },
    )


@router.post("/research/exports")
async def export_research_data(
    body: ExportRequest,
    request: Request,
    user: Annotated[CurrentUser, Depends(_study_personnel)],
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if body.approval_id is None:
            raise await _refuse(conn, request, user, "no_approval", None)

        async with conn.transaction():
            approval = await conn.fetchrow(
                "SELECT * FROM research.export_approvals WHERE id = $1 FOR UPDATE",
                body.approval_id,
            )
            reason = None
            if approval is None:
                reason = "unknown_approval"
            elif approval["used_at"] is not None:
                reason = "approval_already_used"
            elif approval["expires_at"] <= datetime.now(timezone.utc):
                reason = "approval_expired"
            elif approval["approver_user_id"] == user.id:
                # Two people, always: the approver cannot approve their own export.
                reason = "self_approval"
            if reason is None:
                await conn.execute(
                    "UPDATE research.export_approvals SET used_at = now(), used_by_user_id = $2 "
                    "WHERE id = $1",
                    body.approval_id, user.id,
                )
                rows = await conn.fetch(
                    """
                    SELECT r.pseudonymous_code, r.session_id, r.persona_id, r.transcript,
                           r.evaluation, r.consent_version, r.captured_at
                    FROM research.session_records r
                    JOIN research.research_consent c
                      ON c.participant_id = r.participant_id AND c.consented
                    ORDER BY r.pseudonymous_code, r.captured_at
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO research.export_log
                        (requested_by_user_id, approval_id, approver_name, outcome, record_count)
                    VALUES ($1, $2, $3, 'exported', $4)
                    """,
                    user.id, body.approval_id, approval["approver_name"], len(rows),
                )
        if reason is not None:
            raise await _refuse(conn, request, user, reason, body.approval_id)

    audit(
        "export.research", "success", actor_user_id=user.id, request=request,
        approval_id=body.approval_id, approver_name=approval["approver_name"],
        record_count=len(rows),
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "approval": {
            "id": str(body.approval_id),
            "approver_name": approval["approver_name"],
            "purpose": approval["purpose"],
        },
        "records": [
            {
                "pseudonymous_code": r["pseudonymous_code"],
                "session_id": str(r["session_id"]),
                "persona_id": r["persona_id"],
                "transcript": _json(r["transcript"]),
                "evaluation": _json(r["evaluation"]),
                "consent_version": r["consent_version"],
                "captured_at": r["captured_at"].isoformat(),
            }
            for r in rows
        ],
    }


def _json(raw):
    return json.loads(raw) if isinstance(raw, str) else raw
