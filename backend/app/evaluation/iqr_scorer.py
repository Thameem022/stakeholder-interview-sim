from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.evaluation.iqr_schema import SessionEvaluation, Transcript

IQR_RUBRIC_METADATA: Dict[str, Any] = {
    "iqr_score_scale": "10-point",
    "iqr_score_min": 1.0,
    "iqr_score_max": 10.0,
    "iqr_skill_bands": (
        "6.0–6.9 Novice Fact-Finder; 7.0–7.9 Emerging Technical Interviewer; "
        "8.0–8.9 Competent Operational Interviewer; 9.0–9.9 Advanced Systems "
        "Interviewer; 10.0 Master Stakeholder Partner"
    ),
}

IQR_SKILL_SCALE_CONTEXT = """\
**10-point mapping (apply per dimension):**
- Set `score` to a float in [1.0, 10.0] (half steps allowed, e.g. 6.5).
- Set `skill_level_title` to the **exact quoted title** from Phase 1 whose band contains that score (e.g. 7.4 → "Emerging Technical Interviewer").
- For scores **below 6.0**, choose the closest Phase 1 title by meaning and say so in `rationale`, or use a concise developmental label that fits the band (e.g. approaching "Novice Fact-Finder").
- **Closed-turn rule:** If Phase 2 identifies a "Closed Turn" for evidence tied to a dimension, you **must** populate `evidence.alternative_phrasing` (the Bridge) and `line_of_inquiry_impact` (the lost line of inquiry). If there is no closed turn for that dimension's evidence, set both fields to `null`.
"""

# Default prompt path — evaluator system prompts
DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "iqr_system_prompt.txt"


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
                    "{system_prompt}\n\n{rubric_context}\n\n{format_instructions}",
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
            base_metadata = {**dict(transcript.metadata or {}), **IQR_RUBRIC_METADATA}
            base_metadata["status"] = "Incomplete"
            return SessionEvaluation(
                metadata=base_metadata,
                evaluation_results=[],
                overall_summary="Transcript is empty; no IQR evaluation was performed.",
            )

        base_metadata = dict(transcript.metadata or {})
        for key in ("session_id", "persona_id", "scenario_id"):
            base_metadata.setdefault(key, base_metadata.get(key))

        transcript_json = json.dumps(transcript.model_dump(), ensure_ascii=False, indent=2)

        chain_input = {
            "system_prompt": self._system_prompt,
            "rubric_context": IQR_SKILL_SCALE_CONTEXT,
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

        result.metadata = {
            **base_metadata,
            **result.metadata,
            **IQR_RUBRIC_METADATA,
        }
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
