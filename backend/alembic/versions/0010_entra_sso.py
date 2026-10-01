"""Entra ID single sign-on replaces local accounts

SR-2026-052 item 1.3 (SEC-IAM-001). Sign-in is Entra ID OIDC only; there is
no local password anywhere.

identity.users
  + entra_subject   the Entra object id (oid) — the stable key a sign-in is
                    matched on. NULL only for accounts created before SSO,
                    which are linked to their Entra identity by address on
                    their first SSO sign-in (so their pseudonym and work carry
                    over), then never matched by address again.
  + last_login_at
  - password_hash   gone.
identity.pending_registrations  dropped — there is no registration.
identity.oidc_logins            in-flight sign-ins: state, nonce and PKCE
                                verifier, bound to the browser that started
                                them, single-use, ten minutes.

Downgrade restores the columns and table but NOT any password: accounts come
back with an unusable password hash and would need a reset.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010_entra_sso"
down_revision: Union[str, None] = "0009_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE identity.users ADD COLUMN entra_subject text UNIQUE")
    op.execute("ALTER TABLE identity.users ADD COLUMN last_login_at timestamptz")
    op.execute("ALTER TABLE identity.users DROP COLUMN password_hash")
    op.execute("DROP TABLE identity.pending_registrations")

    op.execute("""
        CREATE TABLE identity.oidc_logins (
            state_hash     text        PRIMARY KEY,
            binding_hash   text        NOT NULL,
            nonce          text        NOT NULL,
            code_verifier  text        NOT NULL,
            return_to      text        NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now(),
            expires_at     timestamptz NOT NULL
        )
    """)
    op.execute("CREATE INDEX oidc_logins_expires_idx ON identity.oidc_logins(expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE identity.oidc_logins")
    op.execute("""
        CREATE TABLE identity.pending_registrations (
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
    op.execute(
        "CREATE UNIQUE INDEX pending_registrations_email_active_idx "
        "ON identity.pending_registrations(email) WHERE consumed_at IS NULL"
    )
    # '!' never verifies, so no account becomes reachable without a reset.
    op.execute("ALTER TABLE identity.users ADD COLUMN password_hash text NOT NULL DEFAULT '!'")
    op.execute("ALTER TABLE identity.users ALTER COLUMN password_hash DROP DEFAULT")
    op.execute("ALTER TABLE identity.users DROP COLUMN last_login_at")
    op.execute("ALTER TABLE identity.users DROP COLUMN entra_subject")
