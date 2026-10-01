"""Research-participation consent (IRB-27-0033, SEC-PRI-001).

The text below is a DRAFT placeholder. It must be replaced, word for word, by
the consent form approved through the IRB / OGC / Data Governance gate before
research participation is switched on — and CONSENT_APPROVED set True at the
same time. Production refuses to start with RESEARCH_ENABLED and an
unapproved text (check_research_gate), so the draft can never reach students.

The version is stored with every decision, so each choice is tied to the
exact wording the student saw. Change the wording -> bump the version.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.config import settings

CONSENT_VERSION = "draft-2026-10-01"
CONSENT_APPROVED = False


class ConsentText(BaseModel):
    version: str
    title: str
    points: list[str]
    yes_label: str
    no_label: str


CONSENT_TEXT = ConsentText(
    version=CONSENT_VERSION,
    title="Research participation (optional)",
    points=[
        "DRAFT — replace with the approved consent form before enabling.",
        "Approved study personnel are researching how interviewing skill develops. "
        "If you agree, they may study a copy of your interview transcripts and feedback.",
        "Saying no has no effect on your coursework or your grade. Your choice is never "
        "shown in any coursework view.",
        "Research copies are stored under your pseudonymous ID, not your name.",
        "You can change your mind at any time. Withdrawing deletes your research copies.",
    ],
    yes_label="Yes, I agree to take part",
    no_label="No, I do not want to take part",
)


def check_research_gate() -> None:
    """Refuse a production start that would collect consent on draft text."""
    if settings.is_production and settings.research_enabled and not CONSENT_APPROVED:
        raise RuntimeError(
            "RESEARCH_ENABLED=true with ENVIRONMENT=prod, but the consent text in "
            "app/research/consent.py is not the approved form (CONSENT_APPROVED is "
            "False). Install the approved text first, or leave research disabled."
        )
