"""interview_sessions.mode: voice or text

SR-2026-052 item 2.1 (SEC-ACC-001). Students can hold the interview in
writing instead of by voice (deaf or hard of hearing, a speech disability, no
quiet space, or simply by choice). Both modes produce the same transcript and
go through the same scoring; the mode is recorded so the pilot can compare
them, and nothing else depends on it. Existing sessions were all voice.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0012_interview_mode"
down_revision: Union[str, None] = "0011_bedrock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE interview_sessions ADD COLUMN mode text NOT NULL DEFAULT 'voice' "
        "CHECK (mode IN ('voice', 'text'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE interview_sessions DROP COLUMN mode")
