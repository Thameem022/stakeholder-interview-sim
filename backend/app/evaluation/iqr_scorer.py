from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.evaluation.iqr_schema import SessionEvaluation, Transcript
from app.personas.prompt_assembly import _load_persona_config

# Default prompt path — evaluator system prompts (versioned; bump version here to upgrade)
DEFAULT_PROMPT_PATH = Path(__file__).parent / "prompts" / "iqr" / "v3" / "system_prompt.txt"
SIC_KEYS_DIR = Path(__file__).parent / "sic_keys"

# The overall score is a fixed formula in code, not rubric guidance the judge
# applies by eye. Probing and Framing carry more than Question Quality, which is
# what the rubric always said; stating it as numbers is what makes the score
# reproducible. Bump IQR_WEIGHTS_VERSION whenever these change, so a stored
# evaluation row traces back to the formula that produced it.
IQR_WEIGHTS_VERSION = "1.0"
IQR_DIMENSION_WEIGHTS: dict[str, float] = {
    "framing_and_stakeholder_fit":              0.30,
    "question_quality_and_precision":           0.20,
    "probing_and_follow_up_depth":              0.30,
    "listening_interpretation_and_stewardship": 0.20,
}


def compute_overall_score(dimensions) -> Optional[float]:
    """Weighted mean of the four dimension scores, or None if none are present.

    Renormalises over the dimensions actually returned, so a malformed response
    missing one dimension yields a score on the same 1-10 scale rather than a
    silently depressed one.
    """
    total_weight = 0.0
    weighted = 0.0
    for d in dimensions:
        weight = IQR_DIMENSION_WEIGHTS.get(d.dimension)
        if weight is None:
            continue
        weighted += weight * d.score
        total_weight += weight
    if total_weight == 0.0:
        return None
    return round(weighted / total_weight, 1)


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


def _load_strong_interview_motifs(sic_key: dict) -> list[str]:
    """Return the strong_interview_motifs.motifs list from a loaded SIC key."""
    return list((sic_key.get("strong_interview_motifs") or {}).get("motifs", []))


def _read_sic_key(persona_id: str) -> dict:
    """Load a persona's SIC key, or {} when there isn't one.

    The IQR scorer reads three things out of this file — motifs, catalogue
    labels and the do-not-reward list — so it reads it once and tolerates its
    absence: a missing key must degrade the prompt, never fail the scoring call.
    """
    if not persona_id:
        return {}
    key_path = SIC_KEYS_DIR / f"{persona_id}_sic_key.json"
    if not key_path.is_file():
        return {}
    try:
        return json.loads(key_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("SIC key for '%s' could not be parsed; IQR prompt will omit catalogue context", persona_id)
        return {}


def _build_persona_header(sic_key: dict) -> str:
    """Who the student was talking to, in the judge's own words.

    Without the pronouns the judge infers gender from the first name and gets
    ambiguous ones wrong — a report that misgenders the persona a student just
    spent twenty minutes with reads as though nobody was paying attention.
    """
    name = sic_key.get("persona_name")
    if not name:
        return ""
    lines = [f"PERSONA INTERVIEWED: {name}"]
    if sic_key.get("role_title"):
        lines.append(f"  role: {sic_key['role_title']}")
    if sic_key.get("pronouns"):
        lines.append(f"  pronouns: {sic_key['pronouns']} — use these, do not infer from the name")
    return "\n".join(lines)


def _build_catalogue_block(sic_key: dict) -> str:
    """The only knowledge-item names a moment may cite.

    IQR and SIC are scored in parallel, so this side cannot know what the
    coverage grader actually scored. Constraining the model to exact labels is
    what lets the serving layer resolve each one against the real grade instead
    of trusting prose.
    """
    labels = [
        item["display_label"]
        for item in sic_key.get("sic_catalog", [])
        if item.get("display_label")
    ]
    if not labels:
        return ""
    return "\n".join([
        "CATALOGUE ITEMS — the only knowledge-item names you may put in `out_of_reach_item`:",
        *(f"  - {label}" for label in labels),
        "Copy a label exactly, or use null. Anything not on this list is dropped.",
    ])


def _build_do_not_recommend_block(sic_key: dict) -> str:
    """Behaviours this persona is built to shut down.

    Standing in for the technique library's per-persona fit ratings until the
    library is authored: without it, a model-written technique is free to
    recommend the exact move that closes this stakeholder up.
    """
    behaviors = (sic_key.get("omission_policy") or {}).get("do_not_reward") or []
    if not behaviors:
        return ""
    return "\n".join([
        "DO NOT RECOMMEND (this persona is designed to shut these down):",
        *(f"  - {b.replace('_', ' ')}" for b in behaviors),
        "A technique_name or technique_stem that asks the student to do any of the",
        "above is wrong for this persona, however reasonable it sounds in general.",
    ])


def _build_llm(model: str = "gpt-4o", temperature: float = 0.0) -> BaseChatModel:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required for IQR scoring.")
    return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)


