"""Replay, onto a restored database, what happened after the backup was taken.

SR-2026-052 item 3.2 (SEC-BCK-001). A backup is a point in time. Restoring one
would quietly undo every incident action and research withdrawal since — a
purged disclosure would come back, a withdrawn student would be back in the
research set. The audit log keeps going after the backup, so it is the record
to replay from:

    incident.session_flagged   the flag is recreated (same id, source, reason;
                               its free-text note is never in the audit log)
    admin.flag_reviewed        the flag is marked reviewed
    admin.session_purged       the session is purged again
    research.consent           a withdrawal or refusal is applied again, which
                               deletes the participant's research copies

Run it against the RESTORED database, before the application is pointed at it
(see deploy/WPI_DEPLOY.md, "Restoring a backup"), then run the retention job:

    DATABASE_URL=<restored database> uv run python -m app.jobs.after_restore \\
        --since 2026-10-02T04:00:00Z --audit-log audit.jsonl [--dry-run]

`--since` is the backup's timestamp (in its file name) or earlier. Every step
is idempotent, so overlap with the backup is harmless, and so is running it
twice. The audit log is JSON lines as the service writes them; other lines are
skipped. Nothing here pages anyone: these incidents were already handled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from typing import IO, Iterable, Optional, get_args
from uuid import UUID

import asyncpg

from app.config import settings
from app.incidents.flags import FlagReason, FlagSource, purge_flagged_session
from app.observability.audit import audit, configure_audit_logging
from app.research.store import record_decision

REPLAYED = ("incident.session_flagged", "admin.flag_reviewed", "admin.session_purged", "research.consent")
_RECREATED_NOTE = "Recreated from the audit log after a backup restore; the original note is not recoverable."
_PURGE_NOTE = "Purge re-applied after a backup restore."


def _uuid(value: object) -> Optional[UUID]:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def parse_since(value: str) -> datetime:
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("--since needs a time zone (e.g. 2026-10-02T04:00:00Z)")
    return at


def events_since(lines: Iterable[str], since: datetime) -> list[dict]:
    """The replayable, successful audit events at or after `since`, oldest first."""
    found = []
    for line in lines:
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "audit":
            continue
        if event.get("event") not in REPLAYED or event.get("outcome") != "success":
            continue
        try:
            at = datetime.fromisoformat(str(event["ts"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if at >= since:
            event["_at"] = at
            found.append(event)
    found.sort(key=lambda e: e["_at"])
    return found


async def _flagged(conn: asyncpg.Connection, e: dict, counts: Counter) -> None:
    flag_id, sid = _uuid(e.get("flag_id")), _uuid(e.get("session_id"))
    if (flag_id is None or sid is None or e.get("source") not in get_args(FlagSource)
            or e.get("reason") not in get_args(FlagReason)):
        counts["skipped_malformed"] += 1
        return
    if await conn.fetchval("SELECT 1 FROM session_flags WHERE id = $1", flag_id):
        counts["flags_already_present"] += 1
        return
    if not await conn.fetchval("SELECT 1 FROM interview_sessions WHERE id = $1", sid):
        counts["flags_session_absent"] += 1
        return
    await conn.execute(
        """
        INSERT INTO session_flags (id, session_id, source, reason, note, flagged_by, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        flag_id, sid, e.get("source"), e.get("reason"), _RECREATED_NOTE,
        _uuid(e.get("actor_user_id")), e["_at"],
    )
    counts["flags_recreated"] += 1


async def _reviewed(conn: asyncpg.Connection, e: dict, counts: Counter) -> None:
    tag = await conn.execute(
        "UPDATE session_flags SET status = 'reviewed' WHERE id = $1 AND status = 'open'",
        _uuid(e.get("flag_id")),
    )
    counts["reviews_reapplied" if not tag.endswith(" 0") else "reviews_already_applied"] += 1


