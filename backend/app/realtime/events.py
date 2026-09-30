"""recall_events writes — shared by the `recall` and `world_lookup` tools.

Fire-and-forget, for the same reason /realtime/retrieve's telemetry is: these
run inside a live voice turn with the interviewer waiting, so a slow or failing
insert must never add latency to the persona's reply or surface to the student.
A dropped telemetry row is a worse research dataset; a raised one is dead air.

That also means both endpoints work before migration 0008 is applied — the
insert fails, a warning is logged, and the tool call is still answered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from uuid import UUID

from app.db import get_pool

logger = logging.getLogger(__name__)

# Strong references to in-flight writes. asyncio holds only a weak reference to
# a running task, so one nobody keeps can be collected mid-flight — which shows
# up much later as "some events are missing".
_bg_tasks: set[asyncio.Task] = set()


async def _insert(fields: dict[str, Any]) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO recall_events (
                    session_id, tool, topics, query, mode,
                    ids_returned, earned, path, latency_ms
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                fields["session_id"],
                fields["tool"],
                fields["topics"],
                fields["query"],
                fields["mode"],
                fields["ids_returned"],
                fields["earned"],
                fields["path"],
                fields["latency_ms"],
            )
    except Exception as e:
        logger.warning(f"failed to record recall event: {e}")


def record_recall_event(
    *,
    session_id: UUID,
    tool: str,
    mode: int,
    ids_returned: list[str],
    earned: list[bool],
    latency_ms: float,
    topics: Optional[list[str]] = None,
    query: Optional[str] = None,
    path: Optional[str] = None,
) -> None:
    """Queue one row. Never awaited by a request handler."""
    fields = {
        "session_id": session_id,
        "tool": tool,
        "topics": topics,
        "query": query,
        "mode": mode,
        "ids_returned": ids_returned,
        "earned": earned,
        "path": path,
        "latency_ms": int(round(latency_ms)),
    }
    try:
        task = asyncio.create_task(_insert(fields))
    except RuntimeError:
        return  # no running loop — nothing to record onto
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
