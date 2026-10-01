"""SR-2026-052 item 1.2 (SEC-DATA-001): pseudonymous participants.

Student work is keyed only by a pseudonymous participant; the sign-in identity
and its link to the pseudonym live in the restricted `identity` schema; an
interview cannot start until the pre-session notice is acknowledged; audio is
never stored.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import psycopg2
import pytest

from app.realtime.notice import NOTICE_VERSION
from tests.db import _dsn, participant_of, scalar

# Column names that would put a person next to their work.
IDENTIFYING_COLUMNS = {
    "user_id", "email", "wpi_email", "first_name", "last_name", "name",
    "display_name", "wpi_id", "student_id", "entra_subject", "password_hash",
}
# Scenario content: the fictional Harbortown personas, not people.
SCENARIO_TABLES = ["personas", "persona_chunks", "world_bible_chunks"]


def _rows(query: str, args: tuple = ()) -> list[tuple]:
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(query, args)
        return cur.fetchall()


# --- schema: work is keyed only by pseudonym ---------------------------------


def test_no_work_table_carries_an_identifying_column():
    found = _rows(
        """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = ANY(%s)
          AND NOT (table_name = ANY(%s))
        """,
        (list(IDENTIFYING_COLUMNS), SCENARIO_TABLES),
    )
    assert found == [], f"identifying columns outside the identity store: {found}"


def test_interview_sessions_are_owned_by_a_participant():
    nullable = scalar(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'interview_sessions' "
        "AND column_name = 'participant_id'"
    )
    assert nullable == "NO"
    target = scalar(
        """
        SELECT ccu.table_schema || '.' || ccu.table_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_name = 'interview_sessions'
          AND tc.constraint_type = 'FOREIGN KEY'
          AND tc.constraint_name = 'interview_sessions_participant_id_fkey'
        """
    )
    assert target == "public.participants"


def test_nothing_in_the_work_schema_points_into_the_identity_store():
    """The link runs one way only: identity -> participants."""
    crossing = _rows(
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class src ON src.oid = con.conrelid
        JOIN pg_namespace srcns ON srcns.oid = src.relnamespace
        JOIN pg_class dst ON dst.oid = con.confrelid
        JOIN pg_namespace dstns ON dstns.oid = dst.relnamespace
        WHERE con.contype = 'f' AND srcns.nspname = 'public' AND dstns.nspname = 'identity'
        """
    )
    assert crossing == []


def test_sign_in_identity_and_the_mapping_live_in_the_identity_schema():
    tables = {r[0] for r in _rows(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'identity'"
    )}
    assert {"users", "auth_sessions", "pending_registrations", "auth_rate_limits"} <= tables
    public = {r[0] for r in _rows(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )}
    assert not public & {"users", "auth_sessions", "pending_registrations", "auth_rate_limits"}
    # The mapping: one account, one pseudonym, both ways.
    assert scalar(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = 'identity' "
        "AND indexname = 'users_participant_id_key'"
    ) == 1


# --- the restricted store is unreadable without the support-owner role --------


def _as_role(role: str, query: str):
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SET ROLE {role}")
        cur.execute(query)
        return cur.fetchall()


@pytest.fixture
def roles():
    present = {r[0] for r in _rows(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('ses_support_owner', 'ses_course_reader')"
    )}
    if len(present) < 2:
        pytest.skip("roles not created (migration ran without CREATEROLE)")


def test_course_reader_cannot_read_the_identity_store(roles):
    for table in ("users", "auth_sessions", "pending_registrations", "auth_rate_limits"):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            _as_role("ses_course_reader", f"SELECT 1 FROM identity.{table} LIMIT 1")


def test_course_reader_can_read_work_by_pseudonym(roles):
    _as_role("ses_course_reader", "SELECT participant_id, transcript FROM interview_sessions LIMIT 1")
    _as_role("ses_course_reader", "SELECT pseudonymous_code FROM participants LIMIT 1")


def test_support_owner_can_resolve_the_mapping(roles):
    _as_role(
        "ses_support_owner",
        "SELECT u.email, p.pseudonymous_code FROM identity.users u "
        "JOIN participants p USING (participant_id) LIMIT 1",
    )


# --- the app keys everything to the pseudonym --------------------------------


