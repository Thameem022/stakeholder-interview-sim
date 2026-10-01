"""The retention / deletion job (SR-2026-052 item 1.8, SEC-RET-001).

Run daily by the systemd timer in deploy/systemd/:

    uv run python -m app.jobs.retention            # enforce the schedule
    uv run python -m app.jobs.retention --dry-run  # count only, delete nothing

What it enforces (configured in .env, see app/config.py):

  every run      expired sign-in sessions, stale registrations and rate-limit
                 rows; RAG query telemetry older than RETENTION_TELEMETRY_DAYS;
                 research copies no longer covered by a consent (belt and
                 braces — withdrawal already deletes them); expired, unused
                 export approvals.
  term end       once RETENTION_COURSE_GRACE_DAYS have passed since
                 RETENTION_TERM_END: every interview from on or before that
                 day with its feedback and telemetry, then the student
                 accounts that no longer own any work — and with them the
                 sign-in <-> pseudonym mapping. Sessions with an OPEN incident
                 flag are held until the flag is reviewed or purged.
  protocol end   once RETENTION_RESEARCH_UNTIL has passed: all research copies,
                 consent records and approvals. Before that date research data
                 is untouched — it is retained under the protocol, not the
                 course schedule.
  audio          verifies no column anywhere can hold audio. Audio is never
                 stored; if that ever stops being true the run FAILS loudly.

Everything happens in one transaction: a run deletes all of what is due or
none of it. Each run writes one deletion_log row (counts, never content) and
one audit event.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import asyncpg

from app.config import settings
from app.observability.audit import audit, configure_audit_logging

# Rows that exist only to make sign-in work; kept just long enough to be useful.
_STALE_REGISTRATION_DAYS = 30
_RATE_LIMIT_KEEP = timedelta(days=1)
_UNUSED_APPROVAL_DAYS = 30


@dataclass(frozen=True)
class RetentionPolicy:
    term_end: Optional[date]
    course_grace_days: int
    telemetry_days: int
    research_until: Optional[date]

    @classmethod
    def from_settings(cls) -> "RetentionPolicy":
        def _date(raw: str, name: str) -> Optional[date]:
            raw = raw.strip()
            if not raw:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError:
                raise ValueError(f"{name}={raw!r} is not a YYYY-MM-DD date") from None

        policy = cls(
            term_end=_date(settings.retention_term_end, "RETENTION_TERM_END"),
            course_grace_days=settings.retention_course_grace_days,
            telemetry_days=settings.retention_telemetry_days,
            research_until=_date(settings.retention_research_until, "RETENTION_RESEARCH_UNTIL"),
        )
        if policy.course_grace_days < 0 or policy.telemetry_days < 1:
            raise ValueError("retention periods must be positive")
        return policy

    def course_cutoff(self) -> Optional[datetime]:
        """Records started before this instant belong to the ended term (UTC)."""
        if self.term_end is None:
            return None
        return datetime.combine(self.term_end + timedelta(days=1), time.min, timezone.utc)

    def course_due(self, now: datetime) -> bool:
        cutoff = self.course_cutoff()
        return cutoff is not None and now >= cutoff + timedelta(days=self.course_grace_days)

    def research_due(self, now: datetime) -> bool:
        if self.research_until is None:
            return False
        end = datetime.combine(self.research_until + timedelta(days=1), time.min, timezone.utc)
        return now >= end

    def as_json(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(self).items()}


class AudioStorageFound(RuntimeError):
    pass


def _n(tag: str) -> int:
    return int(tag.rsplit(" ", 1)[-1])


async def _enforce(conn: asyncpg.Connection, policy: RetentionPolicy, now: datetime) -> dict:
    counts: dict[str, int] = {}

    # --- audio: must not exist anywhere ------------------------------------------
    audio = await conn.fetch(
        """
        SELECT table_schema || '.' || table_name || '.' || column_name AS col
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'identity', 'research')
          AND (data_type = 'bytea' OR column_name ILIKE '%audio%'
               OR column_name ILIKE '%recording%')
        """
    )
    if audio:
        raise AudioStorageFound(", ".join(r["col"] for r in audio))
    counts["audio_columns_found"] = 0

    # --- every run: operational data -----------------------------------------------
    counts["auth_sessions_expired"] = _n(await conn.execute(
        "DELETE FROM identity.auth_sessions WHERE expires_at < $1", now
    ))
    counts["registrations_stale"] = _n(await conn.execute(
        """
        DELETE FROM identity.pending_registrations
        WHERE (consumed_at IS NOT NULL OR expires_at < $1) AND created_at < $2
        """,
        now, now - timedelta(days=_STALE_REGISTRATION_DAYS),
    ))
    counts["rate_limit_rows"] = _n(await conn.execute(
        "DELETE FROM identity.auth_rate_limits WHERE occurred_at < $1", now - _RATE_LIMIT_KEEP
    ))
    counts["retrieval_events_aged_out"] = _n(await conn.execute(
        "DELETE FROM retrieval_events WHERE created_at < $1",
        now - timedelta(days=policy.telemetry_days),
    ))
    counts["research_copies_without_consent"] = _n(await conn.execute(
        """
        DELETE FROM research.session_records r
        WHERE NOT EXISTS (
            SELECT 1 FROM research.research_consent c
            WHERE c.participant_id = r.participant_id AND c.consented
        )
        """
    ))
    counts["export_approvals_unused_expired"] = _n(await conn.execute(
        "DELETE FROM research.export_approvals WHERE used_at IS NULL AND expires_at < $1",
        now - timedelta(days=_UNUSED_APPROVAL_DAYS),
    ))

    # --- term end: course data and the identity mapping -----------------------------
    if policy.course_due(now):
        cutoff = policy.course_cutoff()
        # Dropped first: ON COMMIT DROP only fires at the outermost commit, and
        # a caller may run the job twice inside one transaction.
        await conn.execute("DROP TABLE IF EXISTS doomed_sessions")
        await conn.execute(
            """
            CREATE TEMP TABLE doomed_sessions ON COMMIT DROP AS
            SELECT s.id FROM interview_sessions s
            WHERE s.started_at < $1
              AND NOT EXISTS (
                  SELECT 1 FROM session_flags f WHERE f.session_id = s.id AND f.status = 'open'
              )
            """,
            cutoff,
        )
        counts["sessions_held_open_flag"] = await conn.fetchval(
            """
            SELECT count(*) FROM interview_sessions s
            WHERE s.started_at < $1
              AND EXISTS (SELECT 1 FROM session_flags f
                          WHERE f.session_id = s.id AND f.status = 'open')
            """,
            cutoff,
        )
        counts["evaluations"] = _n(await conn.execute(
            "DELETE FROM session_evaluations WHERE session_id IN (SELECT id FROM doomed_sessions)"
        ))
        counts["retrieval_events"] = _n(await conn.execute(
            "DELETE FROM retrieval_events WHERE session_id IN (SELECT id FROM doomed_sessions)"
        ))
        counts["closed_flags"] = _n(await conn.execute(
            "DELETE FROM session_flags WHERE session_id IN (SELECT id FROM doomed_sessions)"
        ))
        counts["sessions"] = _n(await conn.execute(
            "DELETE FROM interview_sessions WHERE id IN (SELECT id FROM doomed_sessions)"
        ))
        # Student accounts from the ended term that own no remaining work. The
        # account IS the sign-in identity and holds the mapping, so this is
        # where the mapping is deleted with the course data. Accounts holding
        # a staff role are not students and are left to the runbook.
        gone = await conn.fetch(
            """
            DELETE FROM identity.users u
            WHERE u.created_at < $1
              AND NOT EXISTS (SELECT 1 FROM identity.account_roles r WHERE r.user_id = u.id)
              AND NOT EXISTS (SELECT 1 FROM interview_sessions s
                              WHERE s.participant_id = u.participant_id)
            RETURNING participant_id
            """,
            cutoff,
        )
        counts["accounts_and_mappings"] = len(gone)
        counts["participants"] = _n(await conn.execute(
            """
            DELETE FROM participants p
            WHERE p.created_at < $1
              AND NOT EXISTS (SELECT 1 FROM identity.users u WHERE u.participant_id = p.participant_id)
              AND NOT EXISTS (SELECT 1 FROM interview_sessions s WHERE s.participant_id = p.participant_id)
            """,
            cutoff,
        ))
        counts["registrations_from_term"] = _n(await conn.execute(
            "DELETE FROM identity.pending_registrations WHERE created_at < $1", cutoff
        ))

    # --- protocol end: research data --------------------------------------------------
    if policy.research_due(now):
        counts["research_copies"] = _n(await conn.execute("DELETE FROM research.session_records"))
        counts["research_consents"] = _n(await conn.execute("DELETE FROM research.research_consent"))
        counts["export_approvals"] = _n(await conn.execute(
            "DELETE FROM research.export_approvals WHERE used_at IS NULL"
        ))

    return counts


async def run_retention(
    conn: asyncpg.Connection,
    policy: RetentionPolicy,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> dict:
    """Enforce the schedule once. Returns {status, counts, log_id}."""
    now = now or datetime.now(timezone.utc)
    parameters = {
        **policy.as_json(),
        "now": now.isoformat(),
        "course_due": policy.course_due(now),
        "research_due": policy.research_due(now),
        "dry_run": dry_run,
    }
    # Logged before the work, so even a crash mid-run leaves a trace.
    log_id = await conn.fetchval(
        "INSERT INTO deletion_log (status, parameters) VALUES ('running', $1::jsonb) RETURNING id",
        json.dumps(parameters),
    )
    status, counts, error = "completed", {}, None
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            counts = await _enforce(conn, policy, now)
        except BaseException:
            await tx.rollback()
            raise
        if dry_run:
            await tx.rollback()
            status = "dry_run"
        else:
            await tx.commit()
    except Exception as e:
        status, error = "failed", f"{type(e).__name__}: {e}"[:500]

    await conn.execute(
        """
        UPDATE deletion_log SET status = $2, counts = $3::jsonb, error = $4, finished_at = now()
        WHERE id = $1
        """,
        log_id, status, json.dumps(counts), error,
    )
    audit(
        "admin.retention_run",
        "failure" if status == "failed" else "success",
        log_id=log_id,
        status=status,
        course_due=parameters["course_due"],
        research_due=parameters["research_due"],
        deleted_total=sum(v for k, v in counts.items() if k != "sessions_held_open_flag"),
        sessions=counts.get("sessions"),
        accounts_and_mappings=counts.get("accounts_and_mappings"),
        error_type=error.split(":", 1)[0] if error else None,
    )
    return {"status": status, "counts": counts, "log_id": log_id, "error": error}


async def _main(dry_run: bool) -> int:
    try:
        policy = RetentionPolicy.from_settings()
    except ValueError as e:
        print(f"retention: bad configuration: {e}", file=sys.stderr)
        # Nothing ran, but a schedule that silently stops is exactly what the
        # audit trail is for.
        audit("admin.retention_run", "failure", status="failed", error_type="ConfigurationError")
        return 2
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        result = await run_retention(conn, policy, dry_run=dry_run)
    finally:
        await conn.close()
    print(json.dumps({**result, "log_id": str(result["log_id"])}, indent=2))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enforce the SES retention schedule.")
    parser.add_argument("--dry-run", action="store_true", help="count what is due; delete nothing")
    args = parser.parse_args()
    configure_audit_logging()
    sys.exit(asyncio.run(_main(args.dry_run)))
