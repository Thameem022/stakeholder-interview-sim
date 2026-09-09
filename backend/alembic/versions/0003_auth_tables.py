"""auth tables: pending_registrations, users, auth_sessions

Adds the account-creation and login schema:
  - pending_registrations — registrations that have not completed the
    set-password step. These are NOT accounts; a row only becomes a user once
    the temporary password is exchanged for a real one.
  - users — completed accounts.
  - auth_sessions — server-side session records; the cookie carries a token
    whose hash is stored here, never the token itself.

The delivery_* columns on pending_registrations are unused while the temporary
password is a fixed config value. They exist now so that adding real email
delivery later does not require another migration.

No FK from interview_sessions.user_id yet — that lands with route protection,
alongside the NOT NULL that 0002 deferred.

Revision ID: 0003_auth_tables
Revises: 0002_session_evaluations
Create Date: 2026-08-18
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_auth_tables"
down_revision: Union[str, None] = "0002_session_evaluations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Case-insensitive email comparison. Without this, Alex@wpi.edu and
    # alex@wpi.edu would be two distinct accounts.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.execute("""
        CREATE TABLE pending_registrations (
            id                       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            email                    citext      NOT NULL,
            first_name               text        NOT NULL,
            last_name                text        NOT NULL,
            temp_password_hash       text        NOT NULL,
            expires_at               timestamptz NOT NULL,
            consumed_at              timestamptz,
            attempts                 int         NOT NULL DEFAULT 0,
            delivery_status          text        NOT NULL DEFAULT 'not_sent',
            delivery_attempted_at    timestamptz,
            created_at               timestamptz NOT NULL DEFAULT now()
        )
    """)

    # One live registration per address. Consumed rows are left in place as an
    # audit trail, so the uniqueness is partial rather than a plain constraint.
    op.execute(
        "CREATE UNIQUE INDEX pending_registrations_email_active_idx "
        "ON pending_registrations(email) WHERE consumed_at IS NULL"
    )

    op.execute("""
        CREATE TABLE users (
            id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            email         citext      NOT NULL UNIQUE,
            first_name    text        NOT NULL,
            last_name     text        NOT NULL,
            password_hash text        NOT NULL,
            is_active     boolean     NOT NULL DEFAULT true,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE auth_sessions (
            id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id      uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash   text        NOT NULL UNIQUE,
            expires_at   timestamptz NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now()
        )
    """)

    # UNIQUE on token_hash already provides the lookup index used on every
    # authenticated request. This one covers the FK: without it, deleting a
    # user sequential-scans auth_sessions to cascade.
    op.execute("CREATE INDEX auth_sessions_user_idx ON auth_sessions(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_sessions")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS pending_registrations")
    # citext is left installed, matching how 0001 leaves the vector extension.
