"""Bedrock Guardrails (SR-2026-052 SEC-AI-001 / SEC-SDLC-001).

Applied with the ApplyGuardrail API to text the system handles, so the same
guardrail covers every model:

- persona speech (source OUTPUT) — harmful or off-scenario content ends the
  interview and flags the session for review;
- student turns (source INPUT) — e.g. personal information or distress — flag
  the session for review (the interview continues);
- scoring feedback (source OUTPUT) — withheld from the student and flagged.

The guardrail's own policies (content filters, denied topics, PII entities)
are configured in AWS, not here. Without a configured guardrail (development
only — production refuses to start) checks report `configured=False` and pass.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

from app.ai.aws import bedrock_runtime
from app.config import settings

logger = logging.getLogger(__name__)

Source = Literal["INPUT", "OUTPUT"]
# ApplyGuardrail limits the size of each text block; feedback and turns are
# well under, and anything longer is checked in slices.
_SLICE = 20000


@dataclass(frozen=True)
class GuardrailVerdict:
    intervened: bool
    configured: bool
    # Policy names that fired (e.g. "contentPolicy:VIOLENCE", "sensitiveInformationPolicy:EMAIL").
    # Never the matched text.
    policies: tuple[str, ...] = field(default_factory=tuple)


def _policies(assessments: list[dict]) -> tuple[str, ...]:
    found: list[str] = []
    for a in assessments or []:
        for policy, body in a.items():
            if not isinstance(body, dict):
                continue
            for key, items in body.items():
                if isinstance(items, list):
                    for item in items:
                        label = item.get("type") or item.get("name") or key
                        if item.get("action") not in (None, "NONE"):
                            found.append(f"{policy}:{label}")
    return tuple(sorted(set(found)))


def _apply(text: str, source: Source) -> GuardrailVerdict:
    policies: list[str] = []
    intervened = False
    for start in range(0, max(len(text), 1), _SLICE):
        response = bedrock_runtime().apply_guardrail(
            guardrailIdentifier=settings.bedrock_guardrail_id,
            guardrailVersion=settings.bedrock_guardrail_version,
            source=source,
            content=[{"text": {"text": text[start:start + _SLICE]}}],
        )
        if response.get("action") == "GUARDRAIL_INTERVENED":
            intervened = True
            policies.extend(_policies(response.get("assessments") or []))
    return GuardrailVerdict(intervened=intervened, configured=True, policies=tuple(sorted(set(policies))))


async def check(text: str, source: Source) -> GuardrailVerdict:
    if not settings.bedrock_guardrail_id:
        return GuardrailVerdict(intervened=False, configured=False)
    if not text.strip():
        return GuardrailVerdict(intervened=False, configured=True)
    return await asyncio.to_thread(_apply, text, source)
