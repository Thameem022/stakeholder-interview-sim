"""IRB-27-0033 / SR-2026-052 items 0.1, 0.2, 1.4: research consent and
separation, named-approver exports, session flags and purges."""

from __future__ import annotations

import json
import uuid

import asyncpg
import psycopg2
import pytest

from app.config import Settings, get_settings
from app.realtime.notice import NOTICE_VERSION
from app.research import consent as consent_mod
from app.research.consent import CONSENT_VERSION
from tests.conftest import register, set_password, sign_in_as
from tests.db import _dsn, grant_role, participant_of, scalar, sql
from tests.test_audit import audit_log  # noqa: F401  (fixture)

# --- helpers -----------------------------------------------------------------


@pytest.fixture
def research_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "research_enabled", True)


@pytest.fixture
def make_user(client):
    def _make(*roles: str) -> tuple[str, str]:
        email = f"sis-test-{uuid.uuid4().hex[:12]}@wpi.edu"
        register(client, email)
        r = set_password(client, email)
        assert r.status_code == 200, r.text
        user_id = r.json()["id"]
        for role in roles:
            grant_role(user_id, role)
        return email, user_id

    return _make


@pytest.fixture
def stub_scorers(monkeypatch):
    import app.evaluation.iqr_scorer as iqr
    import app.evaluation.sic_scorer as sic
    from app.evaluation.iqr_schema import SessionEvaluation

    dims = [
        "framing_and_stakeholder_fit", "question_quality_and_precision",
        "probing_and_follow_up_depth", "listening_interpretation_and_stewardship",
    ]

    class _IQR:
        prompt_version = "v-test"
        last_model_used = "judge-test"

        async def evaluate(self, *a, **k):
            return SessionEvaluation(
                dimensions=[{"dimension": d, "score": 6, "assessment": "ok"} for d in dims],
                overall_score=6, skill_label="Developing",
            )

    class _SIC:
        prompt_version = "v-test"
        last_model_used = "grader-test"

        async def evaluate(self, *a, **k):
            return []

    monkeypatch.setattr(iqr, "IQRScorer", _IQR)
    monkeypatch.setattr(sic, "SICScorer", _SIC)


TRANSCRIPT = json.dumps([
    {"role": "user", "text": "What worries you about the plan?", "timestamp": "t"},
    {"role": "assistant", "text": "Mostly the flooding.", "timestamp": "t"},
])


def _consent(client, yes: bool, version: str = CONSENT_VERSION):
    return client.put("/api/research/consent", json={"consented": yes, "version": version})


def _score(client, sid):
    r = client.post(f"/api/eval/iqr?session_id={sid}")
    assert r.status_code == 200, r.text
    return r


def _research_copies(user_id: str) -> int:
    return scalar(
        "SELECT count(*) FROM research.session_records WHERE participant_id = %s",
        (participant_of(user_id),),
    )


# --- consent ------------------------------------------------------------------


def test_consent_is_not_offered_while_research_is_disabled(logged_in_client):
    client, _ = logged_in_client
    assert client.get("/api/research/consent").json() == {
        "enabled": False, "version": None, "title": None, "points": None,
        "yes_label": None, "no_label": None, "consented": None,
    }
    r = _consent(client, True)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "research_disabled"


def test_a_student_records_and_changes_their_own_choice(
    logged_in_client, research_on, audit_log  # noqa: F811
):
    client, user_id = logged_in_client
    first = client.get("/api/research/consent").json()
    assert first["enabled"] and first["consented"] is None and first["version"] == CONSENT_VERSION

    assert _consent(client, True).json()["consented"] is True
    assert client.get("/api/research/consent").json()["consented"] is True
    assert _consent(client, False).json()["consented"] is False

    decisions = [e["decision"] for e in audit_log.named("research.consent")]
    assert decisions == ["given", "withdrawn"]
    assert audit_log.named("research.consent")[0]["participant_id"] == participant_of(user_id)


