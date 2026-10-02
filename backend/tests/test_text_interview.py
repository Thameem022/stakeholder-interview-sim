"""SR-2026-052 items 2.1 / 2.2: the written interview, and "AI-generated, not graded".

The persona runs through the real Anthropic SDK (Bedrock Mantle client and the
refusal-fallback middleware) over an in-process mock transport, so the request
shapes, the tool loop and the fallback are exercised without AWS access.
Everything else is real: the endpoints, the notice gate, ownership, the
transcript, guardrail flags, audit, and — for equivalence with voice — the
scoring endpoint itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx2
import pytest
from anthropic import AsyncAnthropicBedrockMantle, BetaRefusalFallbackMiddleware, DefaultAsyncHttpxClient

from app.ai import guardrails
from app.ai.guardrails import GuardrailVerdict
from app.config import get_settings
from app.realtime import bedrock_proxy, text_interview
from app.realtime.notice import NOTICE_VERSION
from app.realtime.session import InterviewSession, Turn
from app.realtime.text_interview import MAX_TURN_CHARS, WRITTEN_MODE_NOTE, history_messages
from tests.conftest import enroll, sign_in_as
from tests.db import scalar
from tests.test_audit import audit_log  # noqa: F401  (fixture)

OPUS, SONNET = "anthropic.claude-opus-5-5", "anthropic.claude-sonnet-5-5"
REPO = Path(__file__).resolve().parents[2]


def _text(model: str, text: str, stop: str = "end_turn") -> dict:
    body = {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20,
                  "cache_read_input_tokens": 80, "cache_creation_input_tokens": 0},
    }
    if stop == "refusal":
        body["content"] = [{"type": "text", "text": text}] if text else []
        body["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": None}
    return body


def _tool(model: str, query: str, tool_id: str = "toolu_1") -> dict:
    return {
        "id": "msg_tool", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "tool_use", "id": tool_id, "name": "retrieve_context",
                     "input": {"query": query}}],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


@pytest.fixture
def persona(monkeypatch):
    """Script the persona: `replies` is a list of response bodies, or a
    callable (body) -> response body. Returns the recorded request bodies."""
    script: dict = {"replies": []}
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        replies = script["replies"]
        reply = replies(body) if callable(replies) else replies.pop(0)
        return httpx2.Response(200, json=reply)

    sdk = AsyncAnthropicBedrockMantle(
        aws_region="us-east-1", skip_auth=True, max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        middleware=[BetaRefusalFallbackMiddleware([{"model": SONNET}])],
    )
    monkeypatch.setattr(text_interview, "persona_client", lambda: sdk)
    retrievals: list[tuple] = []

    async def _retrieve(user, sid, persona_id, query):
        retrievals.append((str(sid), persona_id, query))
        return "Relevant context: the harbor floods every spring."

    monkeypatch.setattr(text_interview, "run_retrieval", _retrieve)
    script["requests"], script["retrievals"] = requests, retrievals
    return script


@pytest.fixture(autouse=True)
def _no_guardrail(monkeypatch):
    monkeypatch.setattr(get_settings(), "bedrock_guardrail_id", "")


def _start(client, persona_id: str = "alex_martinez") -> str:
    r = client.post("/api/realtime/text/start",
                    json={"persona_id": persona_id, "notice_version": NOTICE_VERSION})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _transcript(sid: str) -> list[tuple[str, str]]:
    raw = json.loads(scalar("SELECT transcript::text FROM interview_sessions WHERE id = %s", (sid,)))
    return [(t["role"], t["text"]) for t in raw]


# --- the happy path ----------------------------------------------------------------


def test_a_written_interview_runs_through_claude_on_bedrock(logged_in_client, persona, audit_log):  # noqa: F811
    persona["replies"] = [
        _tool(OPUS, "spring flooding"),
        _text(OPUS, "It floods every spring, and the pumps are old."),
        _text(OPUS, "The budget is the main worry."),
    ]
    client, _ = logged_in_client
    sid = _start(client)
    assert scalar("SELECT mode FROM interview_sessions WHERE id = %s", (sid,)) == "text"

    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "What about flooding?"})
    assert r.status_code == 200, r.text
    assert r.json() == {"reply": "It floods every spring, and the pumps are old.",
                        "ended": False, "reason": None}

    first, after_tool = persona["requests"][:2]
    # Claude on Bedrock, with the persona prompt (cached) and the written-mode note.
    assert first["model"] == OPUS
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Alex Martinez" in first["system"][0]["text"]
    assert first["system"][0]["text"].endswith(WRITTEN_MODE_NOTE)
    # The same grounding tool as voice, not forced; effort set, no temperature.
    assert [t["name"] for t in first["tools"]] == ["retrieve_context"]
    assert first["tool_choice"] == {"type": "auto"}
    assert first["output_config"] == {"effort": "low"}
    assert "temperature" not in first
    assert first["messages"] == [{"role": "user", "content": "What about flooding?"}]
    # The tool call was served from retrieval, scoped to this session, and
    # the whole assistant turn went back with the matching result.
    assert persona["retrievals"] == [(sid, "alex_martinez", "spring flooding")]
    assert after_tool["messages"][1]["content"][0]["type"] == "tool_use"
    result = after_tool["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert "floods every spring" in result["content"]

    # The next turn carries the conversation so far, from the server's record.
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "And the budget?"})
    assert r.json()["reply"] == "The budget is the main worry."
    assert [m["role"] for m in persona["requests"][2]["messages"]] == ["user", "assistant", "user"]

    assert client.post(f"/api/realtime/text/{sid}/end").status_code == 200
    assert _transcript(sid) == [
        ("user", "What about flooding?"),
        ("assistant", "It floods every spring, and the pumps are old."),
        ("user", "And the budget?"),
        ("assistant", "The budget is the main worry."),
    ]
    assert scalar("SELECT ended_at IS NOT NULL FROM interview_sessions WHERE id = %s", (sid,))

    events = audit_log.named("ai.text_turn")
    assert [e["stage"] for e in events] == ["start", "turn", "turn", "end"]
    assert events[1]["tool_calls"] == 1 and events[1]["model"] == OPUS
    assert events[-1]["turn_count"] == 4
    assert "flooding" not in "\n".join(audit_log.lines)


def test_an_ended_interview_takes_no_more_turns(logged_in_client, persona):
    client, _ = logged_in_client
    sid = _start(client)
    client.post(f"/api/realtime/text/{sid}/end")
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "Hello?"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "interview_ended"
    assert persona["requests"] == [] and _transcript(sid) == []


def test_the_time_limit_ends_the_interview(logged_in_client, persona, monkeypatch):
    monkeypatch.setattr(get_settings(), "realtime_max_session_minutes", 0)
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "Hello?"})
    assert r.json() == {"reply": None, "ended": True, "reason": "time_limit"}
    assert persona["requests"] == []
    assert scalar("SELECT ended_at IS NOT NULL FROM interview_sessions WHERE id = %s", (sid,))


# --- who may start and talk -------------------------------------------------------------


def test_the_notice_must_be_acknowledged_first(logged_in_client):
    client, user_id = logged_in_client
    before = scalar("SELECT count(*) FROM interview_sessions")
    for version in (None, "an-old-version"):
        r = client.post("/api/realtime/text/start",
                        json={"persona_id": "alex_martinez", "notice_version": version})
        assert r.status_code == 428 and r.json()["detail"]["code"] == "notice_not_acknowledged"
    assert scalar("SELECT count(*) FROM interview_sessions") == before


@pytest.mark.parametrize("persona_id,status", [("nobody_here", 400), ("Alex Martinez!", 422)])
def test_only_a_real_persona_can_be_interviewed(logged_in_client, persona_id, status):
    client, _ = logged_in_client
    r = client.post("/api/realtime/text/start",
                    json={"persona_id": persona_id, "notice_version": NOTICE_VERSION})
    assert r.status_code == status


def test_someone_elses_interview_is_not_found(client, persona):
    enroll(client, email="sis-test-text-owner@example.edu")
    sid = _start(client)
    enroll(client, email="sis-test-text-other@example.edu")
    for path, body in ((f"/api/realtime/text/{sid}/turns", {"text": "hi"}),
                       (f"/api/realtime/text/{sid}/end", {})):
        assert client.post(path, json=body).status_code == 404
    assert persona["requests"] == [] and _transcript(sid) == []
    assert scalar("SELECT ended_at FROM interview_sessions WHERE id = %s", (sid,)) is None
    # And it still works for its owner.
    sign_in_as(client, "sis-test-text-owner@example.edu")
    persona["replies"] = [_text(OPUS, "Hello there.")]
    assert client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hi"}).status_code == 200


def test_a_voice_interview_takes_no_typed_turns(logged_in_client, persona):
    client, _ = logged_in_client
    voice = client.post("/api/realtime/token",
                        json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION}).json()
    r = client.post(f"/api/realtime/text/{voice['session_id']}/turns", json={"text": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_a_text_interview"
    assert scalar("SELECT mode FROM interview_sessions WHERE id = %s", (voice["session_id"],)) == "voice"


def test_a_message_has_a_length_limit(logged_in_client, persona):
    client, _ = logged_in_client
    sid = _start(client)
    assert client.post(f"/api/realtime/text/{sid}/turns",
                       json={"text": "x" * (MAX_TURN_CHARS + 1)}).status_code == 422
    assert client.post(f"/api/realtime/text/{sid}/turns", json={"text": "   "}).status_code == 400
    assert persona["requests"] == []


# --- the persona call -----------------------------------------------------------------------


def test_the_tool_loop_is_bounded(logged_in_client, persona):
    persona["replies"] = lambda body: (
        _text(OPUS, "Let me answer from memory.") if body["tool_choice"]["type"] == "none"
        else _tool(OPUS, f"lookup {len(body['messages'])}", tool_id=f"toolu_{len(body['messages'])}")
    )
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "Tell me everything."})
    assert r.json()["reply"] == "Let me answer from memory."
    choices = [b["tool_choice"]["type"] for b in persona["requests"]]
    assert choices == ["auto", "auto", "auto", "none"]
    assert len(persona["retrievals"]) == 3


def test_a_tool_query_is_validated_like_user_input(logged_in_client, persona):
    persona["replies"] = [_tool(OPUS, "q" * 501), _text(OPUS, "Hard to say.")]
    client, _ = logged_in_client
    sid = _start(client)
    client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hi"})
    assert persona["retrievals"] == []
    assert persona["requests"][1]["messages"][2]["content"][0]["content"] == "(invalid query)"


def test_a_declined_reply_falls_back_to_the_second_model(logged_in_client, persona, audit_log):  # noqa: F811
    persona["replies"] = [_text(OPUS, "", stop="refusal"), _text(SONNET, "Happy to talk about that.")]
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hi"})
    assert r.json()["reply"] == "Happy to talk about that."
    assert [b["model"] for b in persona["requests"]] == [OPUS, SONNET]
    assert audit_log.named("ai.text_turn")[-1]["model_used"] == SONNET


def test_no_reply_at_all_keeps_the_students_words(logged_in_client, persona):
    persona["replies"] = lambda body: _text(body["model"], "", stop="refusal")
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hi"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "persona_unavailable"
    assert _transcript(sid) == [("user", "hi")]
    # The next attempt sends one well-formed user turn, not two in a row.
    persona["replies"] = [_text(OPUS, "Sorry — go on.")]
    client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hello?"})
    assert persona["requests"][-1]["messages"] == [{"role": "user", "content": "hi\n\nhello?"}]


def test_a_reply_cut_off_by_a_refusal_is_never_shown(logged_in_client, persona):
    # A refusal can stop mid-sentence; whatever was produced before it is not a reply.
    persona["replies"] = lambda body: _text(body["model"], "Well, the way to do that is", stop="refusal")
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hi"})
    assert r.status_code == 503 and "the way to do that" not in r.text
    assert _transcript(sid) == [("user", "hi")]


def test_history_alternates_and_starts_with_the_student():
    session = InterviewSession(id=None, participant_id=None, notice_version=None,  # type: ignore[arg-type]
                               persona_id="alex_martinez", voice_id="matthew", started_at=None)  # type: ignore[arg-type]
    session.turns = [Turn("assistant", "Welcome.", "t"), Turn("user", "a", "t"),
                     Turn("user", "b", "t"), Turn("assistant", "c", "t")]
    assert history_messages(session) == [
        {"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"},
    ]


# --- guardrails -----------------------------------------------------------------------------


def _guard_on(monkeypatch, source: str):
    async def _check(text, src):
        return GuardrailVerdict(intervened=src == source, configured=True,
                                policies=("contentPolicy:VIOLENCE",) if src == source else ())
    monkeypatch.setattr(guardrails, "check", _check)


def test_a_harmful_reply_is_withheld_and_the_interview_stopped(logged_in_client, persona, monkeypatch):
    _guard_on(monkeypatch, "OUTPUT")
    persona["replies"] = [_text(OPUS, "SOMETHING HARMFUL")]
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "hi"})
    assert r.json() == {"reply": None, "ended": True, "reason": "guardrail"}
    assert "SOMETHING HARMFUL" not in r.text
    assert _transcript(sid) == [("user", "hi")]
    assert scalar("SELECT ended_at IS NOT NULL FROM interview_sessions WHERE id = %s", (sid,))
    assert scalar("SELECT source || ':' || reason FROM session_flags WHERE session_id = %s",
                  (sid,)) == "guardrail:harmful_ai_output"


def test_a_sensitive_student_turn_is_flagged_and_the_interview_continues(
    logged_in_client, persona, monkeypatch
):
    _guard_on(monkeypatch, "INPUT")
    persona["replies"] = [_text(OPUS, "I hear you.")]
    client, _ = logged_in_client
    sid = _start(client)
    r = client.post(f"/api/realtime/text/{sid}/turns", json={"text": "my real address is ..."})
    assert r.json()["reply"] == "I hear you." and not r.json()["ended"]
    assert scalar("SELECT reason FROM session_flags WHERE session_id = %s", (sid,)) == "sensitive_disclosure"


# --- the same transcript scores the same, typed or spoken ----------------------------------------

STUDENT = ["Hi, I'm studying the harbor plan. What worries you most?",
           "Why does the flooding matter to you personally?"]
PERSONA = ["Honestly, the flooding. It comes every spring.",
           "My shop is on Front Street; we lose a week every year."]


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _hashed_iqr(call: dict) -> str:
    """A judge whose scores depend on exactly what it was given (apart from
    the session id, which the prompt names and which differs by construction)."""
    dims = ["framing_and_stakeholder_fit", "question_quality_and_precision",
            "probing_and_follow_up_depth", "listening_interpretation_and_stewardship"]
    digest = hashlib.sha256(_UUID.sub("SID", call["system"] + call["user"]).encode()).digest()
    return json.dumps({
        "dimensions": [{"dimension": d, "score": 1 + digest[i] % 10, "assessment": f"a{digest[i]}"}
                       for i, d in enumerate(dims)],
        "overall_score": 1 + digest[9] % 10, "skill_label": "Developing", "moments": [],
    })


def test_a_written_and_a_spoken_interview_with_the_same_words_score_identically(
    client, persona, monkeypatch
):
    import app.evaluation.iqr_scorer as iqr
    import app.evaluation.sic_enrichment as enrichment
    import app.evaluation.sic_scorer as sic
    from app.evaluation.sic_scorer import SICGradingResult
    from tests.fake_llm import FakeLLM
    from tests.test_realtime_stream import FakeStream, _connect, _receive_until, persona_says, user_says

    iqr_llm, sic_llm = FakeLLM(_hashed_iqr), FakeLLM(SICGradingResult(grades=[]))
    real_iqr, real_sic = iqr.IQRScorer, sic.SICScorer
    monkeypatch.setattr(iqr, "IQRScorer", lambda: real_iqr(allow_fallback=False, llm=iqr_llm))
    monkeypatch.setattr(sic, "SICScorer", lambda: real_sic(allow_fallback=False, llm=sic_llm))

    async def _no_enrichment(*a, **k):
        return None

    monkeypatch.setattr(enrichment, "enrich_sic_results", _no_enrichment)
    enroll(client, email="sis-test-text-equiv@example.edu")

    # Typed.
    persona["replies"] = [_text(OPUS, PERSONA[0]), _text(OPUS, PERSONA[1])]
    text_sid = _start(client)
    for line in STUDENT:
        client.post(f"/api/realtime/text/{text_sid}/turns", json={"text": line})
    client.post(f"/api/realtime/text/{text_sid}/end")

    # Spoken, through the live voice bridge.
    script = [*user_says(STUDENT[0], "u1"), *persona_says(PERSONA[0], n="1"),
              *user_says(STUDENT[1], "u2"), *persona_says(PERSONA[1], n="2")]
    stream = FakeStream(script)

    async def _open():
        return stream

    monkeypatch.setattr(bedrock_proxy, "open_speech_stream", _open)
    voice = client.post("/api/realtime/token",
                        json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION}).json()
    voice_sid = voice["session_id"]
    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": voice["stream_token"]})
        _receive_until(ws, "ready")
        _receive_until(ws, "assistant_turn_end")
        _receive_until(ws, "assistant_turn_end")
        ws.send_json({"type": "end"})
        _receive_until(ws, "ended")

    assert _transcript(text_sid) == _transcript(voice_sid)
    modes = {scalar("SELECT mode FROM interview_sessions WHERE id = %s", (s,)) for s in (text_sid, voice_sid)}
    assert modes == {"text", "voice"}

    reports = []
    for sid in (text_sid, voice_sid):
        r = client.post(f"/api/eval/iqr?session_id={sid}")
        assert r.status_code == 200, r.text
        reports.append(r.json())

    def _neutral(s: str) -> str:
        return s.replace(text_sid, "SID").replace(voice_sid, "SID")

    # The judges were given exactly the same thing...
    for llm in (iqr_llm, sic_llm):
        typed, spoken = llm.calls
        assert _neutral(typed["system"]) == _neutral(spoken["system"])
        assert _neutral(typed["user"]) == _neutral(spoken["user"])
    # ...and so the whole report is identical, scores and all.
    typed_report, spoken_report = (json.loads(_neutral(json.dumps(r))) for r in reports)
    assert typed_report == spoken_report
    assert reports[0]["overall_score"] == reports[1]["overall_score"]


# --- item 2.2: AI-generated, not graded -------------------------------------------------------


def test_the_notice_says_the_persona_and_feedback_are_ai_and_not_graded(logged_in_client):
    client, _ = logged_in_client
    points = " ".join(client.get("/api/realtime/notice").json()["points"])
    assert "generated by AI" in points and "do not affect your grade" in points
    assert "typed in a written interview" in points


def test_the_persona_and_report_pages_carry_the_statement():
    src = REPO / "frontend" / "src"
    notice = (src / "components" / "AiNotice.tsx").read_text()
    assert "generated by AI" in notice and "do not affect your grade" in notice
    # Persona chooser + interview (App.tsx) and the feedback report.
    assert (src / "App.tsx").read_text().count("<AiNotice") >= 2
    assert "<AiNotice" in (src / "ScoreReport.tsx").read_text()


_GRADEBOOK = re.compile(
    r"gradebook|grade_?passback|\blti\b|lis_outcome|outcome_service|line_?items?\b"
    r"|score_?maximum|instructure|blackboard|moodle|brightspace|\bd2l\b|/api/v1/courses",
    re.I,
)


def test_nothing_writes_to_a_gradebook():
    roots = [REPO / "backend" / "app", REPO / "backend" / "scripts", REPO / "frontend" / "src",
             REPO / "deploy"]
    hits = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".conf", ".service", ".sh", ".env", ".toml"}:
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if _GRADEBOOK.search(line) and "writes to a gradebook" not in line:
                    hits.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:100]}")
    assert hits == [], "possible gradebook/LMS writeback:\n" + "\n".join(hits)
