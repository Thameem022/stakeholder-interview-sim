"""SR-2026-052 item 1.1: the live interview runs browser <-> backend <-> Bedrock.

Nova Sonic is replaced by a scripted fake stream; everything else is real —
the WebSocket endpoint, session-cookie and Origin checks, the single-use
stream token, transcript persistence, tool calls, guardrails and audit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from app.ai.guardrails import GuardrailVerdict
from app.config import get_settings, settings
from app.realtime import bedrock_proxy
from app.realtime.notice import NOTICE_VERSION
from tests.conftest import enroll, sign_in_as
from tests.db import scalar
from tests.test_audit import audit_log  # noqa: F401  (fixture)

ORIGIN = "http://testserver"


class FakeStream:
    """A scripted Nova Sonic. Script items are events to emit, or predicates
    over the stream that must hold (e.g. "the browser's audio has arrived")
    before the script continues."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, event: dict) -> None:
        self.sent.append(event)

    async def events(self):
        for item in self.script:
            if callable(item):
                deadline = time.monotonic() + 5
                while not item(self):
                    if time.monotonic() > deadline:
                        raise TimeoutError("fake stream waited too long")
                    await asyncio.sleep(0.01)
            else:
                yield item
        while not self.closed:
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        self.closed = True

    def kinds(self) -> list[str]:
        return [next(iter(e)) for e in self.sent]


def user_says(text: str, cid: str = "u1") -> list[dict]:
    return [
        {"contentStart": {"contentId": cid, "role": "USER", "type": "TEXT"}},
        {"textOutput": {"contentId": cid, "role": "USER", "content": text}},
        {"contentEnd": {"contentId": cid}},
    ]


def persona_says(text: str, audio: bytes = b"\x01\x02" * 8, n: str = "1") -> list[dict]:
    final = json.dumps({"generationStage": "FINAL"})
    return [
        {"contentStart": {"contentId": f"t{n}", "role": "ASSISTANT", "type": "TEXT",
                          "additionalModelFields": final}},
        {"textOutput": {"contentId": f"t{n}", "role": "ASSISTANT", "content": text}},
        {"contentEnd": {"contentId": f"t{n}"}},
        {"contentStart": {"contentId": f"a{n}", "role": "ASSISTANT", "type": "AUDIO"}},
        {"audioOutput": {"contentId": f"a{n}", "content": base64.b64encode(audio).decode()}},
        {"contentEnd": {"contentId": f"a{n}", "stopReason": "END_TURN"}},
    ]


def audio_arrived(stream: FakeStream) -> bool:
    return "audioInput" in stream.kinds()


@pytest.fixture
def streams(monkeypatch):
    """Queue FakeStreams; each opened stream takes the next one."""
    queue: list[FakeStream] = []
    opened: list[FakeStream] = []

    async def _open():
        stream = queue.pop(0)
        opened.append(stream)
        return stream

    monkeypatch.setattr(bedrock_proxy, "open_speech_stream", _open)
    return queue, opened


@pytest.fixture(autouse=True)
def _no_guardrail(monkeypatch):
    monkeypatch.setattr(get_settings(), "bedrock_guardrail_id", "")


def _cookie(client) -> str:
    return next(c.value for c in client.cookies.jar if c.name == settings.auth_cookie_name)


def _start_interview(client) -> dict:
    r = client.post("/api/realtime/token",
                    json={"persona_id": "alex_martinez", "notice_version": NOTICE_VERSION})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ephemeral_key" not in body and len(body["stream_token"]) >= 32
    return body


def _connect(client, *, origin: str = ORIGIN, cookie: str | None = None):
    headers = {"origin": origin}
    cookie = _cookie(client) if cookie is None else cookie
    if cookie:
        headers["cookie"] = f"{settings.auth_cookie_name}={cookie}"
    return client.websocket_connect("/api/realtime/stream", headers=headers)


def _receive_until(ws, kind: str, limit: int = 50) -> tuple[list[dict], list[bytes]]:
    messages, audio = [], []
    for _ in range(limit):
        message = ws.receive()
        if message.get("bytes") is not None:
            audio.append(message["bytes"])
            continue
        if message.get("text") is None:
            break
        data = json.loads(message["text"])
        messages.append(data)
        if data.get("type") == kind:
            break
    return messages, audio


# --- the happy path ----------------------------------------------------------------------