def test_a_choice_against_stale_wording_is_refused(logged_in_client, research_on):
    client, _ = logged_in_client
    r = _consent(client, True, version="draft-1999")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "consent_version_mismatch"


def test_production_refuses_research_on_draft_consent_text(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "research_enabled", True)
    monkeypatch.setattr(s, "environment", "prod")
    monkeypatch.setattr(consent_mod, "CONSENT_APPROVED", False)
    with pytest.raises(RuntimeError, match="approved form"):
        consent_mod.check_research_gate()
    monkeypatch.setattr(consent_mod, "CONSENT_APPROVED", True)
    consent_mod.check_research_gate()


def test_research_is_off_by_default():
    # The code default, independent of any local .env.
    assert Settings.model_fields["research_enabled"].default is False


# --- capture: only with consent, and invisibly ---------------------------------


def test_a_consented_session_is_copied_into_the_research_store(
    logged_in_client, owned_session, research_on, stub_scorers
):
    client, user_id = logged_in_client
    _consent(client, True)
    sid = owned_session(user_id, transcript=TRANSCRIPT)
    _score(client, sid)
    assert _research_copies(user_id) == 1
    code = scalar(
        "SELECT pseudonymous_code FROM research.session_records WHERE session_id = %s", (str(sid),)
    )
    assert code.startswith("P-")


@pytest.mark.parametrize("choice", [None, False])
def test_no_copy_without_consent(logged_in_client, owned_session, research_on, stub_scorers, choice):
    client, user_id = logged_in_client
    if choice is not None:
        _consent(client, choice)
    _score(client, owned_session(user_id, transcript=TRANSCRIPT))
    assert _research_copies(user_id) == 0


def test_no_copy_while_research_is_disabled(logged_in_client, owned_session, stub_scorers, monkeypatch):
    client, user_id = logged_in_client
    monkeypatch.setattr(get_settings(), "research_enabled", True)
    _consent(client, True)
    monkeypatch.setattr(get_settings(), "research_enabled", False)
    _score(client, owned_session(user_id, transcript=TRANSCRIPT))
    assert _research_copies(user_id) == 0


def test_withdrawing_deletes_research_copies(logged_in_client, owned_session, research_on, stub_scorers):
    client, user_id = logged_in_client
    _consent(client, True)
    _score(client, owned_session(user_id, transcript=TRANSCRIPT))
    assert _research_copies(user_id) == 1
    _consent(client, False)
    assert _research_copies(user_id) == 0


def test_consent_never_appears_in_coursework_responses(
    client, make_user, owned_session, research_on, stub_scorers, monkeypatch
):
    """Same student, same session content, consented vs declined: every
    coursework endpoint must answer identically and never mention consent."""
    import httpx

    async def _ok(*a, **k):
        return httpx.Response(200, json={"value": "ek_test"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _ok)

    def coursework(user_id: str) -> list[str]:
        sid = owned_session(user_id, transcript=TRANSCRIPT)
        bodies = [
            client.get("/api/auth/me"),
            client.get("/api/personas"),
            client.get("/api/voices"),
            client.get("/api/realtime/notice"),
            client.post(
                "/api/realtime/token",
                json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION},
            ),
            client.post("/api/realtime/transcript",
                        json={"session_id": str(sid), "role": "user", "text": "hi"}),
            client.post(f"/api/eval/iqr?session_id={sid}"),
            client.get(f"/api/eval/sessions/{sid}/latest"),
        ]
        for b in bodies:
            assert b.status_code in (200, 201), (b.request.url, b.text)
        return [b.text for b in bodies]

    for choice in (True, False):
        email, user_id = make_user()
        sign_in_as(client, email)
        _consent(client, choice)
        for text in coursework(user_id):
            assert "consent" not in text.lower()
            assert "research" not in text.lower()


# --- research access: approved study personnel only -----------------------------


@pytest.fixture
def consented_record(client, make_user, owned_session, research_on, stub_scorers):
    email, user_id = make_user()
    sign_in_as(client, email)
    _consent(client, True)
    _score(client, owned_session(user_id, transcript=TRANSCRIPT))
    assert _research_copies(user_id) == 1
    return user_id


