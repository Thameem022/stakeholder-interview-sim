"""SR-2026-052 item 1.8 (SEC-RET-001): the retention / deletion job.

The job deletes across the whole database, and the test database may be the
development database. So every test runs on ONE connection inside a
transaction that is always rolled back: the job's own transaction becomes a
savepoint, everything it deletes is visible to the assertions, and none of it
is ever committed.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from app.jobs.retention import RetentionPolicy, run_retention
from app.observability.audit import AUDIT_LOGGER_NAME
from tests.db import _dsn

TERM_END = date(2026, 12, 18)
CUTOFF = datetime(2026, 12, 19, tzinfo=timezone.utc)
DURING_TERM = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
NEXT_TERM = datetime(2027, 1, 20, 15, 0, tzinfo=timezone.utc)
AFTER_GRACE = CUTOFF + timedelta(days=31)
BEFORE_GRACE = CUTOFF + timedelta(days=5)


def policy(**overrides) -> RetentionPolicy:
    base = dict(term_end=TERM_END, course_grace_days=30, telemetry_days=30, research_until=None)
    return RetentionPolicy(**{**base, **overrides})


@pytest.fixture
async def db():
    """A connection whose every change is rolled back at the end of the test."""
    conn = await asyncpg.connect(_dsn())
    tx = conn.transaction()
    await tx.start()
    try:
        yield conn
    finally:
        await tx.rollback()
        await conn.close()


async def make_student(conn, *, created: datetime, role: str | None = None) -> dict:
    pid = await conn.fetchval(
        "INSERT INTO participants (created_at) VALUES ($1) RETURNING participant_id", created
    )
    uid = await conn.fetchval(
        """
        INSERT INTO identity.users (email, first_name, last_name, password_hash, participant_id, created_at)
        VALUES ($1, 'Test', 'Student', 'x', $2, $3) RETURNING id
        """,
        f"sis-test-ret-{uuid.uuid4().hex[:10]}@wpi.edu", pid, created,
    )
    await conn.execute(
        "INSERT INTO identity.auth_sessions (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
        uid, uuid.uuid4().hex, created + timedelta(days=400),
    )
    if role:
        await conn.execute(
            "INSERT INTO identity.account_roles (user_id, role, granted_by) VALUES ($1, $2, 'test')",
            uid, role,
        )
    return {"user_id": uid, "participant_id": pid}


async def make_session(conn, participant_id, *, started: datetime) -> uuid.UUID:
    sid = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO interview_sessions (id, participant_id, persona_id, voice_id, started_at, transcript)
        VALUES ($1, $2, 'alex_martinez', 'sage', $3, '[{"role":"user","text":"hi"}]'::jsonb)
        """,
        sid, participant_id, started,
    )
    await conn.execute(
        "INSERT INTO session_evaluations (session_id, evaluation) VALUES ($1, '{\"overall_score\":5}')", sid
    )
    await conn.execute(
        """
        INSERT INTO retrieval_events (session_id, persona_id, query, k_persona, k_world, created_at)
        VALUES ($1, 'alex_martinez', 'q', 5, 3, $2)
        """,
        sid, started,
    )
    return sid


async def exists(conn, table: str, column: str, value) -> bool:
    return await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {column} = $1)", value)


# --- policy ---------------------------------------------------------------------


def test_course_deletion_is_due_only_after_the_grace_period():
    p = policy()
    assert not p.course_due(BEFORE_GRACE)
    assert not p.course_due(CUTOFF + timedelta(days=30) - timedelta(seconds=1))
    assert p.course_due(CUTOFF + timedelta(days=30))
    assert not policy(term_end=None).course_due(AFTER_GRACE + timedelta(days=999))


def test_bad_configuration_is_rejected(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "retention_term_end", "18/12/2026")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        RetentionPolicy.from_settings()


# --- term end: course data and the mapping ------------------------------------------