async def _purged(conn: asyncpg.Connection, e: dict, counts: Counter) -> None:
    sid = _uuid(e.get("session_id"))
    if sid is None:
        counts["skipped_malformed"] += 1
        return
    session = await conn.fetchrow("SELECT purged_at FROM interview_sessions WHERE id = $1", sid)
    if session is None:
        counts["purges_session_absent"] += 1
        return
    if session["purged_at"] is not None:
        counts["purges_already_applied"] += 1
        return
    flag_id = _uuid(e.get("flag_id"))
    if flag_id is None or not await conn.fetchval(
        "SELECT 1 FROM session_flags WHERE id = $1 AND session_id = $2 AND status <> 'purged'",
        flag_id, sid,
    ):
        # A purge goes through a flag, so there is always a recorded reason.
        flag_id = await conn.fetchval(
            """
            INSERT INTO session_flags (session_id, source, reason, note, flagged_by)
            VALUES ($1, 'support', 'other', $2, $3) RETURNING id
            """,
            sid, _PURGE_NOTE, _uuid(e.get("actor_user_id")),
        )
    await purge_flagged_session(conn, flag_id, purged_by=_uuid(e.get("actor_user_id")))
    counts["purges_reapplied"] += 1


async def _consent(conn: asyncpg.Connection, e: dict, counts: Counter) -> None:
    pid = _uuid(e.get("participant_id"))
    if pid is None:
        counts["skipped_malformed"] += 1
        return
    if e.get("decision") not in ("withdrawn", "declined"):
        # A later "yes" lost to the restore just means the student is asked
        # again; nothing is captured for research without a recorded yes.
        counts["consents_not_replayed"] += 1
        return
    if not await conn.fetchval("SELECT 1 FROM participants WHERE participant_id = $1", pid):
        counts["withdrawals_participant_absent"] += 1
        return
    current = await conn.fetchrow(
        "SELECT consented FROM research.research_consent WHERE participant_id = $1", pid
    )
    copies = await conn.fetchval(
        "SELECT count(*) FROM research.session_records WHERE participant_id = $1", pid
    )
    if current is not None and not current["consented"] and not copies:
        counts["withdrawals_already_applied"] += 1
        return
    await record_decision(conn, pid, False, str(e.get("consent_version") or "restored"))
    counts["withdrawals_reapplied"] += 1


_HANDLERS = {
    "incident.session_flagged": _flagged,
    "admin.flag_reviewed": _reviewed,
    "admin.session_purged": _purged,
    "research.consent": _consent,
}


async def replay(conn: asyncpg.Connection, events: list[dict], *, dry_run: bool = False) -> dict:
    """Apply `events` in order, all or nothing. Returns the counts."""
    counts: Counter = Counter()
    tx = conn.transaction()
    await tx.start()
    try:
        for event in events:
            await _HANDLERS[event["event"]](conn, event, counts)
    except BaseException:
        await tx.rollback()
        raise
    if dry_run:
        await tx.rollback()
    else:
        await tx.commit()
    return dict(counts)


async def _main(since: datetime, log: IO[str], dry_run: bool) -> int:
    events = events_since(log, since)
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        counts = await replay(conn, events, dry_run=dry_run)
    finally:
        await conn.close()
    audit("admin.restore_replayed", "success", since=since.isoformat(), dry_run=dry_run,
          events=len(events), **counts)
    print(json.dumps({"since": since.isoformat(), "events": len(events), "dry_run": dry_run,
                      "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Replay incident and consent actions from the audit log onto a restored database."
    )
    parser.add_argument("--since", required=True, help="the backup's timestamp, e.g. 2026-10-02T04:00:00Z")
    parser.add_argument("--audit-log", required=True, help="JSON-lines audit log ('-' for stdin)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; change nothing")
    args = parser.parse_args()
    try:
        since_at = parse_since(args.since)
    except ValueError as e:
        parser.error(str(e))
    configure_audit_logging()
    with (sys.stdin if args.audit_log == "-" else open(args.audit_log, encoding="utf-8")) as f:
        sys.exit(asyncio.run(_main(since_at, f, args.dry_run)))
