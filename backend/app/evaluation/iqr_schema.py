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
# IQR v1 evaluation models — 4-dimension, whole-interview structure.
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
    assessment: str
    evidence_quote: str
    what_was_missed: str
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
            "Must name the specific student turn that caused the shift."
        ),
    )


class TopStrip(BaseModel):
    strength: str
    missed_opportunities: List[str] = Field(..., min_length=1, max_length=2)
    next_move: str


class SessionEvaluation(BaseModel):
    metadata: Dict[str, Any] = Field(default_factory=dict)
    dimensions: List[DimensionAssessment] = Field(..., min_length=4, max_length=4)
    overall_score: float = Field(..., ge=1.0, le=10.0)
    skill_label: str
    overall_summary: str
    depth_note: str
    earned_vs_volunteered_note: str
    top_strip: TopStrip
