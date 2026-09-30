"""world_sections.tsv — index the heading, weighted above the body

The generated column added in 0007 was `to_tsvector('english', body)`, which
left the heading out entirely. That is backwards: the heading is the most
topical line in the section. "10.3 The Downtown Waterfront Resilience
Feasibility Study (2018)" names the thing the section is about, and a question
naming that study could not match it lexically at all.

Heading goes in at weight A, body at weight B, so a title match outranks a
passing mention in prose. ts_rank's default weights are {D,C,B,A} =
{0.1, 0.2, 0.4, 1.0}, so a heading hit scores 2.5x a body hit — which is also
what makes a rank floor meaningful on the OR-semantics query in world_lookup.py.

The column is dropped and recreated rather than altered: a STORED generated
column's expression cannot be changed in place. That rebuilds the tsvector for
all 91 rows, which is why the index is recreated after rather than before.

Revision ID: 0009_world_sections_tsv_heading
Revises: 0008_recall_events
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009_world_sections_tsv_heading"
down_revision: Union[str, None] = "0008_recall_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dropping the column takes its index with it.
    op.execute("ALTER TABLE world_sections DROP COLUMN tsv")
    op.execute(
        """
        ALTER TABLE world_sections
          ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(heading, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(body, '')), 'B')
          ) STORED
        """
    )
    op.execute("CREATE INDEX world_sections_tsv_idx ON world_sections USING gin (tsv)")


def downgrade() -> None:
    op.execute("ALTER TABLE world_sections DROP COLUMN tsv")
    op.execute(
        """
        ALTER TABLE world_sections
          ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
            to_tsvector('english', body)
          ) STORED
        """
    )
    op.execute("CREATE INDEX world_sections_tsv_idx ON world_sections USING gin (tsv)")
