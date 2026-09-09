"""auth_rate_limits table

Postgres-backed rate limiting for the auth endpoints. An append-only event log
rather than a counter row: "how many events in this bucket within the window"
is a single index range scan, and there is no read-modify-write to race.

A bucket key is "<action>:<dimension>:<value>", e.g. "login:email:a@wpi.edu"
or "register:ip:10.0.0.4". Rows outside the window are pruned per-bucket on
write, so the table stays bounded without a sweeper job.

Revision ID: 0004_auth_rate_limits
Revises: 0003_auth_tables
Create Date: 2026-08-18
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_auth_rate_limits"
down_revision: Union[str, None] = "0003_auth_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE auth_rate_limits (
            id          bigserial   PRIMARY KEY,
            bucket      text        NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX auth_rate_limits_bucket_idx "
        "ON auth_rate_limits(bucket, occurred_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_rate_limits")
