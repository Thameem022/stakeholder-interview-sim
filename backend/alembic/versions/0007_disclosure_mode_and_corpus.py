"""disclosure mode on sessions; persona_knowledge and world_sections tables

Three things, all serving the same shift: moving what the persona knows out of
flat files and into the database, and recording which version of that content
produced a given session.

interview_sessions gains the disclosure state the realtime loop carries turn to
turn (disclosure_mode, mode_streak) plus two provenance stamps. corpus_version
and prompt_version are nullable on purpose: every session that already exists
predates the stamping, and backfilling them with a guess would make old rows
claim a version they were never run under.

persona_knowledge holds the SIC catalogue as rows. `claim` is the grader-facing
statement of the fact and must never be sent to a live persona — it is written
as an assertion, and a model handed it will read it back verbatim. `in_voice`
is the sayable version, `deflection` the line for when the item stays closed.

world_sections is the structured world bible: retrievable by vector, by
full-text, or by entity. It sits alongside world_bible_chunks rather than
replacing it — nothing reads world_sections yet.

Revision ID: 0007_disclosure_mode_and_corpus
Revises: 0006_retrieval_events
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_disclosure_mode_and_corpus"
down_revision: Union[str, None] = "0006_retrieval_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE interview_sessions
          ADD COLUMN disclosure_mode smallint NOT NULL DEFAULT 1,
          ADD COLUMN mode_streak     smallint NOT NULL DEFAULT 0,
          ADD COLUMN corpus_version  text,
          ADD COLUMN prompt_version  text
        """
    )

    op.execute(
        """
        CREATE TABLE persona_knowledge (
            id         text     PRIMARY KEY,
            persona_id text     NOT NULL,
            tier       smallint NOT NULL,
            topics     text[]   NOT NULL,
            -- Grader only. Never assembled into a live prompt: see the note in
            -- this migration's docstring.
            claim      text     NOT NULL,
            in_voice   text     NOT NULL,
            deflection text,
            -- Redundant as a constraint (id is already the primary key), kept
            -- for the index it creates: persona_id leads it, which is what
            -- serves "every item this persona holds".
            UNIQUE (persona_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX persona_knowledge_topics_idx "
        "ON persona_knowledge USING gin (topics)"
    )

    op.execute(
        """
        CREATE TABLE world_sections (
            id        text PRIMARY KEY,
            section   text,
            heading   text,
            body      text,
            entities  text[],
            known_by  text[],
            embedding vector(1536),
            tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED
        )
        """
    )
    op.execute("CREATE INDEX world_sections_tsv_idx ON world_sections USING gin (tsv)")
    op.execute(
        "CREATE INDEX world_sections_entities_idx ON world_sections USING gin (entities)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS world_sections")
    op.execute("DROP TABLE IF EXISTS persona_knowledge")
    op.execute(
        """
        ALTER TABLE interview_sessions
          DROP COLUMN IF EXISTS prompt_version,
          DROP COLUMN IF EXISTS corpus_version,
          DROP COLUMN IF EXISTS mode_streak,
          DROP COLUMN IF EXISTS disclosure_mode
        """
    )
