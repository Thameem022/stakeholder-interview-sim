"""Pseudonymous participants; sign-in identity moves to a restricted schema

SR-2026-052 item 1.2 (SEC-DATA-001), per the Solution Architecture data model:
student work is keyed only by a pseudonymous participant, and everything that
identifies a person — the sign-in identity and its link to the pseudonym —
lives in a separate, restricted store.

  public.participants         participant_id (uuid) + pseudonymous_code + is_active.
                              Opaque: nothing in it identifies anyone.
  identity.users              the sign-in identity (address, name, credential) and,
                              via participant_id, THE MAPPING to the pseudonym.
  identity.auth_sessions,     sign-in machinery. Moved with users: they reference
  identity.pending_registrations,  users, and pending_registrations / rate-limit
  identity.auth_rate_limits   buckets carry addresses.
  interview_sessions          user_id -> participant_id. Evaluations and retrieval
                              events hang off sessions, so they follow.

Nothing in `public` references `identity`; the only link runs the other way
(identity.users -> public.participants). A role with no rights on the identity
schema can read every work table and still cannot tell who anyone is.

Database roles (NOLOGIN, cluster-wide):
  ses_support_owner   reads the identity schema — the Solution Support Owner.
  ses_course_reader   reads work tables only — instructors / study personnel.
Created here when the migrating user may create roles (development, CI). In
production the database owner cannot, so a superuser creates them first — see
deploy/WPI_DEPLOY.md. Grants are applied whenever the roles exist. The
application's own login role owns both schemas: it must resolve a sign-in to a
participant, which is the one place the link is followed.

Also records the pre-session notice acknowledgement on each session
(notice_version, notice_acknowledged_at); NULL for sessions that predate it.

Reversible without data loss, except that pseudonymous codes are regenerated
on a later re-upgrade (they identify nothing, so nothing depends on them).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007_pseudonymous_participants"
down_revision: Union[str, None] = "0006_retrieval_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDENTITY_TABLES = ("users", "pending_registrations", "auth_sessions", "auth_rate_limits")
_WORK_TABLES = ("participants", "interview_sessions", "session_evaluations", "retrieval_events")


def _grant_if_role_exists(role: str, statements: list[str]) -> None:
    body = "\n".join(f"    EXECUTE {s!r};" for s in statements)
    op.execute(f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
{body}
  END IF;
END $$;
""")


def upgrade() -> None:
    op.execute("CREATE SCHEMA identity")
    # Nobody gets in by default — not even USAGE for PUBLIC.
    op.execute("REVOKE ALL ON SCHEMA identity FROM PUBLIC")

    op.execute("""
        CREATE TABLE participants (
            participant_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            -- Random, never derived from anything about the person. Short enough
            -- to read aloud; 40 bits is ample for a cohort of ~1,000.
            pseudonymous_code text        NOT NULL UNIQUE DEFAULT
                ('P-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 10))),
            is_active         boolean     NOT NULL DEFAULT true,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
    """)

    for table in _IDENTITY_TABLES:
        op.execute(f"ALTER TABLE {table} SET SCHEMA identity")

    # One participant per existing account.
    op.execute("ALTER TABLE identity.users ADD COLUMN participant_id uuid")
    op.execute("UPDATE identity.users SET participant_id = gen_random_uuid()")
    op.execute(
        "INSERT INTO participants (participant_id) SELECT participant_id FROM identity.users"
    )
    op.execute("ALTER TABLE identity.users ALTER COLUMN participant_id SET NOT NULL")
    op.execute(
        "ALTER TABLE identity.users "
        "ADD CONSTRAINT users_participant_id_key UNIQUE (participant_id), "
        "ADD CONSTRAINT users_participant_id_fkey FOREIGN KEY (participant_id) "
        "REFERENCES public.participants(participant_id) ON DELETE RESTRICT"
    )

    # Re-key sessions from the account to the pseudonym.
    op.execute("ALTER TABLE interview_sessions ADD COLUMN participant_id uuid")
    op.execute("""
        UPDATE interview_sessions s
           SET participant_id = u.participant_id
          FROM identity.users u
         WHERE u.id = s.user_id
    """)
    # 0005 made user_id NOT NULL with an FK, so every session has an owner;
    # SET NOT NULL fails the migration (and rolls it back) if that ever broke.
    op.execute("ALTER TABLE interview_sessions ALTER COLUMN participant_id SET NOT NULL")
    op.execute(
        "ALTER TABLE interview_sessions "
        "ADD CONSTRAINT interview_sessions_participant_id_fkey "
        "FOREIGN KEY (participant_id) REFERENCES participants(participant_id) ON DELETE RESTRICT"
    )
    op.execute(
        "CREATE INDEX interview_sessions_participant_idx ON interview_sessions(participant_id)"
    )
    op.execute(
        "ALTER TABLE interview_sessions DROP CONSTRAINT IF EXISTS interview_sessions_user_id_fkey"
    )
    op.execute("DROP INDEX IF EXISTS interview_sessions_user_idx")
    op.execute("ALTER TABLE interview_sessions DROP COLUMN user_id")

    op.execute(
        "ALTER TABLE interview_sessions "
        "ADD COLUMN notice_version text NULL, "
        "ADD COLUMN notice_acknowledged_at timestamptz NULL"
    )

    # Roles, where this connection is allowed to make them.
    op.execute("""
DO $$
BEGIN
  IF (SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user) THEN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ses_support_owner') THEN
      CREATE ROLE ses_support_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ses_course_reader') THEN
      CREATE ROLE ses_course_reader NOLOGIN;
    END IF;
  ELSE
    RAISE NOTICE 'Cannot create roles as %; create ses_support_owner and '
                 'ses_course_reader as a superuser, then re-apply the grants '
                 '(see deploy/WPI_DEPLOY.md).', current_user;
  END IF;
END $$;
""")
    _apply_grants()


