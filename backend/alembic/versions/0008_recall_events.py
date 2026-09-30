"""recall_events — per-tool-call telemetry for the knowledge tools

One table for both knowledge tools rather than one each. `recall` and
`world_lookup` answer the same operational question — what did the persona
reach for, did it have anything to say, and how long did the interviewer wait
— and splitting that across two tables means every latency or coverage query
becomes a UNION.

The columns that belong to only one tool are nullable and named for it:
`topics` is recall's, `query` and `path` are world_lookup's. `tool` says which
row shape to expect. `mode`, `ids_returned` and `earned` are common to both.

Numbered 0008, not 0006: 0006 is retrieval_events (the vector-search telemetry
for /realtime/retrieve, which this does not replace) and 0007 is the corpus
migration this depends on.

Unlike retrieval_events, the FK has no ON DELETE CASCADE clause spelled out —
it takes the default NO ACTION, so deleting a session with recall rows fails
loudly rather than silently dropping the telemetry.

Revision ID: 0008_recall_events
Revises: 0007_disclosure_mode_and_corpus
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008_recall_events"
down_revision: Union[str, None] = "0007_disclosure_mode_and_corpus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE recall_events (
            id            bigserial   PRIMARY KEY,
            session_id    uuid        NOT NULL REFERENCES interview_sessions(id),
            created_at    timestamptz NOT NULL DEFAULT now(),
            tool          text        NOT NULL,   -- 'recall' | 'world_lookup'
            topics        text[],                 -- recall
            query         text,                   -- world_lookup
            mode          smallint    NOT NULL,
            ids_returned  text[]      NOT NULL,
            earned        boolean[]   NOT NULL,
            path          text,                   -- 'entity' | 'fts' | 'vector'
            latency_ms    integer
        )
        """
    )
    op.execute(
        "CREATE INDEX recall_events_session_idx ON recall_events (session_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recall_events")
