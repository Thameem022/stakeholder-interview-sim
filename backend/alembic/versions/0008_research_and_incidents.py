"""Research consent and separation; app roles; session flags and purge

IRB-27-0033 + SR-2026-052 items 0.1, 0.2, 1.4 (SEC-PRI-001, SEC-INC-001).

research schema — technically separate from course data, readable only by
approved study personnel. Nothing in `public` references it, and the course
reader role is never granted it, so a coursework query cannot even see whether
a student consented.

  research.research_consent   one row per participant: the choice, the consent
                              text version it was made against, and when.
  research.session_records    consented COPIES of sessions (transcript +
                              evaluation), keyed by pseudonym. Copies, because
                              research retention follows the protocol, not the
                              course schedule — the course data may be deleted
                              at term end while a consented copy legitimately
                              remains. No FK to course tables for that reason.
  research.export_approvals   a named approver's single-use, expiring approval.
  research.export_log         every export attempt, exported or refused.

identity.account_roles — application roles on sign-in accounts (instructor,
study_personnel, export_approver, support_owner). In the identity schema
because a role belongs to a person, not to a pseudonym.

public.session_flags — a session flagged for review (sensitive disclosure,
harmful AI output, ...). Survives a purge and the session's own deletion
(session_id is SET NULL), because it is the incident record.
interview_sessions.purged_at — set when a flagged session's content is purged.

DB role ses_study_personnel (NOLOGIN) reads the research schema. Created where
the migrating user may create roles, like the 0007 roles.

Downgrade DROPS the research schema, roles and flags with their data — export
anything that must be kept, under an approval, before downgrading.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008_research_and_incidents"
down_revision: Union[str, None] = "0007_pseudonymous_participants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _if_role(role: str, statements: list[str]) -> None:
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
    op.execute("CREATE SCHEMA research")
    op.execute("REVOKE ALL ON SCHEMA research FROM PUBLIC")

    op.execute("""
        CREATE TABLE research.research_consent (
            id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            participant_id   uuid        NOT NULL UNIQUE
                                         REFERENCES public.participants(participant_id)
                                         ON DELETE CASCADE,
            consented        boolean     NOT NULL,
            scope            text        NOT NULL DEFAULT 'transcripts_and_feedback',
            consent_version  text        NOT NULL,
            decided_at       timestamptz NOT NULL DEFAULT now(),
            withdrawn_at     timestamptz
        )
    """)

    op.execute("""
        CREATE TABLE research.session_records (
            id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id         uuid        NOT NULL UNIQUE,
            participant_id     uuid        NOT NULL,
            pseudonymous_code  text        NOT NULL,
            persona_id         text,
            transcript         jsonb       NOT NULL,
            evaluation         jsonb,
            consent_version    text        NOT NULL,
            captured_at        timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX session_records_participant_idx ON research.session_records(participant_id)"
    )

    op.execute("""
        CREATE TABLE research.export_approvals (
            id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            approver_user_id  uuid        NOT NULL,
            approver_name     text        NOT NULL,
            purpose           text        NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            expires_at        timestamptz NOT NULL,
            used_at           timestamptz,
            used_by_user_id   uuid
        )
    """)

    op.execute("""
        CREATE TABLE research.export_log (
            id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            requested_by_user_id  uuid        NOT NULL,
            approval_id           uuid,
            approver_name         text,
            outcome               text        NOT NULL CHECK (outcome IN ('exported', 'refused')),
            reason                text,
            record_count          int,
            created_at            timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE identity.account_roles (
            user_id     uuid        NOT NULL REFERENCES identity.users(id) ON DELETE CASCADE,
            role        text        NOT NULL CHECK (role IN
                            ('instructor', 'study_personnel', 'export_approver', 'support_owner')),
            granted_at  timestamptz NOT NULL DEFAULT now(),
            granted_by  text        NOT NULL,
            PRIMARY KEY (user_id, role)
        )
    """)

    op.execute("""
        CREATE TABLE session_flags (
            id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id   uuid        REFERENCES interview_sessions(id) ON DELETE SET NULL,
            source       text        NOT NULL CHECK (source IN
                             ('participant', 'instructor', 'guardrail', 'support')),
            reason       text        NOT NULL CHECK (reason IN
                             ('sensitive_disclosure', 'distress', 'harmful_ai_output', 'other')),
            note         text        CHECK (char_length(note) <= 500),
            flagged_by   uuid,
            status       text        NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'reviewed', 'purged')),
            created_at   timestamptz NOT NULL DEFAULT now(),
            purged_at    timestamptz,
            purged_by    uuid
        )
    """)
    op.execute("CREATE INDEX session_flags_session_idx ON session_flags(session_id)")
    op.execute("ALTER TABLE interview_sessions ADD COLUMN purged_at timestamptz")

    op.execute("""
DO $$
BEGIN
  IF (SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user) THEN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ses_study_personnel') THEN
      CREATE ROLE ses_study_personnel NOLOGIN;
    END IF;
  ELSE
    RAISE NOTICE 'Cannot create roles as %; create ses_study_personnel as a '
                 'superuser (see deploy/WPI_DEPLOY.md).', current_user;
  END IF;
END $$;
""")
    _if_role("ses_study_personnel", [
        "GRANT USAGE ON SCHEMA research TO ses_study_personnel",
        "GRANT SELECT ON ALL TABLES IN SCHEMA research TO ses_study_personnel",
    ])
    _if_role("ses_course_reader", [
        "REVOKE ALL ON SCHEMA research FROM ses_course_reader",
        "GRANT SELECT ON session_flags TO ses_course_reader",
    ])
    _if_role("ses_support_owner", [
        "GRANT SELECT ON identity.account_roles TO ses_support_owner",
        "GRANT SELECT ON session_flags TO ses_support_owner",
    ])


def downgrade() -> None:
    _if_role("ses_study_personnel", [
        "REVOKE ALL ON ALL TABLES IN SCHEMA research FROM ses_study_personnel",
        "REVOKE ALL ON SCHEMA research FROM ses_study_personnel",
    ])
    op.execute("ALTER TABLE interview_sessions DROP COLUMN purged_at")
    op.execute("DROP TABLE session_flags")
    op.execute("DROP TABLE identity.account_roles")
    op.execute("DROP SCHEMA research CASCADE")