class IQRScorer:
    """
    Scores an interview transcript using the Interview Quality Rubric (IQR).

    Wraps a LangChain OpenAI chat model and the IQR system prompt to produce
    structured SessionEvaluation outputs from an input Transcript.
    """

    def __init__(
        self,
        prompt_path: Optional[str] = None,
        model: str = "gpt-4o",
        fallback_model: str = "gpt-4o-mini",
        allow_fallback: bool = True,
        temperature: float = 0.0,
    ) -> None:
        self._prompt_path = Path(prompt_path) if prompt_path else DEFAULT_PROMPT_PATH
        if not self._prompt_path.is_file():
            raise FileNotFoundError(f"IQR system prompt not found at: {self._prompt_path}")
        self._system_prompt = self._prompt_path.read_text(encoding="utf-8")
        self._model = model
        self._fallback_model = fallback_model
        # Evaluation runs set this False: a silent downgrade to the fallback model
        # would otherwise be recorded as a measurement of the primary one.
        self._allow_fallback = allow_fallback
        self._temperature = temperature
        self._llm = _build_llm(model, temperature)
        self._parser = PydanticOutputParser(pydantic_object=SessionEvaluation)
        self._chain = self._build_chain(self._llm)
        # Set by evaluate(); None until the first call.
        self.last_model_used: Optional[str] = None
        self.last_error: Optional[str] = None

    @property
    def prompt_version(self) -> str:
        """Version directory the active system prompt was loaded from, e.g. "v2"."""
        return self._prompt_path.parent.name

    def _build_chain(self, llm: BaseChatModel):
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "{system_prompt}\n\n{persona_header}\n\n{persona_rapport_anchors}\n\n{motifs_context}\n\n"
                    "{catalogue_items}\n\n{do_not_recommend}\n\n{format_instructions}",
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

    async def evaluate(self, transcript: Transcript, config: Optional[dict] = None) -> SessionEvaluation:
        # Reset per call so an inspecting caller never reads a previous run's values.
        self.last_model_used = None
        self.last_error = None

        if not transcript.turns:
            base_metadata = dict(transcript.metadata or {})
            base_metadata["status"] = "Incomplete"
            return SessionEvaluation(
                metadata=base_metadata,
                dimensions=[],
                overall_score=1.0,
                skill_label="Incomplete",
                moments=[],
            )

        base_metadata = dict(transcript.metadata or {})
        for key in ("session_id", "persona_id", "scenario_id"):
            base_metadata.setdefault(key, base_metadata.get(key))

        transcript_json = json.dumps(transcript.model_dump(), ensure_ascii=False, indent=2)

        persona_id: str = str(base_metadata.get("persona_key") or base_metadata.get("persona_id") or "")
        sic_key = _read_sic_key(persona_id)
        motifs = _load_strong_interview_motifs(sic_key)
        if motifs:
            motifs_context = (
                "STRONG INTERVIEW MOTIFS — name these specifically when the student"
                " reached them, rather than defaulting to generic 'probe deeper on"
                " ethics' language:\n"
                + "\n".join(f"  - {m}" for m in motifs)
            )
        else:
            motifs_context = ""

        persona_rapport_anchors = _load_rapport_anchors_block(persona_id)

        chain_input = {
            "system_prompt": self._system_prompt,
            "persona_header": _build_persona_header(sic_key),
            "persona_rapport_anchors": persona_rapport_anchors,
            "motifs_context": motifs_context,
            "catalogue_items": _build_catalogue_block(sic_key),
            "do_not_recommend": _build_do_not_recommend_block(sic_key),
            "format_instructions": self._parser.get_format_instructions(),
            "transcript_json": transcript_json,
        }

        self.last_model_used = self._model
        try:
            result: SessionEvaluation = await self._chain.ainvoke(chain_input, config=config)
        except Exception as e:
            # Record what went wrong before deciding whether to retry — an
            # unrecorded downgrade makes the run unattributable afterwards.
            self.last_error = f"{type(e).__name__}: {e}"
            if not self._allow_fallback:
                raise
            fallback_chain = self._build_chain(_build_llm(self._fallback_model, self._temperature))
            result = await fallback_chain.ainvoke(chain_input, config=config)
            self.last_model_used = self._fallback_model

        # The judge's own overall_score is advisory; the formula in code is the
        # number the student sees and the number the eval harness measures.
        weighted = compute_overall_score(result.dimensions)
        if weighted is not None:
            result.overall_score = weighted

        result.metadata = {
            **base_metadata,
            **result.metadata,
            "judge_model": self.last_model_used,
            "prompt_version": self.prompt_version,
            "iqr_weights_version": IQR_WEIGHTS_VERSION,
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