def test_a_live_interview_runs_through_the_backend(logged_in_client, streams, monkeypatch, audit_log):  # noqa: F811
    queue, opened = streams
    calls = []

    async def _retrieve(user, sid, persona_id, query):
        calls.append((str(sid), persona_id, query))
        return "Relevant context: floods every spring."

    monkeypatch.setattr(bedrock_proxy, "run_retrieval", _retrieve)
    queue.append(FakeStream([
        audio_arrived,
        *user_says("What about flooding?"),
        {"toolUse": {"toolName": "retrieve_context", "toolUseId": "tool-1",
                     "content": json.dumps({"query": "flood history"})}},
        lambda s: "toolResult" in s.kinds(),
        *persona_says("It floods every spring.", audio=b"\x10\x20" * 40),
    ]))

    client, user_id = logged_in_client
    started = _start_interview(client)
    mic = b"\x00\x01" * 160
    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": started["stream_token"]})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(mic)
        messages, audio = _receive_until(ws, "assistant_turn_end")
        ws.send_json({"type": "end"})
        tail, _ = _receive_until(ws, "ended")

    stream = opened[0]
    # Setup: session, prompt with the persona's voice and the retrieval tool,
    # the persona system prompt, then the live audio channel.
    assert stream.kinds()[:2] == ["sessionStart", "promptStart"]
    prompt = stream.sent[1]["promptStart"]
    assert prompt["audioOutputConfiguration"]["voiceId"] == "tiffany"
    assert prompt["toolConfiguration"]["tools"][0]["toolSpec"]["name"] == "retrieve_context"
    roles = [e["contentStart"]["role"] for e in stream.sent if "contentStart" in e]
    assert roles[0] == "SYSTEM" and "USER" in roles
    # The browser's microphone frame reached Nova Sonic intact.
    forwarded = next(e["audioInput"]["content"] for e in stream.sent if "audioInput" in e)
    assert base64.b64decode(forwarded) == mic
    # The tool call was served from retrieval, scoped to this session.
    assert calls == [(started["session_id"], "alex_martinez", "flood history")]
    result = next(e["toolResult"]["content"] for e in stream.sent if "toolResult" in e)
    assert "floods every spring" in json.loads(result)["context"]
    # The browser got the persona's audio and both final transcript lines.
    assert b"".join(audio) == b"\x10\x20" * 40
    finals = [(m["role"], m["text"]) for m in messages if m.get("type") == "transcript" and m["final"]]
    assert finals == [("user", "What about flooding?"), ("assistant", "It floods every spring.")]
    assert tail[-1] == {"type": "ended", "reason": "client_ended"}
    # The stream was ended properly.
    assert stream.kinds()[-3:] == ["contentEnd", "promptEnd", "sessionEnd"] and stream.closed

    # The server, not the browser, wrote the transcript, and closed the session.
    transcript = json.loads(scalar(
        "SELECT transcript::text FROM interview_sessions WHERE id = %s", (started["session_id"],)
    ))
    assert [(t["role"], t["text"]) for t in transcript] == finals
    assert scalar("SELECT ended_at IS NOT NULL FROM interview_sessions WHERE id = %s",
                  (started["session_id"],))
    stages = [e.get("stage") for e in audit_log.named("ai.realtime_session")]
    assert stages == ["token_issued", "stream_open", "stream_end"]
    end = audit_log.named("ai.realtime_session")[-1]
    assert end["turn_count"] == 2 and end["reason"] == "client_ended"
    assert "flooding" not in "\n".join(audit_log.lines)


# --- who may open a stream ------------------------------------------------------------------


def test_a_foreign_origin_is_refused(logged_in_client, streams):
    client, _ = logged_in_client
    with pytest.raises(WebSocketDisconnect) as refused:
        with _connect(client, origin="https://attacker.example"):
            pass
    assert refused.value.code == 4403


def test_no_session_cookie_is_refused(client, streams):
    with pytest.raises(WebSocketDisconnect) as refused:
        with _connect(client, cookie=""):
            pass
    assert refused.value.code == 4401


@pytest.mark.parametrize("case", ["garbage", "reused", "expired", "someone_elses"])
def test_only_a_fresh_token_for_your_own_session_opens_a_stream(client, streams, case):
    queue, _ = streams
    queue.extend(FakeStream([]) for _ in range(2))
    owner_email, _ = enroll(client)
    token = _start_interview(client)["stream_token"]

    if case == "garbage":
        token = "not-a-token"
    elif case == "reused":
        with _connect(client) as ws:
            ws.send_json({"type": "start", "token": token})
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "end"})
            _receive_until(ws, "ended")
    elif case == "expired":
        from app.realtime.token import hash_stream_token
        from tests.db import sql
        sql("UPDATE realtime_stream_tokens SET expires_at = now() - interval '1 second' "
            "WHERE token_hash = %s", (hash_stream_token(token),))
    elif case == "someone_elses":
        enroll(client)  # signs in as a different student

    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": token})
        with pytest.raises(WebSocketDisconnect) as refused:
            ws.receive_json()
    assert refused.value.code == 4401

    if case == "someone_elses":
        # Another student's attempt must not use up the owner's token either.
        sign_in_as(client, owner_email)
        with _connect(client) as ws:
            ws.send_json({"type": "start", "token": token})
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "end"})
            _receive_until(ws, "ended")


