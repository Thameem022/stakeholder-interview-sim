"""Strict scorer wrappers with token accounting.

Wraps IQRScorer / SICScorer for evaluation use. Three things differ from the
production call path:

  * allow_fallback=False — a silent downgrade to the fallback model would
    otherwise be recorded as a measurement of the primary one (and with it off,
    refusals are not rerouted either).
  * SIC goes through grade_raw(), which returns the per-item labels the
    statistics need and skips the cosmetic enrichment call evaluate() makes.
  * Every call is costed and timed, so a run's spend is measured rather than
    estimated afterwards.
"""

from __future__ import annotations

import time
from typing import Optional

from app.ai.claude import Usage
from app.config import settings
from app.evaluation.iqr_schema import SessionEvaluation, Transcript
from app.evaluation.iqr_scorer import DEFAULT_PROMPT_PATH, IQRScorer
from app.evaluation.sic_scorer import (
    DEFAULT_SIC_PROMPT_PATH,
    SIC_KEYS_DIR,
    SICGradingResult,
    SICScorer,
)
from evals.lib.cache import sha256_file, sha256_obj

# USD per 1M tokens — Anthropic's published first-party rates, used as an
# ESTIMATE for budget guards. Claude on Amazon Bedrock is billed by AWS at its
# own rates; use the AWS bill, not this table, for actual spend.
PRICING = {
    "anthropic.claude-opus-5-5":   {"in": 4.00, "cached_in": 0.20, "cache_write": 5.00, "out": 20.00},
    "anthropic.claude-sonnet-5-5": {"in": 2.00, "cached_in": 0.20, "cache_write": 2.50, "out": 10.00},
}


class UsageCollector:
    """Token usage across every call a scorer makes for one evaluation."""

    def __init__(self) -> None:
        self.usage = Usage()

    def cost_usd(self, model: str) -> float:
        rates = PRICING.get(model)
        if not rates:
            return 0.0
        u = self.usage
        return (
            u.input_tokens * rates["in"] / 1e6
            + u.cache_read_input_tokens * rates["cached_in"] / 1e6
            + u.cache_creation_input_tokens * rates["cache_write"] / 1e6
            + u.output_tokens * rates["out"] / 1e6
        )

    def as_dict(self, model: str) -> dict:
        u = self.usage
        return {
            "prompt_tokens": u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens,
            "completion_tokens": u.output_tokens,
            "cached_tokens": u.cache_read_input_tokens,
            "cost_usd": round(self.cost_usd(model), 6),  # an estimate; see PRICING
            "llm_calls": u.calls,
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

def make_scorers(model: Optional[str] = None) -> tuple[IQRScorer, SICScorer]:
    """One scorer pair per model, shared across a whole run.

    The async clients are safe to reuse, and rebuilding them per call would
    re-read the prompt files hundreds of times for nothing. Claude Opus 5.5
    takes no temperature; the run's effort level is BEDROCK_SCORING_EFFORT.
    """
    model = model or settings.bedrock_scoring_model
    return (
        IQRScorer(model=model, allow_fallback=False),
        SICScorer(model=model, allow_fallback=False),
    )


async def score_iqr(scorer: IQRScorer, transcript: Transcript) -> dict:
    collector = UsageCollector()
    started = time.perf_counter()
    error: Optional[str] = None
    result: Optional[dict] = None
    try:
        evaluation = await scorer.evaluate(transcript, config={"usage": collector.usage})
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
        grading = await scorer.grade_raw(persona_id, turns, config={"usage": collector.usage})
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