@pytest.mark.parametrize("roles", [(), ("instructor",), ("export_approver",), ("support_owner",)])
def test_only_study_personnel_can_read_research_data(
    client, make_user, consented_record, roles, audit_log  # noqa: F811
):
    email, _ = make_user(*roles)
    sign_in_as(client, email)
    assert client.get("/api/research/records").status_code == 403
    assert client.post("/api/research/exports", json={}).status_code == 403
    denied = audit_log.named("authz.role")
    assert denied and all(e["outcome"] == "denied" for e in denied)


def test_study_personnel_see_consented_records_by_pseudonym(client, make_user, consented_record):
    email, _ = make_user("study_personnel")
    sign_in_as(client, email)
    r = client.get("/api/research/records")
    assert r.status_code == 200
    codes = [rec["pseudonymous_code"] for rec in r.json()]
    expected = scalar(
        "SELECT pseudonymous_code FROM participants WHERE participant_id = %s",
        (participant_of(consented_record),),
    )
    assert expected in codes
    assert "@" not in r.text


# --- exports: named approver, recorded beforehand ---------------------------------


def _export(client, approval_id=None):
    body = {} if approval_id is None else {"approval_id": str(approval_id)}
    return client.post("/api/research/exports", json=body)


def _log(outcome: str, user_id: str) -> list[tuple]:
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT reason, approver_name, record_count FROM research.export_log "
            "WHERE outcome = %s AND requested_by_user_id = %s ORDER BY created_at",
            (outcome, user_id),
        )
        return cur.fetchall()


def test_an_export_without_an_approval_is_refused_and_logged(
    client, make_user, consented_record, audit_log  # noqa: F811
):
    email, researcher = make_user("study_personnel")
    sign_in_as(client, email)
    r = _export(client)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "no_approval"
    assert _log("refused", researcher) == [("no_approval", None, None)]
    (event,) = audit_log.named("export.research")
    assert event["outcome"] == "denied" and event["actor_user_id"] == researcher