# --- guardrails and stream renewal ----------------------------------------------------------


def test_a_guardrail_intervention_on_persona_speech_ends_and_flags_the_interview(
    logged_in_client, streams, monkeypatch, audit_log  # noqa: F811
):
    queue, _ = streams
    queue.append(FakeStream([audio_arrived, *persona_says("Something it must never say.")]))
    # If the guardrail failed to stop it, the time limit would end it instead
    # (and the reason assertion below would fail) rather than hanging the test.
    monkeypatch.setattr(get_settings(), "realtime_max_session_minutes", 0.05)

    async def _check(text, source):
        hit = source == "OUTPUT"
        return GuardrailVerdict(intervened=hit, configured=True,
                                policies=("contentPolicy:VIOLENCE",) if hit else ())

    monkeypatch.setattr("app.ai.guardrails.check", _check)
    client, _ = logged_in_client
    started = _start_interview(client)
    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": started["stream_token"]})
        ws.receive_json()
        ws.send_bytes(b"\x00\x00" * 160)
        messages, _ = _receive_until(ws, "ended")

    assert messages[-1] == {"type": "ended", "reason": "guardrail"}
    assert scalar("SELECT source || ':' || reason FROM session_flags WHERE session_id = %s",
                  (started["session_id"],)) == "guardrail:harmful_ai_output"
    (event,) = audit_log.named("ai.guardrail")
    assert event["stage"] == "live_output" and event["policies"] == ["contentPolicy:VIOLENCE"]
    assert "must never say" not in "\n".join(audit_log.lines)


def test_a_long_interview_renews_the_stream_and_keeps_the_conversation(
    logged_in_client, streams, monkeypatch, audit_log  # noqa: F811
):
    queue, opened = streams
    monkeypatch.setattr(get_settings(), "nova_sonic_stream_renew_seconds", 0)
    queue.append(FakeStream([audio_arrived, *user_says("Tell me about the seawall."),
                             *persona_says("It was built in 1962.")]))
    queue.append(FakeStream([]))

    client, _ = logged_in_client
    started = _start_interview(client)
    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": started["stream_token"]})
        ws.receive_json()
        ws.send_bytes(b"\x00\x00" * 160)
        _receive_until(ws, "assistant_turn_end")
        ws.send_bytes(b"\x02\x00" * 160)  # audio during/after the swap is not lost
        deadline = time.monotonic() + 5
        while (len(opened) < 2 or "audioInput" not in opened[1].kinds()) and time.monotonic() < deadline:
            time.sleep(0.02)
        ws.send_json({"type": "end"})
        _receive_until(ws, "ended")

    first, second = opened
    assert first.closed and first.kinds()[-1] == "sessionEnd"
    replayed = [e["textInput"]["content"] for e in second.sent if "textInput" in e]
    assert replayed[1:] == ["Tell me about the seawall.", "It was built in 1962."]
    roles = [e["contentStart"]["role"] for e in second.sent if "contentStart" in e]
    assert roles[:3] == ["SYSTEM", "USER", "ASSISTANT"]
    assert "audioInput" in second.kinds()
    assert audit_log.named("ai.realtime_session")[-1]["renewals"] == 1


def test_frames_that_are_not_audio_are_dropped(logged_in_client, streams):
    queue, opened = streams
    queue.append(FakeStream([]))
    client, _ = logged_in_client
    started = _start_interview(client)
    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": started["stream_token"]})
        ws.receive_json()
        ws.send_bytes(b"\x00\x01\x02")          # odd length: not 16-bit PCM
        ws.send_bytes(b"\x00" * (70 * 1024))    # larger than any audio frame
        ws.send_json({"type": "end"})
        _receive_until(ws, "ended")
    assert "audioInput" not in opened[0].kinds()


def test_the_stream_token_never_reaches_the_audit_log(logged_in_client, streams, audit_log):  # noqa: F811
    queue, _ = streams
    queue.append(FakeStream([]))
    client, _ = logged_in_client
    started = _start_interview(client)
    with _connect(client) as ws:
        ws.send_json({"type": "start", "token": started["stream_token"]})
        ws.receive_json()
        ws.send_json({"type": "end"})
        _receive_until(ws, "ended")
    assert started["stream_token"] not in "\n".join(audit_log.lines)
