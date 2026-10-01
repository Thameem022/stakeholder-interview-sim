"""SR-2026-052 item 1.7: security audit events.

Each sensitive action must emit a structured event naming the actor and the
pseudonymous participant id — and no event may carry interview text, an
address, or a credential.
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest

from app.config import get_settings, settings
from app.observability import audit as audit_mod
from app.realtime.notice import NOTICE_VERSION
from tests.conftest import enroll, sign_in_as
from tests.db import participant_of, scalar

SECRET = "my-private-disclosure-91ab"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    @property
    def events(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


@pytest.fixture
def audit_log():
    logger = logging.getLogger(audit_mod.AUDIT_LOGGER_NAME)
    cap = _Capture()
    logger.addHandler(cap)
    logger.setLevel(logging.INFO)
    yield cap
    logger.removeHandler(cap)


def _no_pii(cap: _Capture, *needles: str) -> None:
    raw = "\n".join(cap.lines)
    for needle in needles:
        assert needle not in raw, f"{needle!r} reached the audit log"


def _new_user(client) -> tuple[str, str]:
    return enroll(client)


# --- the event shape ---------------------------------------------------------


def test_every_event_is_one_json_line_with_actor_and_pseudonym(audit_log):
    actor, pseudonym = uuid.uuid4(), uuid.uuid4()
    audit_mod.audit(
        "test.event", "success", actor_user_id=actor, participant_id=pseudonym, detail="x"
    )
    (event,) = audit_log.events
    assert event["type"] == "audit" and event["schema"] == audit_mod.AUDIT_SCHEMA_VERSION
    assert event["event"] == "test.event" and event["outcome"] == "success"
    assert event["actor_user_id"] == str(actor)
    assert event["participant_id"] == str(pseudonym)
    assert "\n" not in audit_log.lines[0]


@pytest.mark.parametrize("field", ["text", "transcript", "query", "quote", "email", "password"])
def test_fields_that_could_carry_interview_text_are_refused(field):
    with pytest.raises(ValueError, match="could carry"):
        audit_mod.audit("test.event", "success", **{field: SECRET})


def test_long_values_are_capped(audit_log):
    audit_mod.audit("test.event", "success", detail="x" * 5000)
    assert len(audit_log.events[0]["detail"]) <= 201


# --- authentication ----------------------------------------------------------
# Sign-in success and failure events are produced by the Entra flow and are
# tested end to end in tests/test_sso.py.


def test_logout_is_audited_with_actor_and_pseudonym(client, audit_log):
    email, user_id = _new_user(client)
    client.post("/api/auth/logout")
    (logout,) = audit_log.named("auth.logout")
    assert logout["actor_user_id"] == user_id
    assert logout["participant_id"] == participant_of(user_id) != user_id
    _no_pii(audit_log, email)


def test_a_forged_session_cookie_is_audited(client, audit_log):
    client.cookies.set(settings.auth_cookie_name, "forged-token")
    client.get("/api/personas")
    (event,) = audit_log.named("auth.session_rejected")
    assert event["outcome"] == "denied" and event["path"] == "/api/personas"
    _no_pii(audit_log, "forged-token")


def test_a_csrf_rejection_is_audited(logged_in_client, audit_log):
    client, _ = logged_in_client
    client.csrf_auto = False
    client.post("/api/auth/logout", headers={"Origin": "https://attacker.example"})
    (event,) = audit_log.named("auth.csrf_rejected")
    assert event["reason"] == "missing" and event["origin"] == "https://attacker.example"


def test_rate_limiting_is_audited_with_the_user_but_not_the_bucket_value(
    logged_in_client, audit_log
):
    client, user_id = logged_in_client
    for _ in range(22):
        client.post(f"/api/eval/iqr?session_id={uuid.uuid4()}")
    event = audit_log.named("auth.rate_limited")[0]
    assert event["action"] == "eval-iqr" and event["scope"] == "user"
    assert event["actor_user_id"] == user_id


# --- authorization -----------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda c, sid: c.get(f"/api/eval/sessions/{sid}/latest"),
        lambda c, sid: c.post(f"/api/eval/iqr?session_id={sid}"),
        lambda c, sid: c.post(
            "/api/realtime/transcript",
            json={"session_id": str(sid), "role": "user", "text": SECRET},
        ),
        lambda c, sid: c.post(
            "/api/realtime/retrieve",
            json={"session_id": str(sid), "persona_id": "alex_martinez", "query": SECRET},
        ),
    ],
    ids=["latest-eval", "score", "transcript", "retrieve"],
)
def test_touching_another_participants_session_is_audited(
    client, owned_session, audit_log, call
):
    _, owner = _new_user(client)
    sid = owned_session(owner)
    email_b, intruder = _new_user(client)
    sign_in_as(client, email_b)

    assert call(client, sid).status_code == 404
    (event,) = audit_log.named("authz.session_access")
    assert event["outcome"] == "denied"
    assert event["actor_user_id"] == intruder
    assert event["owner_participant_id"] == participant_of(owner)
    assert event["participant_id"] == participant_of(intruder)
    assert event["session_id"] == str(sid)
    _no_pii(audit_log, SECRET)


# --- AI request/response metadata --------------------------------------------


def test_realtime_session_mint_is_audited(logged_in_client, monkeypatch, audit_log):
    async def _ok(*args, **kwargs):
        return httpx.Response(200, json={"value": "ek_test_not_real"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _ok)
    client, user_id = logged_in_client
    r = client.post("/api/realtime/token", json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION})
    assert r.status_code == 200, r.text

    (event,) = audit_log.named("ai.realtime_session")
    assert event["outcome"] == "success" and event["actor_user_id"] == user_id
    assert event["session_id"] == r.json()["session_id"]
    assert event["model"] == settings.openai_realtime_model
    assert isinstance(event["latency_ms"], int)
    _no_pii(audit_log, "ek_test_not_real")


def test_a_failed_realtime_mint_is_audited(logged_in_client, monkeypatch, audit_log):
    async def _down(*args, **kwargs):
        raise httpx.ConnectError("blocked in tests")

    monkeypatch.setattr(httpx.AsyncClient, "post", _down)
    client, _ = logged_in_client
    client.post("/api/realtime/token", json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION})
    (event,) = audit_log.named("ai.realtime_session")
    assert event["outcome"] == "failure" and event["error_type"] == "ConnectError"


def test_retrieval_is_audited_without_the_query(
    logged_in_client, owned_session, monkeypatch, audit_log
):
    import app.realtime.retrieve as retrieve

    async def _embed(query):
        return [0.0]

    async def _persona(persona_id, query, k, query_vec):
        return [{"text": "chunk", "chunk_id": "p1", "score": 0.9}]

    async def _world(query, k, query_vec):
        return []

    monkeypatch.setattr(retrieve, "embed_one", _embed)
    monkeypatch.setattr(retrieve, "search_persona", _persona)
    monkeypatch.setattr(retrieve, "search_world", _world)

    client, user_id = logged_in_client
    sid = owned_session(user_id)
    r = client.post(
        "/api/realtime/retrieve",
        json={"session_id": str(sid), "persona_id": "alex_martinez", "query": SECRET},
    )
    assert r.status_code == 200

    (event,) = audit_log.named("ai.retrieve")
    assert event["outcome"] == "success" and event["actor_user_id"] == user_id
    assert event["persona_hits"] == 1 and event["world_hits"] == 0
    assert event["embedding_model"] == settings.embedding_model
    _no_pii(audit_log, SECRET)


def test_scoring_is_audited_with_models_and_versions_but_no_transcript(
    logged_in_client, owned_session, monkeypatch, audit_log, caplog
):
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

        async def evaluate(self, *args, **kwargs):
            return SessionEvaluation(
                dimensions=[{"dimension": d, "score": 5, "assessment": "ok"} for d in dims],
                overall_score=5, skill_label="Developing",
            )

    class _SIC:
        prompt_version = "v-test"
        last_model_used = "grader-test"

        async def evaluate(self, *args, **kwargs):
            return []

    monkeypatch.setattr(iqr, "IQRScorer", _IQR)
    monkeypatch.setattr(sic, "SICScorer", _SIC)

    client, user_id = logged_in_client
    sid = owned_session(user_id, transcript=json.dumps([
        {"role": "user", "text": f"Something personal: {SECRET}", "timestamp": "t"},
        {"role": "assistant", "text": "Thanks for telling me.", "timestamp": "t"},
    ]))
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/api/eval/iqr?session_id={sid}")
    assert r.status_code == 200, r.text

    (event,) = audit_log.named("ai.scoring")
    assert event["outcome"] == "success" and event["actor_user_id"] == user_id
    assert event["session_id"] == str(sid) and event["turn_count"] == 2
    assert event["iqr_model_used"] == "judge-test"
    assert event["sic_model_used"] == "grader-test"
    assert event["injection_guard_version"]
    _no_pii(audit_log, SECRET)
    assert SECRET not in caplog.text


def test_transcript_appends_never_put_text_in_any_log(
    logged_in_client, owned_session, audit_log, caplog
):
    client, user_id = logged_in_client
    sid = owned_session(user_id)
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/realtime/transcript",
            json={"session_id": str(sid), "role": "user", "text": SECRET},
        )
    assert SECRET in scalar(
        "SELECT transcript::text FROM interview_sessions WHERE id = %s", (str(sid),)
    )
    _no_pii(audit_log, SECRET)
    assert SECRET not in caplog.text


# --- sink configuration ------------------------------------------------------


@pytest.fixture
def sink(monkeypatch):
    s = get_settings()

    def _set(**values):
        for k, v in values.items():
            monkeypatch.setattr(s, k, v)
        audit_mod.configure_audit_logging()
        return audit_mod._sink

    yield _set
    monkeypatch.undo()
    audit_mod.configure_audit_logging()


def test_sink_none_disables_output(sink):
    assert sink(audit_log_sink="none") is None


def test_syslog_sink_accepts_host_and_port(sink):
    handler = sink(audit_log_sink="syslog", audit_syslog_address="127.0.0.1:5514")
    assert isinstance(handler, logging.handlers.SysLogHandler)
    assert handler.address == ("127.0.0.1", 5514)


def test_an_unknown_sink_refuses_to_start(sink):
    with pytest.raises(RuntimeError, match="AUDIT_LOG_SINK"):
        sink(audit_log_sink="carrier-pigeon")


def test_reconfiguring_does_not_duplicate_handlers(sink):
    sink(audit_log_sink="stdout")
    sink(audit_log_sink="stdout")
    logger = logging.getLogger(audit_mod.AUDIT_LOGGER_NAME)
    assert sum(isinstance(h, logging.StreamHandler) and not isinstance(h, _Capture)
               for h in logger.handlers) == 1