def test_a_new_account_gets_its_own_pseudonym(logged_in_client):
    _, user_id = logged_in_client
    pid = participant_of(user_id)
    assert pid != user_id
    code = scalar("SELECT pseudonymous_code FROM participants WHERE participant_id = %s", (pid,))
    assert re.fullmatch(r"P-[0-9A-F]{10}", code)


@pytest.fixture
def no_network(monkeypatch):
    calls = []

    async def _ok(*args, **kwargs):
        calls.append(args)
        return httpx.Response(200, json={"value": "ek_test_not_real"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _ok)
    return calls


def test_an_interview_is_keyed_to_the_pseudonym_and_records_the_notice(
    logged_in_client, no_network
):
    client, user_id = logged_in_client
    r = client.post(
        "/api/realtime/token",
        json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    owner, version, acked = _rows(
        "SELECT participant_id::text, notice_version, notice_acknowledged_at "
        "FROM interview_sessions WHERE id = %s",
        (sid,),
    )[0]
    assert owner == participant_of(user_id)
    assert version == NOTICE_VERSION and acked is not None


def test_responses_about_your_own_work_never_carry_your_identity(
    logged_in_client, owned_session
):
    client, user_id = logged_in_client
    sid = owned_session(user_id)
    email = scalar("SELECT email FROM identity.users WHERE id = %s", (user_id,))
    r = client.post(
        "/api/realtime/transcript",
        json={"session_id": str(sid), "role": "user", "text": "hello"},
    )
    assert r.status_code == 200
    assert email not in r.text and user_id not in r.text


# --- the pre-session notice blocks the interview ------------------------------


def test_the_notice_is_served_with_its_version(logged_in_client):
    client, _ = logged_in_client
    r = client.get("/api/realtime/notice")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == NOTICE_VERSION
    text = " ".join(body["points"]).lower()
    assert "ai" in text and "personal" in text and "health" in text


@pytest.mark.parametrize("version", [None, "", "1999-01-01"])
def test_no_interview_starts_without_acknowledging_the_current_notice(
    logged_in_client, no_network, version
):
    client, user_id = logged_in_client
    body = {"persona_id": "alex_martinez"}
    if version is not None:
        body["notice_version"] = version
    r = client.post("/api/realtime/token", json=body)

    assert r.status_code == 428
    assert r.json()["detail"]["code"] == "notice_not_acknowledged"
    assert r.json()["detail"]["notice_version"] == NOTICE_VERSION
    # Refused before anything happened: no session row, no credential minted.
    assert no_network == []
    assert scalar(
        "SELECT count(*) FROM interview_sessions WHERE participant_id = %s",
        (participant_of(user_id),),
    ) == 0


def test_the_notice_needs_a_session_like_everything_else(client):
    client.cookies.clear()
    assert client.get("/api/realtime/notice").status_code == 401


# --- audio is never persisted -------------------------------------------------


def test_no_column_could_hold_audio():
    found = _rows(
        """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'identity')
          AND (data_type = 'bytea' OR column_name ILIKE '%%audio%%'
               OR column_name ILIKE '%%recording%%' OR column_name ILIKE '%%voice_sample%%')
        """
    )
    assert found == [], f"a column that could store audio: {found}"


_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_BACKEND = Path(__file__).resolve().parents[1] / "app"


@pytest.mark.skipif(not _FRONTEND.is_dir(), reason="frontend not checked out")
def test_the_browser_never_records_or_stores_audio():
    """Audio leaves the microphone only as a live stream. A recorder or a
    client-side store appearing here would be a new place audio could land."""
    banned = re.compile(r"\bMediaRecorder\b|\bindexedDB\b|\bcreateObjectURL\b")
    hits = [
        f"{p.relative_to(_FRONTEND)}:{i}"
        for p in _FRONTEND.rglob("*.ts*")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if banned.search(line)
    ]
    assert hits == [], f"possible audio capture/storage: {hits}"


def test_the_backend_has_no_audio_write_path():
    banned = re.compile(r"\.(wav|mp3|ogg|webm|pcm)\b|UploadFile|audio_bytes", re.I)
    hits = [
        f"{p.relative_to(_BACKEND)}:{i}"
        for p in _BACKEND.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if banned.search(line)
    ]
    assert hits == [], f"possible audio persistence: {hits}"