async def test_term_end_deletes_course_data_and_the_mapping_with_cascades(db):
    student = await make_student(db, created=DURING_TERM - timedelta(days=30))
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)

    result = await run_retention(db, policy(), now=AFTER_GRACE)

    assert result["status"] == "completed"
    assert not await exists(db, "interview_sessions", "id", sid)
    assert not await exists(db, "session_evaluations", "session_id", sid)
    assert not await exists(db, "retrieval_events", "session_id", sid)
    # The account, its sign-in sessions, and the sign-in <-> pseudonym mapping.
    assert not await exists(db, "identity.users", "id", student["user_id"])
    assert not await exists(db, "identity.auth_sessions", "user_id", student["user_id"])
    assert not await exists(db, "participants", "participant_id", student["participant_id"])
    assert result["counts"]["sessions"] >= 1
    assert result["counts"]["accounts_and_mappings"] >= 1


async def test_nothing_from_the_term_is_deleted_before_the_grace_period(db):
    student = await make_student(db, created=DURING_TERM)
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)
    result = await run_retention(db, policy(), now=BEFORE_GRACE)
    assert result["status"] == "completed"
    assert await exists(db, "interview_sessions", "id", sid)
    assert "sessions" not in result["counts"]


async def test_a_new_terms_work_is_never_touched(db):
    """A term end left in the config must not eat the next term."""
    returning = await make_student(db, created=DURING_TERM)
    old = await make_session(db, returning["participant_id"], started=DURING_TERM)
    new = await make_session(db, returning["participant_id"], started=NEXT_TERM)
    newcomer = await make_student(db, created=NEXT_TERM)
    newcomer_session = await make_session(db, newcomer["participant_id"], started=NEXT_TERM)

    await run_retention(db, policy(), now=NEXT_TERM + timedelta(days=60))

    assert not await exists(db, "interview_sessions", "id", old)
    assert await exists(db, "interview_sessions", "id", new)
    assert await exists(db, "interview_sessions", "id", newcomer_session)
    # The returning student still owns work, so their account and pseudonym stay.
    assert await exists(db, "identity.users", "id", returning["user_id"])
    assert await exists(db, "identity.users", "id", newcomer["user_id"])


async def test_staff_accounts_are_left_to_the_runbook(db):
    staff = await make_student(db, created=DURING_TERM, role="study_personnel")
    await make_session(db, staff["participant_id"], started=DURING_TERM)
    await run_retention(db, policy(), now=AFTER_GRACE)
    assert await exists(db, "identity.users", "id", staff["user_id"])


async def test_a_session_under_an_open_incident_is_held_until_reviewed(db):
    student = await make_student(db, created=DURING_TERM)
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)
    flag = await db.fetchval(
        "INSERT INTO session_flags (session_id, source, reason) "
        "VALUES ($1, 'instructor', 'distress') RETURNING id",
        sid,
    )

    first = await run_retention(db, policy(), now=AFTER_GRACE)
    assert await exists(db, "interview_sessions", "id", sid)
    assert first["counts"]["sessions_held_open_flag"] >= 1

    await db.execute("UPDATE session_flags SET status = 'reviewed' WHERE id = $1", flag)
    await run_retention(db, policy(), now=AFTER_GRACE)
    assert not await exists(db, "interview_sessions", "id", sid)
    assert not await exists(db, "session_flags", "id", flag)


# --- every run: operational data --------------------------------------------------


async def test_query_telemetry_ages_out_on_its_own_short_window(db):
    student = await make_student(db, created=NEXT_TERM)
    sid = await make_session(db, student["participant_id"], started=NEXT_TERM)
    old = await db.fetchval(
        "INSERT INTO retrieval_events (session_id, persona_id, query, k_persona, k_world, created_at) "
        "VALUES ($1, 'p', 'old query', 5, 3, $2) RETURNING id",
        sid, NEXT_TERM - timedelta(days=40),
    )
    await run_retention(db, policy(term_end=None), now=NEXT_TERM)
    assert not await exists(db, "retrieval_events", "id", old)
    assert await exists(db, "retrieval_events", "session_id", sid)  # the recent one


# --- research data follows the protocol, not the term --------------------------------


