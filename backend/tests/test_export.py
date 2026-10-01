"""SR-2026-052 item 1.9 (SEC-DATA-001): students download their own materials."""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest

from tests.conftest import register, set_password, sign_in_as
from tests.db import participant_of, scalar, sql
from tests.test_audit import audit_log  # noqa: F401  (fixture)

TRANSCRIPT = json.dumps([
    {"role": "user", "text": "How would the seawall affect your street?", "timestamp": "t1"},
    {"role": "assistant", "text": "Honestly, it worries me.", "timestamp": "t2"},
])

EVALUATION = json.dumps({
    "overall_score": 6.4,
    "skill_label": "Developing",
    "dimensions": [
        {"dimension": "framing_and_stakeholder_fit", "score": 7, "assessment": "Clear opener."},
        {"dimension": "probing_and_follow_up_depth", "score": 5, "assessment": "Stayed broad."},
    ],
    "moments": [{
        "headline": "You asked about the street", "dimension": "framing_and_stakeholder_fit",
        "student_quote": "How would the seawall affect your street?",
        "what_it_produced": "A worry", "produced_label": "persona_did", "outcome": "More to find",
        "outcome_kind": "go_further", "technique_name": "Echo", "technique_stem": "You said…",
    }],
    "insight_coverage": [{
        "tier": 1, "title": "Core facts", "description": "Basics",
        "items": [{"display_label": "Flood history", "elicited": True,
                   "earned_mode": "earned", "credit_mode": "explicit"}],
    }],
})


def _zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(response.content))


def _session_with_feedback(owned_session, user_id) -> uuid.UUID:
    sid = owned_session(user_id, transcript=TRANSCRIPT)
    sql(
        "INSERT INTO session_evaluations (session_id, evaluation) VALUES (%s, %s::jsonb)",
        (str(sid), EVALUATION),
    )
    return sid


def _other_user(client) -> tuple[str, str]:
    email = f"sis-test-{uuid.uuid4().hex[:12]}@wpi.edu"
    register(client, email)
    r = set_password(client, email)
    assert r.status_code == 200, r.text
    return email, r.json()["id"]


def test_a_student_downloads_their_own_interview(logged_in_client, owned_session, audit_log):  # noqa: F811
    client, user_id = logged_in_client
    sid = _session_with_feedback(owned_session, user_id)

    zf = _zip(client.get(f"/api/export/sessions/{sid}"))
    names = zf.namelist()
    assert "README.txt" in names
    folder = next(n.split("/")[0] for n in names if n.endswith("transcript.txt"))
    for part in ("transcript.txt", "transcript.json", "feedback.md", "feedback.json"):
        assert f"{folder}/{part}" in names

    transcript = zf.read(f"{folder}/transcript.txt").decode()
    assert "You: How would the seawall affect your street?" in transcript
    feedback = zf.read(f"{folder}/feedback.md").decode()
    assert "not a grade" in feedback
    assert "Framing & Fit — 7 / 10" in feedback
    assert "Flood history — Earned" in feedback
    readme = zf.read("README.txt").decode()
    assert "interview guides and written" in readme and "reflections" in readme

    code = scalar("SELECT pseudonymous_code FROM participants WHERE participant_id = %s",
                  (participant_of(user_id),))
    assert code in client.get(f"/api/export/sessions/{sid}").headers["content-disposition"]

    (event,) = [e for e in audit_log.named("export.self")][:1]
    assert event["participant_id"] == participant_of(user_id) and event["session_count"] == 1


def test_nothing_in_the_download_identifies_the_student(logged_in_client, owned_session):
    client, user_id = logged_in_client
    sid = _session_with_feedback(owned_session, user_id)
    email, first, last = (
        scalar(f"SELECT {c} FROM identity.users WHERE id = %s", (user_id,))
        for c in ("email", "first_name", "last_name")
    )
    zf = _zip(client.get(f"/api/export/sessions/{sid}"))
    blob = b"".join(zf.read(n) for n in zf.namelist()).decode()
    assert email not in blob and f"{first} {last}" not in blob and user_id not in blob


def test_another_participants_session_is_a_404(client, logged_in_client, owned_session, audit_log):  # noqa: F811
    _, owner = logged_in_client
    sid = _session_with_feedback(owned_session, owner)
    email_b, intruder = _other_user(client)
    sign_in_as(client, email_b)

    r = client.get(f"/api/export/sessions/{sid}")
    assert r.status_code == 404
    assert b"seawall" not in r.content
    (denial,) = audit_log.named("authz.session_access")
    assert denial["actor_user_id"] == intruder
    assert not [e for e in audit_log.named("export.self") if e["actor_user_id"] == intruder]


def test_an_unknown_session_is_a_404(logged_in_client):
    client, _ = logged_in_client
    assert client.get(f"/api/export/sessions/{uuid.uuid4()}").status_code == 404


def test_download_all_contains_only_the_callers_interviews(client, logged_in_client, owned_session):
    _, user_a = logged_in_client
    mine = [_session_with_feedback(owned_session, user_a) for _ in range(2)]
    email_b, user_b = _other_user(client)
    theirs = owned_session(user_b, transcript=json.dumps(
        [{"role": "user", "text": "someone else's words", "timestamp": "t"}]
    ))
    sign_in_as(client, email_b)
    zf_b = _zip(client.get("/api/export/me"))
    blob_b = b"".join(zf_b.read(n) for n in zf_b.namelist()).decode()
    assert "someone else's words" in blob_b
    assert "seawall" not in blob_b
    assert all(str(s)[:8] not in " ".join(zf_b.namelist()) for s in mine)
    assert str(theirs)[:8] in " ".join(zf_b.namelist())


def test_a_purged_session_exports_without_its_content(logged_in_client, owned_session):
    client, user_id = logged_in_client
    sid = _session_with_feedback(owned_session, user_id)
    sql("UPDATE interview_sessions SET purged_at = now() WHERE id = %s", (str(sid),))
    zf = _zip(client.get(f"/api/export/sessions/{sid}"))
    names = zf.namelist()
    transcript = zf.read(next(n for n in names if n.endswith("transcript.txt"))).decode()
    assert "removed after review" in transcript
    assert not any(n.endswith("feedback.md") for n in names)
    assert b"seawall" not in b"".join(zf.read(n) for n in names)


def test_an_unscored_session_has_no_feedback_files(logged_in_client, owned_session):
    client, user_id = logged_in_client
    sid = owned_session(user_id, transcript=TRANSCRIPT)
    names = _zip(client.get(f"/api/export/sessions/{sid}")).namelist()
    assert any(n.endswith("transcript.txt") for n in names)
    assert not any(n.endswith("feedback.md") or n.endswith("feedback.json") for n in names)


@pytest.mark.parametrize("path", ["/api/export/me", f"/api/export/sessions/{uuid.uuid4()}"])
def test_export_needs_a_session(client, path):
    client.cookies.clear()
    assert client.get(path).status_code == 401
