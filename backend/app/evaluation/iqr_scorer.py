from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.evaluation.iqr_schema import SessionEvaluation, TopStrip, Transcript
from app.personas.prompt_assembly import _load_persona_config

# Default prompt path — evaluator system prompts (versioned; bump version here to upgrade)
DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "iqr" / "v2" / "system_prompt.txt"
SIC_KEYS_DIR = Path(__file__).parent / "sic_keys"


def _load_rapport_anchors_block(persona_id: str) -> str:
    """Build the PERSONA-SPECIFIC RAPPORT ANCHORS block for the IQR chain, or '' if absent."""
    if not persona_id:
        return ""
    try:
        config = _load_persona_config(persona_id)
    except Exception:
        return ""
    anchors = config.get("iqr_rapport_anchors")
    if not anchors:
        logger.warning("iqr_rapport_anchors missing from persona config for '%s' — rapport scoring will use generic rules only", persona_id)
        return ""
    lines = [
        "PERSONA-SPECIFIC RAPPORT ANCHORS (apply when scoring Framing & Stakeholder Fit):",
        f"  Summary: {anchors.get('summary', '')}",
        "  High-score behaviors (count these as evidence of rapport):",
    ]
    for b in anchors.get("high_score_behaviors", []):
        lines.append(f"    - {b}")
    lines.append("  Low-score behaviors (count these against rapport):")
    for b in anchors.get("low_score_behaviors", []):
        lines.append(f"    - {b}")
    lines.append("  DO NOT credit these as rapport:")
    for b in anchors.get("explicit_non_rewards", []):
        lines.append(f"    - {b}")
    return "\n".join(lines)


def _load_strong_interview_motifs(persona_id: str) -> list[str]:
    """Return the strong_interview_motifs.motifs list for the persona, or [] if absent."""
    if not persona_id:
        return []
    key_path = SIC_KEYS_DIR / f"{persona_id}_sic_key.json"
    if not key_path.is_file():
        return []
    try:
        data = json.loads(key_path.read_text(encoding="utf-8"))
        return list(data.get("strong_interview_motifs", {}).get("motifs", []))
    except Exception:
        return []


def _build_llm() -> BaseChatModel:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required for IQR scoring.")
    return ChatOpenAI(model="gpt-4o", temperature=0.0, api_key=api_key)


class IQRScorer:
    """
    Scores an interview transcript using the Interview Quality Rubric (IQR).

    Wraps a LangChain OpenAI chat model and the IQR system prompt to produce
    structured SessionEvaluation outputs from an input Transcript.
    """

    def __init__(self, prompt_path: Optional[str] = None) -> None:
        self._prompt_path = Path(prompt_path) if prompt_path else DEFAULT_PROMPT_PATH
        if not self._prompt_path.is_file():
            raise FileNotFoundError(f"IQR system prompt not found at: {self._prompt_path}")
        self._system_prompt = self._prompt_path.read_text(encoding="utf-8")
        self._llm = _build_llm()
        self._parser = PydanticOutputParser(pydantic_object=SessionEvaluation)
        self._chain = self._build_chain(self._llm)

    def _build_chain(self, llm: BaseChatModel):
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "{system_prompt}\n\n{persona_rapport_anchors}\n\n{motifs_context}\n\n{format_instructions}",
                ),
                (
                    "user",
                    (
                        "Evaluate the following interview transcript and return "
                        "a single JSON object matching the `SessionEvaluation` schema.\n\n"
                        "Transcript JSON:\n```json\n{transcript_json}\n```"
                    ),
                ),
            ]
        )
        return prompt | llm | self._parser

    async def evaluate(self, transcript: Transcript) -> SessionEvaluation:
        if not transcript.turns:
            base_metadata = dict(transcript.metadata or {})
            base_metadata["status"] = "Incomplete"
            return SessionEvaluation(
                metadata=base_metadata,
                dimensions=[],
                overall_score=1.0,
                skill_label="Incomplete",
                overall_summary="Transcript is empty; no IQR evaluation was performed.",
                depth_note="No depth could be assessed — transcript is empty.",
                earned_vs_volunteered_note="No insight was exchanged.",
                top_strip=TopStrip(
                    strength="N/A — no interview to evaluate.",
                    missed_opportunities=["Complete an interview to receive coaching feedback."],
                    next_move="Begin an interview and complete at least a few turns.",
                ),
            )

        base_metadata = dict(transcript.metadata or {})
        for key in ("session_id", "persona_id", "scenario_id"):
            base_metadata.setdefault(key, base_metadata.get(key))

        transcript_json = json.dumps(transcript.model_dump(), ensure_ascii=False, indent=2)

        persona_id: str = str(base_metadata.get("persona_key") or base_metadata.get("persona_id") or "")
        motifs = _load_strong_interview_motifs(persona_id)
        if motifs:
            motifs_context = (
                "STRONG INTERVIEW MOTIFS — use these in overall_summary when score ≥ 8.0:\n"
                + "\n".join(f"  - {m}" for m in motifs)
            )
        else:
            motifs_context = ""

        persona_rapport_anchors = _load_rapport_anchors_block(persona_id)

        chain_input = {
            "system_prompt": self._system_prompt,
            "persona_rapport_anchors": persona_rapport_anchors,
            "motifs_context": motifs_context,
            "format_instructions": self._parser.get_format_instructions(),
            "transcript_json": transcript_json,
        }

        try:
            result: SessionEvaluation = await self._chain.ainvoke(chain_input)
        except Exception:
            # Fallback: retry with gpt-4o-mini for reliability
            fallback_llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.0,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
            fallback_chain = self._build_chain(fallback_llm)
            result = await fallback_chain.ainvoke(chain_input)

        result.metadata = {**base_metadata, **result.metadata}
        return result


def convert_transcript_to_iqr(raw: dict, student_speaker: str = "Student") -> Transcript:
    """
    Convert a Phase 3 webapp transcript (role/text/timestamp format) into
    the IQR Transcript schema (turn_id, speaker, text).
    """
    from app.evaluation.iqr_schema import Turn

    turns_raw = raw.get("turns", [])
    persona_key = raw.get("persona_key", "")

    # Derive stakeholder display name from persona_key
    stakeholder_speaker = persona_key.replace("_", " ").title() if persona_key else "Stakeholder"

    role_map = {"user": student_speaker, "assistant": stakeholder_speaker}

    converted_turns = []
    for i, t in enumerate(turns_raw):
        role = str(t.get("role", "")).lower()
        speaker = role_map.get(role, role.title())
        converted_turns.append(
            Turn(
                turn_id=i + 1,
                speaker=speaker,
                text=str(t.get("text", "")).strip(),
            )
        )

    # Build metadata: merge top-level keys into metadata dict
    meta: dict = dict(raw.get("metadata") or {})
    for key in ("session_id", "persona_key", "started_at", "ended_at"):
        if key in raw and key not in meta:
            meta[key] = raw[key]

    return Transcript(metadata=meta, turns=converted_turns)
