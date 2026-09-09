"""Strict scorer wrappers with token accounting.

Wraps IQRScorer / SICScorer for evaluation use. Three things differ from the
production call path:

  * allow_fallback=False — a silent downgrade to gpt-4o-mini would otherwise be
    recorded as a measurement of gpt-4o.
  * SIC goes through grade_raw(), which returns the per-item labels the
    statistics need and skips the cosmetic enrichment call evaluate() makes.
  * Every call is costed and timed, so a run's spend is measured rather than
    estimated afterwards.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from langchain_core.callbacks import BaseCallbackHandler

from app.evaluation.iqr_schema import SessionEvaluation, Transcript
from app.evaluation.iqr_scorer import DEFAULT_PROMPT_PATH, IQRScorer
from app.evaluation.sic_scorer import (
    DEFAULT_SIC_PROMPT_PATH,
    SIC_KEYS_DIR,
    SICGradingResult,
    SICScorer,
)
from evals.lib.cache import sha256_file, sha256_obj

# USD per 1M tokens. Cached input is billed at half rate on both models, and the
# stable system-prompt prefix means most repeat runs hit that discount.
PRICING = {
    "gpt-4o":                 {"in": 2.50, "cached_in": 1.25, "out": 10.00},
    "gpt-4o-mini":            {"in": 0.15, "cached_in": 0.075, "out": 0.60},
    "text-embedding-3-small": {"in": 0.02, "cached_in": 0.02, "out": 0.0},
}


class UsageCollector(BaseCallbackHandler):
    """Captures token usage and the serving fingerprint for every LLM call.

    Two shapes have to be handled. The IQR chain ends in a PydanticOutputParser
    and populates llm_output["token_usage"]; the SIC chain uses
    with_structured_output, where usage usually arrives on the message as
    usage_metadata instead. Reading only one of them yields a column of zeros
    that is not noticed until the study is over.
    """

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_tokens = 0
        self.fingerprints: list[Optional[str]] = []
        self.calls = 0

    def on_llm_end(self, response, **kwargs: Any) -> None:  # noqa: ANN001
        self.calls += 1
        out = getattr(response, "llm_output", None) or {}
        usage = dict(out.get("token_usage") or {})

        if not usage:
            try:
                message = response.generations[0][0].message
                meta = getattr(message, "usage_metadata", None) or {}
                usage = {
                    "prompt_tokens": meta.get("input_tokens", 0),
                    "completion_tokens": meta.get("output_tokens", 0),
                    "prompt_tokens_details": {
                        "cached_tokens": (meta.get("input_token_details") or {}).get("cache_read", 0)
                    },
                }
            except (AttributeError, IndexError):
                usage = {}

        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        if hasattr(details, "get"):
            self.cached_tokens += int(details.get("cached_tokens") or 0)
        self.fingerprints.append(out.get("system_fingerprint"))

    def cost_usd(self, model: str) -> float:
        rates = PRICING.get(model)
        if not rates:
            return 0.0
        fresh = max(self.prompt_tokens - self.cached_tokens, 0)
        return (
            fresh * rates["in"] / 1e6
            + self.cached_tokens * rates["cached_in"] / 1e6
            + self.completion_tokens * rates["out"] / 1e6
        )

    def as_dict(self, model: str) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": round(self.cost_usd(model), 6),
            "system_fingerprint": self.fingerprints[0] if self.fingerprints else None,
            "llm_calls": self.calls,
        }


# ── Content hashes: what the cache key is built from ─────────────────────────

def iqr_prompt_sha(scorer: Optional[IQRScorer] = None) -> str:
    return sha256_file(scorer._prompt_path if scorer else DEFAULT_PROMPT_PATH)


def sic_prompt_sha(scorer: Optional[SICScorer] = None) -> str:
    return sha256_file(scorer._prompt_path if scorer else DEFAULT_SIC_PROMPT_PATH)


def sic_rubric_sha(persona_id: str) -> str:
    """Hash of the persona's SIC key. Editing a catalogue item must invalidate."""
    return sha256_file(SIC_KEYS_DIR / f"{persona_id}_sic_key.json")


def iqr_schema_sha() -> str:
    return sha256_obj(SessionEvaluation.model_json_schema())


def sic_schema_sha() -> str:
    return sha256_obj(SICGradingResult.model_json_schema())


# ── Scoring calls ────────────────────────────────────────────────────────────

def make_scorers(model: str, temperature: float = 0.0) -> tuple[IQRScorer, SICScorer]:
    """One scorer pair per model, shared across a whole run.

    The async clients are safe to reuse, and rebuilding them per call would
    re-read the prompt files hundreds of times for nothing.
    """
    return (
        IQRScorer(model=model, allow_fallback=False, temperature=temperature),
        SICScorer(model=model, allow_fallback=False, temperature=temperature),
    )


async def score_iqr(scorer: IQRScorer, transcript: Transcript) -> dict:
    collector = UsageCollector()
    started = time.perf_counter()
    error: Optional[str] = None
    result: Optional[dict] = None
    try:
        evaluation = await scorer.evaluate(transcript, config={"callbacks": [collector]})
        result = evaluation.model_dump()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return {
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "model_used": scorer.last_model_used,
        "scorer_error": scorer.last_error,
        "error": error,
        "result": result,
        **collector.as_dict(scorer._model),
    }


async def score_sic(scorer: SICScorer, persona_id: str, turns: list[dict]) -> dict:
    collector = UsageCollector()
    started = time.perf_counter()
    error: Optional[str] = None
    result: Optional[dict] = None
    try:
        grading = await scorer.grade_raw(persona_id, turns, config={"callbacks": [collector]})
        result = grading.model_dump()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return {
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "model_used": scorer.last_model_used,
        "scorer_error": scorer.last_error,
        "error": error,
        "result": result,
        **collector.as_dict(scorer._model),
    }