async def _consented_copy(db, student, sid, consented=True):
    await db.execute(
        "INSERT INTO research.research_consent (participant_id, consented, consent_version) "
        "VALUES ($1, $2, 'v-test')",
        student["participant_id"], consented,
    )
    await db.execute(
        """
        INSERT INTO research.session_records
            (session_id, participant_id, pseudonymous_code, transcript, consent_version)
        VALUES ($1, $2, 'P-TEST', '[]'::jsonb, 'v-test')
        """,
        sid, student["participant_id"],
    )


async def test_research_copies_and_consent_outlive_course_deletion(db):
    student = await make_student(db, created=DURING_TERM)
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)
    await _consented_copy(db, student, sid)

    await run_retention(db, policy(research_until=date(2028, 6, 30)), now=AFTER_GRACE)

    assert not await exists(db, "interview_sessions", "id", sid)  # course copy gone
    assert await exists(db, "research.session_records", "session_id", sid)
    assert await exists(db, "research.research_consent", "participant_id", student["participant_id"])


async def test_research_data_goes_at_the_protocol_end(db):
    student = await make_student(db, created=DURING_TERM)
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)
    await _consented_copy(db, student, sid)
    result = await run_retention(
        db, policy(research_until=date(2027, 6, 30)), now=datetime(2027, 7, 1, tzinfo=timezone.utc)
    )
    assert not await exists(db, "research.session_records", "session_id", sid)
    assert not await exists(db, "research.research_consent", "participant_id", student["participant_id"])
    assert result["counts"]["research_copies"] >= 1


async def test_copies_without_a_live_consent_are_removed_every_run(db):
    student = await make_student(db, created=NEXT_TERM)
    sid = await make_session(db, student["participant_id"], started=NEXT_TERM)
    await _consented_copy(db, student, sid, consented=False)
    await run_retention(db, policy(term_end=None), now=NEXT_TERM)
    assert not await exists(db, "research.session_records", "session_id", sid)


# --- the deletion log, dry runs, failures ---------------------------------------------


async def test_every_run_writes_a_deletion_log_entry_and_an_audit_event(db, caplog):
    student = await make_student(db, created=DURING_TERM)
    await make_session(db, student["participant_id"], started=DURING_TERM)

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    lines: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: lines.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    try:
        result = await run_retention(db, policy(), now=AFTER_GRACE)
    finally:
        logger.removeHandler(handler)

    row = await db.fetchrow("SELECT * FROM deletion_log WHERE id = $1", result["log_id"])
    assert row["status"] == "completed" and row["finished_at"] is not None
    counts = json.loads(row["counts"])
    params = json.loads(row["parameters"])
    assert counts["sessions"] >= 1 and counts["audio_columns_found"] == 0
    assert params["course_due"] is True and params["term_end"] == "2026-12-18"

    (event,) = [json.loads(line) for line in lines if '"admin.retention_run"' in line]
    assert event["outcome"] == "success" and event["log_id"] == str(result["log_id"])


async def test_a_dry_run_counts_but_deletes_nothing(db):
    student = await make_student(db, created=DURING_TERM)
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)
    result = await run_retention(db, policy(), now=AFTER_GRACE, dry_run=True)
    assert result["status"] == "dry_run" and result["counts"]["sessions"] >= 1
    assert await exists(db, "interview_sessions", "id", sid)
    assert await exists(db, "identity.users", "id", student["user_id"])
    assert await db.fetchval(
        "SELECT status FROM deletion_log WHERE id = $1", result["log_id"]
    ) == "dry_run"


async def test_a_column_that_could_hold_audio_fails_the_run_and_deletes_nothing(db):
    student = await make_student(db, created=DURING_TERM)
    sid = await make_session(db, student["participant_id"], started=DURING_TERM)
    await db.execute("ALTER TABLE interview_sessions ADD COLUMN audio_blob bytea")

    result = await run_retention(db, policy(), now=AFTER_GRACE)

    assert result["status"] == "failed"
    assert "interview_sessions.audio_blob" in result["error"]
    assert await exists(db, "interview_sessions", "id", sid)
    assert await db.fetchval(
        "SELECT status FROM deletion_log WHERE id = $1", result["log_id"]
    ) == "failed"
