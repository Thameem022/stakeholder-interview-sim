"""Claude on Amazon Bedrock, for rubric scoring — through the official SDK.

Uses `AsyncAnthropicBedrockMantle` (Bedrock's Messages-API endpoint), which
signs with the standard AWS credential chain (see app/ai/aws.py for where those
credentials come from). Three things shape the calls:

- **No structured-outputs feature on this endpoint**, so the response schema is
  given in the system prompt and every response is validated against the
  Pydantic model in code. An invalid or truncated response gets exactly one
  retry, then fails — it is never repaired or guessed at.
- **No temperature**: Claude Opus 5.5 rejects sampling parameters. Runs are
  made comparable by a fixed `effort`, schema validation, and the code-side
  IQR weighting; the effort level is recorded with every evaluation.
- **Refusals fall back** to a second model via the SDK's client-side
  `BetaRefusalFallbackMiddleware` (server-side `fallbacks` is not available on
  Bedrock). Which model actually answered is returned to the caller.

The system prompt is marked for prompt caching: it is long, identical for every
interview with the same persona, and only the transcript varies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Generic, Optional, Protocol, TypeVar

import anthropic
from anthropic import AsyncAnthropicBedrockMantle, BetaFallbackState, BetaRefusalFallbackMiddleware
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaTextBlockParam
from pydantic import BaseModel, ValidationError

from app.config import settings

T = TypeVar("T", bound=BaseModel)

# Non-streaming; generous enough for the IQR report plus adaptive thinking.
DEFAULT_MAX_TOKENS = 16000
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class ScoringRefused(Exception):
    """Every model in the chain declined (stop_reason == "refusal")."""


class InvalidModelOutput(Exception):
    """The response was not valid against the schema, even after one retry."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.calls += other.calls


@dataclass
class StructuredResult(Generic[T]):
    value: T
    model: str
    usage: Usage = field(default_factory=Usage)


class StructuredLLM(Protocol):
    """What the scorers depend on — the Bedrock client, or a test double."""

    async def generate(
        self, *, model: str, system: str, user: str, schema: type[T],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> StructuredResult[T]: ...


def schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "OUTPUT FORMAT: Respond with a single JSON object and nothing else — no "
        "prose, no Markdown fences. It must validate against this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"), sort_keys=True)
    )


def _json_text(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


def _usage(response: Any) -> Usage:
    u = getattr(response, "usage", None)
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        calls=1,
    )


class ClaudeOnBedrock:
    """`StructuredLLM` over Claude on Bedrock.

    `refusal_fallback` names the model a declined request is retried on (None
    disables it — evaluation runs measure one model at a time).
    """

    def __init__(
        self,
        *,
        refusal_fallback: Optional[str] = None,
        client: Optional[AsyncAnthropicBedrockMantle] = None,
    ) -> None:
        self._refusal_fallback = refusal_fallback
        self._client = client

    def _get_client(self) -> AsyncAnthropicBedrockMantle:
        if self._client is None:
            middleware = (
                [BetaRefusalFallbackMiddleware([{"model": self._refusal_fallback}])]
                if self._refusal_fallback else None
            )
            self._client = AsyncAnthropicBedrockMantle(
                aws_region=settings.aws_region,
                max_retries=3,  # 408/409/429/5xx and connection errors
                timeout=180.0,
                middleware=middleware,
            )
        return self._client

    async def generate(
        self, *, model: str, system: str, user: str, schema: type[T],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> StructuredResult[T]:
        client = self._get_client()
        system_blocks: list[BetaTextBlockParam] = [{
            "type": "text",
            "text": f"{system}\n\n{schema_instructions(schema)}",
            "cache_control": {"type": "ephemeral"},
        }]
        messages: list[BetaMessageParam] = [{"role": "user", "content": user}]
        output_config: BetaOutputConfigParam = {"effort": settings.bedrock_scoring_effort}
        total = Usage()
        problem = "no attempt made"
        for _attempt in range(2):
            # One fallback state per request: it pins only this call's retry.
            with BetaFallbackState():
                response = await client.beta.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_blocks,
                    messages=messages,
                    output_config=output_config,
                )
            total.add(_usage(response))

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                raise ScoringRefused(getattr(details, "category", None) or "refused")
            if response.stop_reason == "max_tokens":
                problem = "truncated at max_tokens"
                continue
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                value = schema.model_validate_json(_json_text(text))
            except ValidationError as e:
                # The error names fields and types, never the content.
                problem = f"schema validation failed ({e.error_count()} errors)"
                continue
            return StructuredResult(value=value, model=response.model, usage=total)
        raise InvalidModelOutput(problem)


# Errors worth a second try on the fallback model. A refusal already had its
# own fallback inside the call; schema failures already had their retry.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    InvalidModelOutput,
)
