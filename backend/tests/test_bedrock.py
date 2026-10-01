"""SR-2026-052 items 1.1 / 1.6: AI on Amazon Bedrock, credentials server-side.

The Claude client runs through the real Anthropic SDK (Bedrock Mantle client,
refusal-fallback middleware) against an in-process mock transport, so request
shapes and response handling are exercised without AWS access. Titan and
Guardrails use stubbed boto3 clients.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import anthropic
import httpx2
import pytest
from anthropic import AsyncAnthropicBedrockMantle, BetaRefusalFallbackMiddleware, DefaultAsyncHttpxClient
from pydantic import BaseModel

from app.ai import claude, embeddings, guardrails
from app.ai.claude import ClaudeOnBedrock, InvalidModelOutput, ScoringRefused
from app.config import Settings, check_aws, get_settings
from app.evaluation.generation import generate_with_fallback
from tests.db import scalar
from tests.fake_llm import FakeLLM

OPUS, SONNET = "anthropic.claude-opus-5-5", "anthropic.claude-sonnet-5-5"


class Answer(BaseModel):
    score: int
    note: str


def _message(model: str, *, text: str | None = None, stop: str = "end_turn") -> dict:
    content = [] if text is None else [{"type": "text", "text": text}]
    body = {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20,
                  "cache_read_input_tokens": 80, "cache_creation_input_tokens": 0},
    }
    if stop == "refusal":
        body["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": None}
    return body


def _client(replies, *, fallback: str | None = SONNET):
    """A ClaudeOnBedrock over the real SDK; `replies(model, n)` returns a message."""
    requests: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        n = sum(1 for r in requests if r["body"]["model"] == body["model"])
        return httpx2.Response(200, json=replies(body["model"], n))

    sdk = AsyncAnthropicBedrockMantle(
        aws_region="us-east-1", skip_auth=True, max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        middleware=[BetaRefusalFallbackMiddleware([{"model": fallback}])] if fallback else None,
    )
    return ClaudeOnBedrock(refusal_fallback=fallback, client=sdk), requests


# --- Claude on Bedrock (scoring) ---------------------------------------------------


async def test_a_scoring_call_goes_to_claude_on_bedrock_with_effort_and_caching():
    llm, requests = _client(lambda m, n: _message(m, text='```json\n{"score": 7, "note": "ok"}\n```'))
    result = await llm.generate(model=OPUS, system="RUBRIC", user="TRANSCRIPT", schema=Answer)

    assert result.value == Answer(score=7, note="ok") and result.model == OPUS
    assert result.usage.cache_read_input_tokens == 80 and result.usage.calls == 1
    (req,) = requests
    assert req["url"].startswith("https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages")
    body = req["body"]
    assert body["model"] == OPUS
    assert body["output_config"] == {"effort": get_settings().bedrock_scoring_effort}
    # Opus 5.5 rejects sampling parameters; none may be sent.
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    system = body["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert system["text"].startswith("RUBRIC") and '"score"' in system["text"]
    assert body["messages"] == [{"role": "user", "content": "TRANSCRIPT"}]


async def test_invalid_output_gets_exactly_one_retry():
    replies = iter(["not json", '{"score": 3, "note": "fine"}'])
    llm, requests = _client(lambda m, n: _message(m, text=next(replies)))
    result = await llm.generate(model=OPUS, system="s", user="u", schema=Answer)
    assert result.value.score == 3 and len(requests) == 2
    assert result.usage.calls == 2


async def test_output_that_stays_invalid_fails_rather_than_being_guessed():
    llm, requests = _client(lambda m, n: _message(m, text='{"score": "high"}'))
    with pytest.raises(InvalidModelOutput, match="schema validation failed"):
        await llm.generate(model=OPUS, system="s", user="u", schema=Answer)
    assert len(requests) == 2


async def test_truncated_output_is_retried_then_fails():
    llm, _ = _client(lambda m, n: _message(m, text='{"score": 1', stop="max_tokens"))
    with pytest.raises(InvalidModelOutput, match="truncated"):
        await llm.generate(model=OPUS, system="s", user="u", schema=Answer)


async def test_a_refusal_falls_back_to_the_second_model():
    def replies(model, n):
        if model == OPUS:
            return _message(model, stop="refusal")
        return _message(model, text='{"score": 5, "note": "from fallback"}')

    llm, requests = _client(replies)
    result = await llm.generate(model=OPUS, system="s", user="u", schema=Answer)
    assert result.model == SONNET and result.value.note == "from fallback"
    assert [r["body"]["model"] for r in requests] == [OPUS, SONNET]


async def test_a_refusal_with_no_fallback_raises():
    llm, _ = _client(lambda m, n: _message(m, stop="refusal"), fallback=None)
    with pytest.raises(ScoringRefused, match="cyber"):
        await llm.generate(model=OPUS, system="s", user="u", schema=Answer)


# --- the scorers' fallback policy ------------------------------------------------------


async def test_a_transient_error_retries_once_on_the_fallback_model():
    llm = FakeLLM(Answer(score=1, note="x"), fail=[InvalidModelOutput("schema validation failed")])
    result, error = await generate_with_fallback(
        llm, model=OPUS, fallback_model=SONNET, allow_fallback=True,
        system="s", user="u", schema=Answer,
    )
    assert [c["model"] for c in llm.calls] == [OPUS, SONNET]
    assert result.model == SONNET and error.startswith("InvalidModelOutput")


async def test_evaluation_runs_never_fall_back():
    llm = FakeLLM(Answer(score=1, note="x"), fail=[InvalidModelOutput("bad")])
    with pytest.raises(InvalidModelOutput):
        await generate_with_fallback(
            llm, model=OPUS, fallback_model=SONNET, allow_fallback=False,
            system="s", user="u", schema=Answer,
        )
    assert [c["model"] for c in llm.calls] == [OPUS]


async def test_a_configuration_error_is_not_masked_by_the_fallback():
    request = httpx2.Request("POST", "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages")
    denied = anthropic.PermissionDeniedError("denied", response=httpx2.Response(403, request=request), body=None)
    llm = FakeLLM(Answer(score=1, note="x"), fail=[denied])
    with pytest.raises(anthropic.PermissionDeniedError):
        await generate_with_fallback(
            llm, model=OPUS, fallback_model=SONNET, allow_fallback=True,
            system="s", user="u", schema=Answer,
        )
    assert len(llm.calls) == 1


async def test_scorers_default_to_the_configured_bedrock_models():
    from app.evaluation.iqr_schema import Transcript, Turn
    from app.evaluation.iqr_scorer import IQRScorer

    dims = ["framing_and_stakeholder_fit", "question_quality_and_precision",
            "probing_and_follow_up_depth", "listening_interpretation_and_stewardship"]
    reply = json.dumps({
        "dimensions": [{"dimension": d, "score": 6, "assessment": "ok"} for d in dims],
        "overall_score": 9, "skill_label": "Developing", "moments": [],
    })
    llm = FakeLLM(reply)
    scorer = IQRScorer(llm=llm)
    result = await scorer.evaluate(Transcript(
        metadata={"persona_key": "alex_martinez"},
        turns=[Turn(turn_id=1, speaker="Student", text="Hello")],
    ))
    assert llm.calls[0]["model"] == get_settings().bedrock_scoring_model
    assert result.metadata["judge_model"] == get_settings().bedrock_scoring_model
    assert result.metadata["judge_effort"] == get_settings().bedrock_scoring_effort
    assert result.overall_score == 6.0  # the code-side formula, not the judge's 9


# --- Titan embeddings ---------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, dims: int = 1024, guardrail: dict | None = None):
        self.dims, self.guardrail = dims, guardrail
        self.invocations: list[dict] = []
        self.guardrail_calls: list[dict] = []

    def invoke_model(self, **kwargs):
        self.invocations.append(kwargs)
        return {"body": io.BytesIO(json.dumps({"embedding": [0.1] * self.dims}).encode())}

    def apply_guardrail(self, **kwargs):
        self.guardrail_calls.append(kwargs)
        return self.guardrail or {"action": "NONE", "assessments": []}


async def test_embeddings_come_from_titan_with_the_configured_dimension(monkeypatch):
    runtime = _FakeRuntime()
    monkeypatch.setattr(embeddings, "bedrock_runtime", lambda: runtime)
    vectors = await embeddings.embed_texts(["flood barriers", "zoning"])

    assert len(vectors) == 2 and all(len(v) == 1024 for v in vectors)
    call = runtime.invocations[0]
    assert call["modelId"] == "amazon.titan-embed-text-v2:0"
    assert json.loads(call["body"]) == {"inputText": "flood barriers", "dimensions": 1024, "normalize": True}


async def test_an_embedding_of_the_wrong_size_is_refused(monkeypatch):
    monkeypatch.setattr(embeddings, "bedrock_runtime", lambda: _FakeRuntime(dims=1536))
    with pytest.raises(ValueError, match="1536 dimensions"):
        await embeddings.embed_texts(["x"])


def test_the_vector_columns_match_titan():
    for table in ("persona_chunks", "world_bible_chunks"):
        typ = scalar(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attname = 'embedding'", (table,)
        )
        assert typ == "vector(1024)"


# --- Guardrails -----------------------------------------------------------------------


async def test_without_a_guardrail_checks_pass_but_say_so(monkeypatch):
    monkeypatch.setattr(get_settings(), "bedrock_guardrail_id", "")
    verdict = await guardrails.check("anything", "OUTPUT")
    assert verdict.intervened is False and verdict.configured is False


async def test_a_guardrail_intervention_reports_policies_not_text(monkeypatch):
    monkeypatch.setattr(get_settings(), "bedrock_guardrail_id", "gr-test")
    runtime = _FakeRuntime(guardrail={
        "action": "GUARDRAIL_INTERVENED",
        "assessments": [{"contentPolicy": {"filters": [
            {"type": "VIOLENCE", "confidence": "HIGH", "action": "BLOCKED"},
        ]}}],
    })
    monkeypatch.setattr(guardrails, "bedrock_runtime", lambda: runtime)
    verdict = await guardrails.check("something harmful", "OUTPUT")
    assert verdict.intervened and verdict.policies == ("contentPolicy:VIOLENCE",)
    call = runtime.guardrail_calls[0]
    assert call["guardrailIdentifier"] == "gr-test" and call["source"] == "OUTPUT"


def test_harmful_feedback_is_withheld_and_the_session_flagged(
    logged_in_client, owned_session, monkeypatch
):
    import app.evaluation.iqr_scorer as iqr
    import app.evaluation.sic_scorer as sic
    from app.ai.guardrails import GuardrailVerdict
    from app.evaluation.iqr_schema import SessionEvaluation

    dims = ["framing_and_stakeholder_fit", "question_quality_and_precision",
            "probing_and_follow_up_depth", "listening_interpretation_and_stewardship"]

    class _IQR:
        prompt_version, last_model_used = "v", OPUS

        async def evaluate(self, *a, **k):
            return SessionEvaluation(
                dimensions=[{"dimension": d, "score": 5, "assessment": "harmful text"} for d in dims],
                overall_score=5, skill_label="x",
            )

    class _SIC:
        prompt_version, last_model_used = "v", OPUS

        async def evaluate(self, *a, **k):
            return []

    async def _intervene(text, source):
        return GuardrailVerdict(intervened=True, configured=True, policies=("contentPolicy:HATE",))

    monkeypatch.setattr(iqr, "IQRScorer", _IQR)
    monkeypatch.setattr(sic, "SICScorer", _SIC)
    monkeypatch.setattr(guardrails, "check", _intervene)

    client, user_id = logged_in_client
    sid = owned_session(user_id, transcript='[{"role":"user","text":"hi","timestamp":"t"}]')
    r = client.post(f"/api/eval/iqr?session_id={sid}")

    assert r.status_code == 503 and r.json()["detail"]["code"] == "feedback_under_review"
    assert "harmful text" not in r.text
    assert scalar("SELECT count(*) FROM session_evaluations WHERE session_id = %s", (str(sid),)) == 0
    assert scalar("SELECT source || ':' || reason FROM session_flags WHERE session_id = %s",
                  (str(sid),)) == "guardrail:harmful_ai_output"


# --- credentials: server-side only, never a long-term key ------------------------------


@pytest.mark.parametrize("env,ok", [
    ({"AWS_ACCESS_KEY_ID": "AKIA-long-term"}, False),
    ({"AWS_ACCESS_KEY_ID": "ASIA-temp", "AWS_SESSION_TOKEN": "token"}, True),
    ({}, True),
])
def test_production_refuses_a_long_term_aws_key(monkeypatch, env, ok):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    s = Settings(environment="prod", bedrock_guardrail_id="gr")
    if ok:
        check_aws(s)
    else:
        with pytest.raises(RuntimeError, match="long-term AWS access key"):
            check_aws(s)


def test_production_requires_a_guardrail(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    with pytest.raises(RuntimeError, match="BEDROCK_GUARDRAIL_ID"):
        check_aws(Settings(environment="prod", bedrock_guardrail_id=""))


async def test_the_speech_sdk_gets_credentials_from_the_botocore_chain(monkeypatch):
    from app.ai import aws

    class _Frozen:
        access_key, secret_key, token = "ASIA-temp", "secret", "session-token"

    class _Creds:
        def get_frozen_credentials(self):
            return _Frozen()

    class _Session:
        def get_credentials(self):
            return _Creds()

    monkeypatch.setattr(aws, "session", lambda: _Session())
    identity = await aws.BotocoreIdentityResolver().get_identity(properties={})
    assert identity.access_key_id == "ASIA-temp" and identity.session_token == "session-token"


# --- nothing talks to OpenAI any more -------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_SCANNED = [
    _ROOT / "backend" / "app", _ROOT / "backend" / "scripts", _ROOT / "backend" / "evals" / "lib",
    _ROOT / "backend" / "evals" / "scripts", _ROOT / "frontend" / "src",
]
_FILES = [_ROOT / "backend" / "pyproject.toml", _ROOT / "backend" / "uv.lock",
          _ROOT / ".env.example", _ROOT / "docker-compose.yml", _ROOT / "frontend" / "package.json"]


def test_no_code_path_calls_openai():
    pattern = re.compile(r"openai|gpt-4|gpt-realtime|text-embedding-3", re.I)
    hits = []
    for root in _SCANNED:
        if root.is_dir():
            for p in root.rglob("*"):
                if p.suffix in {".py", ".ts", ".tsx", ".js"}:
                    hits += [f"{p.relative_to(_ROOT)}:{i}" for i, line in
                             enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pattern.search(line)]
    for p in _FILES:
        if p.is_file():
            hits += [f"{p.relative_to(_ROOT)}:{i}" for i, line in
                     enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pattern.search(line)]
    assert hits == [], f"OpenAI references remain: {hits}"


def test_no_aws_secret_is_committed():
    secret = re.compile(r"AKIA[0-9A-Z]{16}|aws_secret_access_key\s*=\s*\S+", re.I)
    for p in [_ROOT / ".env.example", *(_ROOT / "deploy").rglob("*")]:
        if p.is_file():
            assert not secret.search(p.read_text(encoding="utf-8", errors="ignore")), p
    assert claude  # module imported: the client exists without any key configured
