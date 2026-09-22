from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Transcript models — consumed by iqr_scorer and the realtime layer.
# Do not remove or rename these.
# ---------------------------------------------------------------------------

class Turn(BaseModel):
    turn_id: int = Field(..., description="Monotonically increasing 1-based index for the turn.")
    speaker: str = Field(..., description="Role label of the speaker (e.g., 'Student', 'Alex Martinez').")
    text: str = Field(..., description="Raw text of the utterance for this turn.")
    intent: Optional[str] = Field(default=None)
    iqr_markers: List[str] = Field(default_factory=list)

    @field_validator("turn_id")
    @classmethod
    def validate_turn_id_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("turn_id must be a positive integer.")
        return value


class Transcript(BaseModel):
    metadata: Dict[str, Any] = Field(default_factory=dict)
    turns: List[Turn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# IQR v2 evaluation models — 4-dimension scores plus the three moments.
#
# Display-only renames (Framing & Fit, Question Quality, Follow-Up Depth,
# Listening & Interpretation) deliberately do NOT change these literal keys:
# every stored session_evaluations row and the 16-row golden dataset key off
# them, and renaming would orphan both.
# ---------------------------------------------------------------------------

DimensionName = Literal[
    "framing_and_stakeholder_fit",
    "question_quality_and_precision",
    "probing_and_follow_up_depth",
    "listening_interpretation_and_stewardship",
]


StakeholderResponsePattern = Literal[
    "became_guarded",
    "opened_up",
    "neutral",
]


class DimensionAssessment(BaseModel):
    dimension: DimensionName
    score: float = Field(..., ge=1.0, le=10.0)
    assessment: str = Field(
        ...,
        description="About two sentences, second person. The only per-dimension prose shown.",
    )
    stakeholder_response_pattern: Optional[StakeholderResponsePattern] = Field(
        default=None,
        description=(
            "Only set for framing_and_stakeholder_fit. Names how the stakeholder's "
            "disclosure shifted in response to the student's framing/tone. "
            "'became_guarded' = stakeholder shortened answers, stopped volunteering, "
            "or got tight-lipped after a specific student turn. 'opened_up' = "
            "stakeholder visibly deepened disclosure after a student move. "
            "'neutral' = no clear inflection."
        ),
    )
    cause_effect_explanation: Optional[str] = Field(
        default=None,
        description=(
            "Only required when stakeholder_response_pattern is 'became_guarded' "
            "or 'opened_up'. One concrete sentence in the format "
            "'Because you <specific student behavior>, <stakeholder name> "
            "<observable response shift>, <consequence for the interview>.' "
            "Must name the specific student turn that caused the shift. "
            "Generated but no longer displayed on the Framing card — it informs "
            "the Framing moment, which covers the same turn with more context."
        ),
    )


# ---------------------------------------------------------------------------
# Three moments — the evidence layer of the report.
#
# One short stretch of transcript (two to four turns) where something
# consequential happened. Replaces the per-dimension evidence_quote and
# what_was_missed fields, which restated the assessments.
# ---------------------------------------------------------------------------

ProducedLabel = Literal["persona_did", "you_learned"]

OutcomeKind = Literal["it_cost_you", "out_of_reach", "go_further"]


class Moment(BaseModel):
    headline: str = Field(
        ...,
        description=(
            "States plainly what the stakeholder did and what the student did. "
            "No metaphors. e.g. 'Your opening put Alex in the wrong chair.'"
        ),
    )
    dimension: DimensionName = Field(
        ..., description="Which dimension this moment belongs to; rendered as a tag."
    )
    persona_offered: Optional[str] = Field(
        default=None,
        description=(
            "The opening, the hedge, the loaded word the stakeholder put on the "
            "table. Null when the student's own turn started the exchange."
        ),
    )
    student_quote: str = Field(
        ..., description="Verbatim quote from the student's turns."
    )
    what_it_produced: str = Field(
        ..., description="What the exchange actually produced."
    )
    produced_label: ProducedLabel = Field(
        ...,
        description=(
            "'persona_did' when the stakeholder acted in response; 'you_learned' "
            "when the point is what the student came away with. Selects the "
            "static row label."
        ),
    )
    outcome: str = Field(
        ...,
        description=(
            "What stayed out of reach, or what a further step would have gotten. "
            "Must NOT name a knowledge item — use out_of_reach_item for that."
        ),
    )
    outcome_kind: OutcomeKind = Field(
        ...,
        description=(
            "'it_cost_you' when the move cost the interview something later; "
            "'out_of_reach' when something specific stayed closed; 'go_further' "
            "when the move worked and the note is an extension."
        ),
    )
    out_of_reach_item: Optional[str] = Field(
        default=None,
        description=(
            "Exact display_label of a knowledge item from the catalogue list "
            "provided in the system message, or null. The item's real state is "
            "resolved downstream against the coverage grades."
        ),
    )
    technique_name: str = Field(
        ..., description="Short imperative name for the move to try instead."
    )
    technique_stem: str = Field(
        ..., description="One example sentence the student could have said."
    )


class SessionEvaluation(BaseModel):
    metadata: Dict[str, Any] = Field(default_factory=dict)
    dimensions: List[DimensionAssessment] = Field(..., min_length=4, max_length=4)
    overall_score: float = Field(..., ge=1.0, le=10.0)
    skill_label: str
    # Three for the pilot, not five — five is where the report starts
    # overwhelming again. Not min_length=3: a four-turn transcript has no three
    # consequential moments, and forcing the count invites fabrication.
    moments: List[Moment] = Field(default_factory=list, max_length=3)
