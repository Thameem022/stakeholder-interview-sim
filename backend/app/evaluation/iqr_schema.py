from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


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


class Evidence(BaseModel):
    turn_id: int = Field(..., description="Turn identifier where key evidence was observed.")
    student_quote: str = Field(..., description="Short excerpt of the student's utterance motivating the score.")
    stakeholder_cue: str = Field(..., description="Relevant stakeholder statement, reaction, or contextual cue.")
    alternative_phrasing: Optional[str] = Field(
        default=None,
        description="Verbatim suggestion for a clearer or stronger interview question.",
    )

    @field_validator("turn_id")
    @classmethod
    def validate_turn_id_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("evidence.turn_id must be a positive integer.")
        return value


class IQREvaluation(BaseModel):
    dimension_id: str = Field(..., description="Stable identifier (e.g., 'IQ-01').")
    dimension_name: str = Field(..., description="Human-readable name of the rubric dimension.")
    score: float = Field(..., description="Rubric score on a 10-point scale.")
    skill_level_title: str = Field(..., description="Descriptive skill indicator.")
    label: str = Field(..., description="Textual label or band associated with the score.")
    rationale: str = Field(..., description="Natural language explanation justifying the assigned score.")
    line_of_inquiry_impact: Optional[str] = Field(default=None)
    evidence: Evidence = Field(..., description="Structured evidence pointing back to source transcript turns.")

    @field_validator("score")
    @classmethod
    def validate_score_range(cls, value: float) -> float:
        if not (1.0 <= value <= 10.0):
            raise ValueError("score must be between 1.0 and 10.0 (inclusive).")
        return value


class SessionEvaluation(BaseModel):
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evaluation_results: List[IQREvaluation] = Field(default_factory=list)
    overall_summary: str = Field(...)
