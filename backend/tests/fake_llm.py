"""A `StructuredLLM` test double: records exactly what the judge would receive."""

from __future__ import annotations

from typing import Any, Callable, Optional

from app.ai.claude import StructuredResult, Usage


class FakeLLM:
    """Returns `reply` (a model instance, a JSON string, or a callable taking
    the call kwargs) and records every call. `fail` raises instead, once per
    entry, before replies resume."""

    def __init__(self, reply: Any = None, *, fail: Optional[list[BaseException]] = None) -> None:
        self.reply = reply
        self.fail = list(fail or [])
        self.calls: list[dict] = []

    async def generate(self, *, model, system, user, schema, max_tokens=16000):
        call = {"model": model, "system": system, "user": user, "schema": schema}
        self.calls.append(call)
        if self.fail:
            raise self.fail.pop(0)
        reply = self.reply(call) if isinstance(self.reply, Callable) else self.reply
        value = schema.model_validate_json(reply) if isinstance(reply, str) else reply
        return StructuredResult(value=value, model=model, usage=Usage(input_tokens=10, output_tokens=5, calls=1))
