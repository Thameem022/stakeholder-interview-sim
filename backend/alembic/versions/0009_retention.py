"""Retention: deletion_log; research consent outlives the course record

SR-2026-052 item 1.8 (SEC-RET-001).

deletion_log — one row per run of the retention job (app/jobs/retention.py):
what it was configured to do, what it deleted (counts only, never content),
and how it ended. It is the evidence that the schedule is being enforced.

research.research_consent loses its FK to public.participants. Course data —
participants included — is deleted at term end, but a consent decision is
part of the research record and must live exactly as long as the research
copies it authorises (until the protocol's own end date). With the cascading
FK, term-end deletion would have silently erased consent and orphaned every
consented copy.

interview_sessions.participant_id stays ON DELETE RESTRICT, deliberately.
The retention job deletes in dependency order inside one transaction and logs
what it removed; a cascade from participants would let any other participant
delete wipe student work without a deletion-log entry.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009_retention"
down_revision: Union[str, None] = "0008_research_and_incidents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE deletion_log (
            id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at   timestamptz NOT NULL DEFAULT now(),
            finished_at  timestamptz,
            status       text        NOT NULL CHECK (status IN
                             ('running', 'completed', 'dry_run', 'failed')),
            parameters   jsonb       NOT NULL,
            counts       jsonb       NOT NULL DEFAULT '{}'::jsonb,
            error        text
        )
    """)
    op.execute("CREATE INDEX deletion_log_started_idx ON deletion_log(started_at)")

    op.execute(
        "ALTER TABLE research.research_consent "
        "DROP CONSTRAINT IF EXISTS research_consent_participant_id_fkey"
    )

    op.execute("""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ses_support_owner') THEN
    EXECUTE 'GRANT SELECT ON deletion_log TO ses_support_owner';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ses_course_reader') THEN
    EXECUTE 'GRANT SELECT ON deletion_log TO ses_course_reader';
  END IF;
END $$;
""")


def downgrade() -> None:
    # Consent rows whose participant is already gone cannot regain the FK.
    op.execute("""
        DELETE FROM research.research_consent c
        WHERE NOT EXISTS (
            SELECT 1 FROM public.participants p WHERE p.participant_id = c.participant_id
        )
    """)
    op.execute(
        "ALTER TABLE research.research_consent "
        "ADD CONSTRAINT research_consent_participant_id_fkey FOREIGN KEY (participant_id) "
        "REFERENCES public.participants(participant_id) ON DELETE CASCADE"
    )
    op.execute("DROP TABLE deletion_log")
