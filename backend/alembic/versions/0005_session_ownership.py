"""interview_sessions.user_id: backfill, NOT NULL, and the FK

Completes the ownership column that 0002 added and deliberately left loose
("FK + NOT NULL when auth lands"). After this, an interview session without an
owner is unrepresentable, which is what lets every read and write filter by the
signed-in user.

Existing rows predate authentication and have no owner. They are assigned to
the account named by LEGACY_SESSION_OWNER_EMAIL, or to the oldest account if
that is unset. If neither can be resolved the migration RAISES rather than
guessing or discarding — these rows are interview transcripts with evaluations
attached, and silently reassigning or dropping research data is worse than a
failed deploy. env.py wraps migrations in a transaction, so a raise here rolls
the backfill back cleanly.

The variable is read from the environment directly rather than from Settings:
the application never needs it, and an unused setting reads like live config.

The FK is ON DELETE RESTRICT, deliberately unlike auth_sessions.user_id which
CASCADEs. A login session is disposable state; an interview transcript is the
research data. RESTRICT turns "clean up this old account" into an explicit
decision instead of unannounced data loss, and steers account retirement toward
the `users.is_active = false` soft delete that load_session_user already honours.

NOTE: downgrade() cannot restore the original NULLs. It removes the constraint
and the NOT NULL, but every backfilled row keeps its assigned owner.

Revision ID: 0005_session_ownership
Revises: 0004_auth_rate_limits
Create Date: 2026-08-25
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005_session_ownership"
down_revision: Union[str, None] = "0004_auth_rate_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    orphans = conn.execute(
        sa.text("SELECT count(*) FROM interview_sessions WHERE user_id IS NULL")
    ).scalar_one()

    if orphans:
        owner_email = (os.getenv("LEGACY_SESSION_OWNER_EMAIL") or "").strip().lower()

        if owner_email:
            owner_id = conn.execute(
                sa.text("SELECT id FROM users WHERE email = :e"), {"e": owner_email}
            ).scalar()
            if owner_id is None:
                raise RuntimeError(
                    f"LEGACY_SESSION_OWNER_EMAIL={owner_email} matches no users row, "
                    f"and {orphans} interview_sessions have no owner. Register and "
                    "set a password for that account first, or unset the variable to "
                    "fall back to the oldest account."
                )
        else:
            # Tie-break on id: created_at defaults to transaction time, so two
            # accounts made in one transaction could tie. Determinism is free.
            row = conn.execute(
                sa.text("SELECT id, email FROM users ORDER BY created_at, id LIMIT 1")
            ).first()
            if row is None:
                raise RuntimeError(
                    f"{orphans} interview_sessions have no user_id and the users "
                    "table is empty, so there is nobody to assign them to. Create an "
                    "account, set LEGACY_SESSION_OWNER_EMAIL to it, and re-run. To "
                    "discard the old interview data instead, delete those rows "
                    "deliberately first — this migration will not do it for you."
                )
            owner_id, owner_email = row

        conn.execute(
            sa.text("UPDATE interview_sessions SET user_id = :u WHERE user_id IS NULL"),
            {"u": owner_id},
        )
        print(f"0005: assigned {orphans} orphaned interview_sessions to {owner_email}")

    # Both statements take ACCESS EXCLUSIVE and scan the table. Trivial at this
    # size; at scale you would ADD CONSTRAINT ... NOT VALID then VALIDATE.
    op.execute("ALTER TABLE interview_sessions ALTER COLUMN user_id SET NOT NULL")
    op.execute(
        "ALTER TABLE interview_sessions "
        "ADD CONSTRAINT interview_sessions_user_id_fkey "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE interview_sessions "
        "DROP CONSTRAINT IF EXISTS interview_sessions_user_id_fkey"
    )
    op.execute("ALTER TABLE interview_sessions ALTER COLUMN user_id DROP NOT NULL")
    # The backfilled owners stay. Which rows were originally NULL is not
    # recorded anywhere, so this is not reversible.
