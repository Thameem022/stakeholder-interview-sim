"""users.is_trial — throwaway guest accounts for the "Try SES" link

A trial visitor gets a real users row so that interview_sessions,
session_evaluations and every ownership check keep working unchanged. The flag
is what keeps those rows apart: it gates the trial-only limits (one interview,
Alex only, 10-minute cap) and lets reporting exclude them.

Additive with a default, so existing accounts are untouched.

Revision ID: 0007_trial_users
Revises: 0006_retrieval_events
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_trial_users"
down_revision: Union[str, None] = "0006_retrieval_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_trial boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_trial")