def test_an_approved_export_succeeds_once_and_names_the_approver(
    client, make_user, consented_record, audit_log  # noqa: F811
):
    approver_email, _ = make_user("export_approver")
    researcher_email, researcher = make_user("study_personnel")

    sign_in_as(client, approver_email)
    approval = client.post(
        "/api/research/export-approvals", json={"purpose": "Pilot analysis of probing depth"}
    )
    assert approval.status_code == 201, approval.text
    approval_id = approval.json()["id"]

    sign_in_as(client, researcher_email)
    r = _export(client, approval_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approval"]["approver_name"] == "Alex Rivera"
    assert any(rec["transcript"] for rec in body["records"])
    assert "@" not in r.text

    (logged,) = _log("exported", researcher)
    assert logged[1] == "Alex Rivera" and logged[2] == len(body["records"])
    exported = [e for e in audit_log.named("export.research") if e["outcome"] == "success"]
    assert exported[0]["approver_name"] == "Alex Rivera"

    again = _export(client, approval_id)
    assert again.status_code == 403 and again.json()["detail"]["reason"] == "approval_already_used"


def test_an_approver_cannot_approve_their_own_export(client, make_user, consented_record):
    email, user_id = make_user("export_approver", "study_personnel")
    sign_in_as(client, email)
    approval_id = client.post(
        "/api/research/export-approvals", json={"purpose": "Self-approval attempt here"}
    ).json()["id"]
    r = _export(client, approval_id)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "self_approval"


@pytest.mark.parametrize("reason", ["unknown_approval", "approval_expired"])
def test_unknown_and_expired_approvals_are_refused(client, make_user, consented_record, reason):
    approver_email, _ = make_user("export_approver")
    researcher_email, researcher = make_user("study_personnel")
    sign_in_as(client, approver_email)
    approval_id = client.post(
        "/api/research/export-approvals", json={"purpose": "Pilot analysis of framing"}
    ).json()["id"]
    if reason == "approval_expired":
        sql("UPDATE research.export_approvals SET expires_at = now() - interval '1 minute' "
            "WHERE id = %s", (approval_id,))
    else:
        approval_id = str(uuid.uuid4())
    sign_in_as(client, researcher_email)
    r = _export(client, approval_id)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == reason
    assert _log("refused", researcher)[0][0] == reason


def test_creating_an_approval_needs_the_approver_role(client, make_user):
    email, _ = make_user("study_personnel")
    sign_in_as(client, email)
    r = client.post("/api/research/export-approvals", json={"purpose": "No role to approve this"})
    assert r.status_code == 403


def test_a_withdrawn_participant_is_not_exported(client, make_user, owned_session, research_on, stub_scorers):
    email, user_id = make_user()
    sign_in_as(client, email)
    _consent(client, True)
    _score(client, owned_session(user_id, transcript=TRANSCRIPT))
    _consent(client, False)

    approver_email, _ = make_user("export_approver")
    researcher_email, _ = make_user("study_personnel")
    sign_in_as(client, approver_email)
    approval_id = client.post(
        "/api/research/export-approvals", json={"purpose": "Check withdrawal is honoured"}
    ).json()["id"]
    sign_in_as(client, researcher_email)
    code = scalar("SELECT pseudonymous_code FROM participants WHERE participant_id = %s",
                  (participant_of(user_id),))
    assert code not in _export(client, approval_id).text


# --- database-level separation ----------------------------------------------------


def _as_role(role: str, query: str):
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SET ROLE {role}")
        cur.execute(query)
        return cur.fetchall()


@pytest.fixture
def db_roles():
    have = scalar(
        "SELECT count(*) FROM pg_roles WHERE rolname IN ('ses_course_reader', 'ses_study_personnel')"
    )
    if have < 2:
        pytest.skip("roles not created (migration ran without CREATEROLE)")


@pytest.mark.parametrize("table", ["research_consent", "session_records", "export_approvals", "export_log"])
def test_course_reader_cannot_see_any_research_table(db_roles, table):
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        _as_role("ses_course_reader", f"SELECT 1 FROM research.{table} LIMIT 1")


def test_study_personnel_role_can_read_research(db_roles):
    _as_role("ses_study_personnel", "SELECT consented FROM research.research_consent LIMIT 1")


def test_coursework_schema_never_references_research():
    crossing = scalar(
        """
        SELECT count(*) FROM pg_constraint con
        JOIN pg_class src ON src.oid = con.conrelid
        JOIN pg_namespace s ON s.oid = src.relnamespace
        JOIN pg_class dst ON dst.oid = con.confrelid
        JOIN pg_namespace d ON d.oid = dst.relnamespace
        WHERE con.contype = 'f' AND s.nspname = 'public' AND d.nspname = 'research'
        """
    )
    assert crossing == 0
    assert scalar(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name ILIKE '%%consent%%'"
    ) == 0


# --- flags and purges ---------------------------------------------------------------


def _flag(client, sid, reason="sensitive_disclosure", note=None):
    return client.post(f"/api/sessions/{sid}/flags", json={"reason": reason, "note": note})


def test_a_student_can_flag_their_own_session(logged_in_client, owned_session, audit_log):  # noqa: F811
    client, user_id = logged_in_client
    sid = owned_session(user_id)
    r = _flag(client, sid, note="I mentioned a real family situation by mistake")
    assert r.status_code == 201
    (event,) = audit_log.named("incident.session_flagged")
    assert event["severity"] == "high" and event["source"] == "participant"
    assert event["reason"] == "sensitive_disclosure" and event["session_id"] == str(sid)
    assert "family" not in "\n".join(audit_log.lines)


def test_flagging_someone_elses_session_is_a_404(client, make_user, owned_session):
    _, owner = make_user()
    sid = owned_session(owner)
    email, _ = make_user()
    sign_in_as(client, email)
    assert _flag(client, sid).status_code == 404
    assert scalar("SELECT count(*) FROM session_flags WHERE session_id = %s", (str(sid),)) == 0


def test_an_instructor_can_flag_any_session(client, make_user, owned_session):
    _, owner = make_user()
    sid = owned_session(owner)
    email, _ = make_user("instructor")
    sign_in_as(client, email)
    assert _flag(client, sid, reason="harmful_ai_output").status_code == 201
    assert scalar("SELECT source FROM session_flags WHERE session_id = %s", (str(sid),)) == "instructor"


async def test_an_automated_guardrail_trip_flags_and_notifies(
    logged_in_client, owned_session, audit_log  # noqa: F811
):
    from app.incidents.flags import flag_session

    _, user_id = logged_in_client
    sid = owned_session(user_id)
    conn = await asyncpg.connect(_dsn())
    try:
        await flag_session(conn, sid, source="guardrail", reason="harmful_ai_output")
    finally:
        await conn.close()
    (event,) = audit_log.named("incident.session_flagged")
    assert event["source"] == "guardrail" and event["actor_user_id"] is None


@pytest.mark.parametrize("roles", [(), ("instructor",), ("study_personnel",)])
def test_only_the_support_owner_reviews_and_purges(client, make_user, owned_session, roles):
    _, owner = make_user()
    sid = owned_session(owner)
    email, _ = make_user(*roles)
    sign_in_as(client, email)
    sql("INSERT INTO session_flags (session_id, source, reason) VALUES (%s, 'guardrail', 'other')",
        (str(sid),))
    flag_id = scalar("SELECT id FROM session_flags WHERE session_id = %s", (str(sid),))
    assert client.get("/api/admin/flags").status_code == 403
    assert client.post(f"/api/admin/flags/{flag_id}/purge").status_code == 403
    assert client.post(f"/api/admin/flags/{flag_id}/review").status_code == 403


def test_purge_removes_the_content_everywhere_and_keeps_the_incident_record(
    client, make_user, owned_session, research_on, stub_scorers, audit_log  # noqa: F811
):
    email, user_id = make_user()
    sign_in_as(client, email)
    _consent(client, True)
    sid = owned_session(user_id, transcript=TRANSCRIPT)
    _score(client, sid)
    sql("INSERT INTO retrieval_events (session_id, persona_id, query, k_persona, k_world) "
        "VALUES (%s, 'alex_martinez', 'q', 5, 3)", (str(sid),))
    flag_id = _flag(client, sid).json()["flag_id"]
    assert _research_copies(user_id) == 1

    support_email, support = make_user("support_owner")
    sign_in_as(client, support_email)
    listed = client.get("/api/admin/flags").json()
    assert flag_id in [f["id"] for f in listed]

    r = client.post(f"/api/admin/flags/{flag_id}/purge")
    assert r.status_code == 200, r.text
    assert r.json()["evaluations_deleted"] == 1
    assert r.json()["research_copies_deleted"] == 1

    assert scalar("SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid),)) == "[]"
    assert scalar("SELECT purged_at IS NOT NULL FROM interview_sessions WHERE id = %s", (str(sid),))
    assert scalar("SELECT count(*) FROM session_evaluations WHERE session_id = %s", (str(sid),)) == 0
    assert scalar("SELECT count(*) FROM retrieval_events WHERE session_id = %s", (str(sid),)) == 0
    assert _research_copies(user_id) == 0
    assert scalar("SELECT status FROM session_flags WHERE id = %s", (flag_id,)) == "purged"

    (purged,) = [e for e in audit_log.named("admin.session_purged") if e["outcome"] == "success"]
    assert purged["actor_user_id"] == support and purged["flag_id"] == flag_id

    again = client.post(f"/api/admin/flags/{flag_id}/purge")
    assert again.status_code == 404


def test_purge_only_goes_through_a_flag(client, make_user, audit_log):  # noqa: F811
    email, _ = make_user("support_owner")
    sign_in_as(client, email)
    assert client.post(f"/api/admin/flags/{uuid.uuid4()}/purge").status_code == 404
    (event,) = audit_log.named("admin.session_purged")
    assert event["outcome"] == "failure"
