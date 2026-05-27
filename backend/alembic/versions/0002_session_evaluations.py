"""session_evaluations table and forward-looking interview_sessions.user_id

Adds:
  - interview_sessions.user_id (uuid NULL) — no FK yet; FK + NOT NULL when auth lands.
  - session_evaluations table — stores every IQR+SIC run as a separate row so
    re-scoring keeps history. Composite (session_id, created_at DESC) index
    makes "latest for session" an index seek.

Revision ID: 0002_session_evaluations
Revises: 0001_initial
Create Date: 2026-05-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_session_evaluations"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Forward-looking ownership column. FK and NOT NULL added when users
    # table is introduced.
    op.execute("ALTER TABLE interview_sessions ADD COLUMN user_id uuid NULL")
    op.execute(
        "CREATE INDEX interview_sessions_user_idx ON interview_sessions(user_id)"
    )

    op.execute(
        """
        CREATE TABLE session_evaluations (
            id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id      uuid        NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
            evaluation      jsonb       NOT NULL,
            scorer_metadata jsonb       NOT NULL DEFAULT '{}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX session_evaluations_session_idx "
        "ON session_evaluations(session_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_evaluations")
    op.execute("DROP INDEX IF EXISTS interview_sessions_user_idx")
    op.execute("ALTER TABLE interview_sessions DROP COLUMN IF EXISTS user_id")