def _apply_grants() -> None:
    _grant_if_role_exists("ses_support_owner", [
        "GRANT USAGE ON SCHEMA identity TO ses_support_owner",
        "GRANT SELECT ON ALL TABLES IN SCHEMA identity TO ses_support_owner",
        "GRANT SELECT ON participants TO ses_support_owner",
    ])
    _grant_if_role_exists("ses_course_reader", [
        # Explicit, so a later broad grant elsewhere cannot quietly widen it.
        "REVOKE ALL ON SCHEMA identity FROM ses_course_reader",
        *(f"GRANT SELECT ON {t} TO ses_course_reader" for t in _WORK_TABLES),
    ])


def downgrade() -> None:
    _grant_if_role_exists("ses_course_reader", [
        *(f"REVOKE ALL ON {t} FROM ses_course_reader" for t in _WORK_TABLES),
    ])
    _grant_if_role_exists("ses_support_owner", [
        "REVOKE ALL ON ALL TABLES IN SCHEMA identity FROM ses_support_owner",
        "REVOKE ALL ON SCHEMA identity FROM ses_support_owner",
        "REVOKE ALL ON participants FROM ses_support_owner",
    ])

    op.execute(
        "ALTER TABLE interview_sessions "
        "DROP COLUMN notice_acknowledged_at, DROP COLUMN notice_version"
    )

    op.execute("ALTER TABLE interview_sessions ADD COLUMN user_id uuid")
    op.execute("""
        UPDATE interview_sessions s
           SET user_id = u.id
          FROM identity.users u
         WHERE u.participant_id = s.participant_id
    """)
    op.execute("ALTER TABLE interview_sessions ALTER COLUMN user_id SET NOT NULL")
    op.execute(
        "ALTER TABLE interview_sessions "
        "ADD CONSTRAINT interview_sessions_user_id_fkey "
        "FOREIGN KEY (user_id) REFERENCES identity.users(id) ON DELETE RESTRICT"
    )
    op.execute("CREATE INDEX interview_sessions_user_idx ON interview_sessions(user_id)")
    op.execute("DROP INDEX IF EXISTS interview_sessions_participant_idx")
    op.execute("ALTER TABLE interview_sessions DROP COLUMN participant_id")

    op.execute("ALTER TABLE identity.users DROP COLUMN participant_id")
    op.execute("DROP TABLE participants")

    for table in _IDENTITY_TABLES:
        op.execute(f"ALTER TABLE identity.{table} SET SCHEMA public")
    op.execute("DROP SCHEMA identity")
    # The roles are cluster-wide and may serve other databases; left in place.
