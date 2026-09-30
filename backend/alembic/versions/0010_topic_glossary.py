"""topic_glossary — what each recall topic actually covers

The `recall` tool constrains its `topics` parameter to an enum of tag names,
which tells the model what it may choose but not what any of it means. A tag
like `capacity` or `institutional_process` is only self-evident to whoever
authored the catalogue. This table carries a one-line gloss per tag so the tool
description can define the vocabulary it is asking the model to select from.

Authored in the persona knowledge JSON next to `topic_vocabulary` and loaded by
scripts/load_knowledge.py, like everything else in persona_knowledge. It lives
in the database rather than being read from the files at request time because
the realtime path already reads its knowledge from the database, and one source
of truth beats two.

Keyed on the topic alone, not on persona: `topic_vocabulary` is identical
across all four personas, so a gloss is a property of the vocabulary rather
than of anyone holding it. The loader enforces that the files agree.

Revision ID: 0010_topic_glossary
Revises: 0009_world_sections_tsv_heading
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010_topic_glossary"
down_revision: Union[str, None] = "0009_world_sections_tsv_heading"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE topic_glossary (
            topic text PRIMARY KEY,
            gloss text NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS topic_glossary")
