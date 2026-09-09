"""retrieval_events — per-tool-call timing and result telemetry for the RAG path

The realtime model calls /realtime/retrieve roughly 20-40 times per interview,
and until now nothing about those calls was kept: no latency, no scores, no
record of which chunks the persona was actually handed. That makes two
questions unanswerable after the fact — how fast retrieval is in production,
and whether the corpus had anything relevant to say.

One row per retrieve call. embed_ms and search_ms are separated because they
are different problems: embed_ms is a network round trip to OpenAI, search_ms
is pgvector on the local box, and only one of them is ours to tune.

The write is fire-and-forget on the request path (see app/realtime/retrieve.py).
A failed insert must never surface to the student mid-interview, and the insert
must never add latency to a persona's reply, so this table is deliberately
append-only with no constraints beyond the session FK.

ON DELETE CASCADE, unlike session_evaluations: an evaluation is research data
worth protecting from an accidental session delete, whereas a retrieval event
is operational telemetry that has no meaning once its session is gone.

Revision ID: 0006_retrieval_events
Revises: 0005_session_ownership
Create Date: 2026-09-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006_retrieval_events"
down_revision: Union[str, None] = "0005_session_ownership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE retrieval_events (
            id                 bigserial   PRIMARY KEY,
            -- Nullable: the retrieve endpoint is reachable without a session id
            -- (an older client, or an ad-hoc call), and telemetry that drops
            -- those rows would quietly under-report the call volume.
            session_id         uuid        NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
            persona_id         text        NOT NULL,
            query              text        NOT NULL,
            embed_ms           double precision,
            search_ms          double precision,
            total_ms           double precision,
            persona_top_scores real[],
            world_top_scores   real[],
            persona_chunk_ids  text[],
            world_chunk_ids    text[],
            k_persona          int,
            k_world            int,
            ef_search          int,
            error              text,
            created_at         timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX retrieval_events_session_idx "
        "ON retrieval_events(session_id, created_at)"
    )
    # Latency percentiles are computed over a time window across all sessions,
    # which the composite index above cannot serve (its leading column is
    # session_id, and the interesting queries do not filter on it).
    op.execute(
        "CREATE INDEX retrieval_events_created_idx ON retrieval_events(created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retrieval_events")
