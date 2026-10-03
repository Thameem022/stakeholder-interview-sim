"""SR-2026-052 item 3.2: a restored backup must not bring back what was removed.

The contract test does the real things through the API — flag, purge,
withdraw — captures the audit events they emit, rewinds the database to the
moment of the "backup", and replays. Field names in those events are the
contract between the emitters and app/jobs/after_restore.py; if either side
changes, this fails.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.jobs.after_restore import events_since, replay
from tests.conftest import sign_in_as
from tests.db import _dsn, participant_of, scalar, sql
from tests.test_audit import audit_log  # noqa: F401  (fixture)
from tests.test_research import (  # noqa: F401  (fixtures)
    TRANSCRIPT,
    _consent,
    _research_copies,
    _score,
    make_user,
    research_on,
    stub_scorers,
)

# The rows a backup holds for a session, and how to put them back.
_TABLES = {
    "session_evaluations": "session_id",
    "research.session_records": "session_id",
    "session_flags": "session_id",
}


def _snapshot(sids: list) -> dict:
    ids = tuple(str(s) for s in sids)
    snap = {
        table: json.loads(scalar(
            f"SELECT coalesce(json_agg(row_to_json(t)), '[]')::text FROM {table} t WHERE {key} IN %s",
            (ids,),
        ))
        for table, key in _TABLES.items()
    }
    snap["sessions"] = json.loads(scalar(
        "SELECT json_agg(json_build_object('id', id, 'transcript', transcript, 'purged_at', purged_at))::text "
        "FROM interview_sessions WHERE id IN %s", (ids,),
    ))
    snap["consent"] = json.loads(scalar(
        "SELECT coalesce(json_agg(row_to_json(c)), '[]')::text FROM research.research_consent c "
        "WHERE participant_id IN (SELECT participant_id FROM interview_sessions WHERE id IN %s)", (ids,),
    ))
    return snap


def _rewind(snap: dict, sids: list) -> None:
    """Put the database back as the snapshot had it — what a restore does."""
    ids = tuple(str(s) for s in sids)
    for table, key in _TABLES.items():
        sql(f"DELETE FROM {table} WHERE {key} IN %s", (ids,))
        for row in snap[table]:
            sql(f"INSERT INTO {table} SELECT * FROM json_populate_record(NULL::{table}, %s::json)",
                (json.dumps(row),))
    for s in snap["sessions"]:
        sql("UPDATE interview_sessions SET transcript = %s::jsonb, purged_at = %s WHERE id = %s",
            (json.dumps(s["transcript"]), s["purged_at"], s["id"]))
    for row in snap["consent"]:
        sql("DELETE FROM research.research_consent WHERE participant_id = %s", (row["participant_id"],))
        sql("INSERT INTO research.research_consent "
            "SELECT * FROM json_populate_record(NULL::research.research_consent, %s::json)",
            (json.dumps(row),))


def _replay(lines, since, *, dry_run=False) -> dict:
    async def go():
        conn = await asyncpg.connect(_dsn())
        try:
            return await replay(conn, events_since(lines, since), dry_run=dry_run)
        finally:
            await conn.close()
    return asyncio.run(go())


def _flag(client, sid):
    r = client.post(f"/api/sessions/{sid}/flags", json={"reason": "sensitive_disclosure"})
    assert r.status_code == 201, r.text
    return r.json()["flag_id"]


def test_replaying_the_audit_log_redoes_what_a_restore_undid(
    client, make_user, owned_session, research_on, stub_scorers, audit_log  # noqa: F811
):
    # Two students taking part in research, each with a scored interview.
    a_email, a = make_user()
    sign_in_as(client, a_email)
    _consent(client, True)
    sid_a = owned_session(a, transcript=TRANSCRIPT)
    _score(client, sid_a)
    b_email, b = make_user()
    sign_in_as(client, b_email)
    _consent(client, True)
    sid_b = owned_session(b, transcript=TRANSCRIPT)
    _score(client, sid_b)
    sids = [sid_a, sid_b]

    # --- the nightly backup is taken here ---
    backup_at = datetime.now(timezone.utc)
    at_backup = _snapshot(sids)

    # After it: A flags their session and the Support Owner purges it; a
    # second flag on B's session is reviewed; B withdraws from research.
    sign_in_as(client, a_email)
    flag_a = _flag(client, sid_a)
    sign_in_as(client, b_email)
    flag_b = _flag(client, sid_b)
    support_email, support = make_user("support_owner")
    sign_in_as(client, support_email)
    assert client.post(f"/api/admin/flags/{flag_a}/purge").status_code == 200
    assert client.post(f"/api/admin/flags/{flag_b}/review").status_code == 200
    sign_in_as(client, b_email)
    _consent(client, False)
    assert _research_copies(a) == 0 and _research_copies(b) == 0

    # --- the server is restored from that backup ---
    _rewind(at_backup, sids)
    assert scalar("SELECT purged_at FROM interview_sessions WHERE id = %s", (str(sid_a),)) is None
    assert _research_copies(a) == 1 and _research_copies(b) == 1
    assert scalar("SELECT count(*) FROM session_flags WHERE session_id IN %s",
                  (tuple(str(s) for s in sids),)) == 0

    # --- and the audit log since the backup is replayed onto it ---
    counts = _replay(audit_log.lines, backup_at)

    assert counts["flags_recreated"] == 2
    assert counts["reviews_reapplied"] == 1
    assert counts["purges_reapplied"] == 1
    assert counts["withdrawals_reapplied"] == 1
    # A's session is purged again, through its original (recreated) flag.
    assert scalar("SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid_a),)) == "[]"
    assert scalar("SELECT purged_at IS NOT NULL FROM interview_sessions WHERE id = %s", (str(sid_a),))
    assert scalar("SELECT count(*) FROM session_evaluations WHERE session_id = %s", (str(sid_a),)) == 0
    assert scalar("SELECT status FROM session_flags WHERE id = %s", (flag_a,)) == "purged"
    assert scalar("SELECT purged_by::text FROM session_flags WHERE id = %s", (flag_a,)) == support
    assert scalar("SELECT source || ':' || reason FROM session_flags WHERE id = %s",
                  (flag_a,)) == "participant:sensitive_disclosure"
    # B's flag is back and reviewed; B is out of research again.
    assert scalar("SELECT status FROM session_flags WHERE id = %s", (flag_b,)) == "reviewed"
    assert _research_copies(a) == 0 and _research_copies(b) == 0
    assert scalar("SELECT consented FROM research.research_consent WHERE participant_id = %s",
                  (participant_of(b),)) is False
    # B's own interview is untouched.
    assert scalar("SELECT transcript::text FROM interview_sessions WHERE id = %s",
                  (str(sid_b),)) != "[]"

    # Replaying again changes nothing.
    again = _replay(audit_log.lines, backup_at)
    assert set(again) <= {"flags_already_present", "reviews_already_applied",
                          "purges_already_applied", "withdrawals_already_applied"}


# --- the log reader -----------------------------------------------------------------------------

SINCE = datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc)


def _line(event: str, at: datetime, outcome: str = "success", **fields) -> str:
    return json.dumps({"ts": at.isoformat(), "type": "audit", "event": event, "outcome": outcome, **fields})


def test_only_successful_replayable_events_since_the_backup_are_read():
    sid = str(uuid.uuid4())
    lines = [
        _line("admin.session_purged", SINCE - timedelta(seconds=1), session_id=sid),   # before
        _line("admin.session_purged", SINCE + timedelta(hours=1), "failure", session_id=sid),
        _line("auth.login", SINCE + timedelta(hours=1)),
        "not json at all",
        '{"type": "app", "event": "admin.session_purged"}',
        # As journalctl prints it without -o cat: a prefix before the JSON.
        "Oct 02 05:00:00 vm python[42]: " + _line("admin.session_purged", SINCE + timedelta(hours=2),
                                                    session_id=sid),
        _line("research.consent", SINCE + timedelta(hours=1), decision="withdrawn"),
    ]
    events = events_since(lines, SINCE)
    assert [(e["event"], e["_at"].hour) for e in events] == [
        ("research.consent", 5), ("admin.session_purged", 6),
    ]


def test_a_dry_run_changes_nothing(logged_in_client, owned_session):
    _, user_id = logged_in_client
    sid = owned_session(user_id, transcript=TRANSCRIPT)
    lines = [_line("admin.session_purged", SINCE + timedelta(minutes=5),
                   session_id=str(sid), flag_id=str(uuid.uuid4()), actor_user_id=user_id)]
    counts = _replay(lines, SINCE, dry_run=True)
    assert counts == {"purges_reapplied": 1}
    assert scalar("SELECT purged_at FROM interview_sessions WHERE id = %s", (str(sid),)) is None
    assert scalar("SELECT count(*) FROM session_flags WHERE session_id = %s", (str(sid),)) == 0


def test_a_purge_without_its_flag_gets_one_so_the_reason_is_recorded(logged_in_client, owned_session):
    _, user_id = logged_in_client
    sid = owned_session(user_id, transcript=TRANSCRIPT)
    lines = [_line("admin.session_purged", SINCE + timedelta(minutes=5),
                   session_id=str(sid), flag_id=str(uuid.uuid4()), actor_user_id=user_id)]
    assert _replay(lines, SINCE) == {"purges_reapplied": 1}
    assert scalar("SELECT source || ':' || reason || ':' || status FROM session_flags "
                  "WHERE session_id = %s", (str(sid),)) == "support:other:purged"
    assert scalar("SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid),)) == "[]"


def test_what_the_backup_never_had_is_counted_not_invented():
    lines = [
        _line("admin.session_purged", SINCE + timedelta(minutes=1), session_id=str(uuid.uuid4())),
        _line("incident.session_flagged", SINCE + timedelta(minutes=1), flag_id=str(uuid.uuid4()),
              session_id=str(uuid.uuid4()), source="participant", reason="distress"),
        _line("incident.session_flagged", SINCE + timedelta(minutes=1), flag_id=str(uuid.uuid4()),
              session_id=str(uuid.uuid4()), source="hacker", reason="distress"),
        _line("research.consent", SINCE + timedelta(minutes=1), participant_id=str(uuid.uuid4()),
              decision="withdrawn"),
        _line("research.consent", SINCE + timedelta(minutes=1), participant_id=str(uuid.uuid4()),
              decision="given"),
    ]
    assert _replay(lines, SINCE) == {
        "purges_session_absent": 1, "flags_session_absent": 1, "skipped_malformed": 1,
        "withdrawals_participant_absent": 1, "consents_not_replayed": 1,
    }


@pytest.mark.parametrize("value", ["2026-10-02T04:00:00Z", "20261002T040000Z"])
def test_since_takes_the_backup_file_timestamp(value):
    from app.jobs.after_restore import parse_since
    assert parse_since(value) == SINCE
