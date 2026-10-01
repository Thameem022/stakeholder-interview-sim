"""One scoring call, with the fallback policy both scorers share.

The primary model is tried first. A refusal has already been routed to the
fallback model inside the call (client-side refusal middleware); a transient
failure — connection, rate limit, server error, or output that stayed invalid
after its one retry — is retried once on the fallback model. Anything else
(a bad request, missing permissions) is a configuration problem and raises.

Returns the result and, when the primary failed, a short description of why
— recorded so a downgraded run is never mistaken for a primary-model run.
"""

from __future__ import annotations

from typing import Optional, TypeVar

from pydantic import BaseModel

from app.ai.claude import TRANSIENT_ERRORS, StructuredLLM, StructuredResult, Usage

T = TypeVar("T", bound=BaseModel)


async def generate_with_fallback(
    llm: StructuredLLM,
    *,
    model: str,
    fallback_model: str,
    allow_fallback: bool,
    system: str,
    user: str,
    schema: type[T],
    usage: Optional[Usage] = None,
) -> tuple[StructuredResult[T], Optional[str]]:
    try:
        result = await llm.generate(model=model, system=system, user=user, schema=schema)
        error = None
    except TRANSIENT_ERRORS as e:
        if not allow_fallback:
            raise
        # Type and message of our own exceptions only; SDK messages can quote
        # request content, so for those the type alone is kept.
        error = type(e).__name__ + (f": {e}" if e.__class__.__module__.startswith("app.") else "")
        result = await llm.generate(model=fallback_model, system=system, user=user, schema=schema)
    if usage is not None:
        usage.add(result.usage)
    return result, error
