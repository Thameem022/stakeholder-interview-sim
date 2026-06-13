from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SIC_KEYS_DIR = Path(__file__).parent / "sic_keys"
DEFAULT_SIC_PROMPT_PATH = Path(__file__).parent / "prompts" / "sic_system_prompt.txt"


# ── Structured output schemas ────────────────────────────────────────────────

CreditMode = Literal[
    "explicit_acknowledgment",
    "indirect_acknowledgment",
    "reflective_silence",
    # legacy Tier 1/2: just "elicited" semantics, no Tier 3 modes
    "explicit",
]

OmissionClassification = Literal[
    "insufficient_framing",
    "appropriate_non_disclosure",
]

EarnedMode = Literal["earned", "volunteered", "not_present"]


class SICItemGrade(BaseModel):
    chunk_id: str = Field(description="The chunk_id from the SIC catalog")
    elicited: bool = Field(description="True if the student elicited this fact or signal in any credited form")
    earned_mode: EarnedMode = Field(
        default="not_present",
        description=(
            "'earned' = student's question/framing directly preceded the persona's mention; "
            "'volunteered' = persona mentioned the content unprompted or in response to a broad "
            "opener that did not target this item; 'not_present' = content not in transcript."
        ),
    )
    credit_mode: Optional[CreditMode] = Field(
        default=None,
        description=(
            "For Tier 3 'signal' items: one of 'explicit_acknowledgment', "
            "'indirect_acknowledgment', 'reflective_silence' (when surfaced). "
            "For Tier 1/2 'fact' items: 'explicit' when surfaced. None if not surfaced."
        ),
    )
    omission_classification: Optional[OmissionClassification] = Field(
        default=None,
        description=(
            "For Tier 3 items only, when elicited=false: one of "
            "'insufficient_framing' or 'appropriate_non_disclosure'. None for "
            "Tier 1/2 items, and None for surfaced items."
        ),
    )
    evidence_quote: str = Field(
        description=(
            "Verbatim quote from the student's transcript turns proving the framing or elicitation, "
            "or empty string if no relevant student behavior is present."
        ),
    )
    surfacing_cues_used: List[str] = Field(
        default_factory=list,
        description=(
            "Surfacing cues from the catalog that the student demonstrated (verbatim from "
            "the item's surfacing_cues list)."
        ),
    )


class SICGradingResult(BaseModel):
    grades: List[SICItemGrade] = Field(description="One grade entry per SIC catalog item")


# ── Status computation ───────────────────────────────────────────────────────

# Tier 3 percentage weighting per credit mode (extraction is not the goal —
# indirect acknowledgment and reflective-silence-with-good-framing both count).
_TIER3_CREDIT_WEIGHT = {
    "explicit_acknowledgment": 1.0,
    "indirect_acknowledgment": 0.7,
    "reflective_silence":      0.5,
}


def _compute_status_for_tier(
    tier_num: int,
    item_views: List[dict],
) -> Tuple[str, float, Optional[str]]:
    """
    Returns (status, percentage_0_to_100, skill_label).

    For Tier 1/2 ('fact' items): binary elicited or not; status is one of
    'full' / 'partial' / 'not_accessed_insufficient_framing' (we don't
    distinguish appropriate restraint for non-Tier-3 items).

    For Tier 3 ('signal' items): partial-credit weighting; if zero items
    surfaced, the status reflects the majority omission_classification.
    """
    total = len(item_views)
    if total == 0:
        return "not_accessed_insufficient_framing", 0.0, None

    if tier_num == 3:
        weighted = 0.0
        for v in item_views:
            if v.get("elicited"):
                mode = v.get("credit_mode") or "indirect_acknowledgment"
                weighted += _TIER3_CREDIT_WEIGHT.get(mode, 0.5)
        pct = round((weighted / total) * 100, 1)
        if weighted == 0:
            # Pick status by majority omission classification.
            classifications = [v.get("omission_classification") for v in item_views]
            appr = sum(1 for c in classifications if c == "appropriate_non_disclosure")
            insuf = sum(1 for c in classifications if c == "insufficient_framing")
            # If the grader didn't classify, default to insufficient_framing — we
            # would rather over-attribute developmental feedback than over-attribute
            # restraint praise to a student who didn't earn it.
            if appr > insuf:
                return "not_accessed_appropriate_restraint", 0.0, None
            return "not_accessed_insufficient_framing", 0.0, None
        if pct < 50:
            return "partial", pct, "Developing"
        if pct < 100:
            return "partial", pct, "Proficient"
        return "full", pct, None

    # Tier 1/2: binary count
    found = sum(1 for v in item_views if v.get("elicited"))
    pct = round((found / total) * 100, 1)
    if pct == 0:
        return "not_accessed_insufficient_framing", 0.0, None
    if pct < 50:
        return "partial", pct, "Developing"
    if pct < 100:
        return "partial", pct, "Proficient"
    return "full", pct, None


# ── SICScorer ────────────────────────────────────────────────────────────────

class SICScorer:
    """
    Grades an interview transcript against a persona's SIC key and returns
    TierCoverage payloads ready for the frontend InsightCoveragePanel.

    SIC key files live in sic_keys/{persona_id}_sic_key.json.
    Each file must contain a top-level 'sic_catalog' array and a
    'tier_metadata' object keyed by tier number as a string ("1", "2", "3").

    For personas with the v2 SIC schema (presence of top-level 'omission_policy'),
    Tier 3 items are graded with credit modes and omission classifications instead
    of binary elicited/not, and the omission_policy.do_not_reward guardrail blocks
    extractive suggested_follow_ups from becoming actionable tips.
    """

    def __init__(self, prompt_path: Optional[str] = None) -> None:
        self._prompt_path = Path(prompt_path) if prompt_path else DEFAULT_SIC_PROMPT_PATH
        if not self._prompt_path.is_file():
            raise FileNotFoundError(f"SIC system prompt not found at: {self._prompt_path}")
        self._system_prompt = self._prompt_path.read_text(encoding="utf-8")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is required for SIC scoring.")
        self._llm = ChatOpenAI(model="gpt-4o", temperature=0.0, api_key=api_key)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _load_sic_key(self, persona_id: str) -> dict:
        path = SIC_KEYS_DIR / f"{persona_id}_sic_key.json"
        if not path.is_file():
            # Fail loudly. We do NOT silently fall back to another persona's key —
            # that would produce confidently-wrong coverage scores against the
            # wrong rubric. Surface the missing-file error to the caller so it
            # can be reported in the UI / logs and fixed.
            raise FileNotFoundError(
                f"No SIC key found for persona '{persona_id}'. "
                f"Expected file: {path}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        # Sanity check: the persona_id inside the file should match the filename.
        embedded_id = data.get("persona_id")
        if embedded_id and embedded_id != persona_id:
            logger.warning(
                "SIC key persona_id mismatch: file '%s' contains persona_id=%r "
                "but was loaded for persona_id=%r. Scoring will continue using "
                "the file's contents — fix the file so the two agree.",
                path.name, embedded_id, persona_id,
            )
        return data

    def _build_rubric_text(
        self,
        catalog: List[dict],
        omission_policy: Optional[dict],
        persona_name: Optional[str] = None,
        persona_archetype: Optional[str] = None,
        grading_rules: Optional[dict] = None,
    ) -> str:
        lines: List[str] = []
        # Persona header — the system prompt is persona-agnostic, so the
        # grader needs the persona's name + archetype injected here. Different
        # personas surface ethical/strategic signals differently; the archetype
        # is a strong prior on what kinds of disclosure look "in character."
        if persona_name or persona_archetype:
            header = ["PERSONA UNDER EVALUATION:"]
            if persona_name:
                header.append(f"  name: {persona_name}")
            if persona_archetype:
                header.append(f"  archetype: {persona_archetype}")
            lines.append("\n".join(header))
            lines.append("")
        lines.append("SIC CATALOG — grade every item below:")
        for item in catalog:
            tier = item.get("tier")
            item_type = item.get("type", "fact")
            block = [
                f"\n  chunk_id: {item['chunk_id']}",
                f"  tier: {tier}",
                f"  type: {item_type}",
                f"  evaluation_criteria: {item['evaluation_criteria']}",
            ]
            if item.get("surfacing_cues"):
                block.append("  surfacing_cues (interviewer behaviors that would surface this):")
                for cue in item["surfacing_cues"]:
                    block.append(f"    - {cue}")
            if item.get("credit_modes"):
                block.append("  credit_modes (Tier 3 — pick ONE if elicited):")
                for mode_name, mode_desc in item["credit_modes"].items():
                    block.append(f"    - {mode_name}: {mode_desc}")
            if item.get("omission_interpretation"):
                block.append("  omission_interpretation (Tier 3 — pick ONE if NOT elicited):")
                for cls_name, cls_desc in item["omission_interpretation"].items():
                    block.append(f"    - {cls_name}: {cls_desc}")
            lines.append("\n".join(block))
        if omission_policy:
            persona_label = f" — {persona_name}" if persona_name else ""
            policy_block = [
                "",
                f"OMISSION POLICY (HARD GUARDRAIL{persona_label}):",
                "  The system prompt instructs you to defer to this persona-specific",
                "  policy as the operative guardrail. Apply these lists exactly.",
            ]
            if omission_policy.get("tier_3_not_accessed_is_not_failure"):
                policy_block.append(
                    "  - Tier 3 items NOT surfaced are NOT a student failure by default. "
                    "Classify the omission accurately."
                )
            if omission_policy.get("do_not_reward"):
                policy_block.append(
                    "  - do_not_reward (behaviors that MUST NOT be credited as elicitation): "
                    + ", ".join(omission_policy["do_not_reward"])
                )
            if omission_policy.get("do_reward"):
                policy_block.append(
                    "  - do_reward (behaviors that SHOULD be credited when the persona responds in kind): "
                    + ", ".join(omission_policy["do_reward"])
                )
            lines.append("\n".join(policy_block))
        if grading_rules and grading_rules.get("tier_2_requires_gate"):
            lines.append(
                "\n".join([
                    "",
                    "PER-PERSONA GRADING RULE — Tier 2 gate:",
                    "  This persona requires Tier 2 items to also pass a framing gate. For each",
                    "  Tier 2 fact, the student must have asked a targeted question naming the",
                    "  topic (e.g., \"how do neighbors handle flooding before calling the city\")",
                    "  before the persona's mention. Tier 2 content volunteered in response to a",
                    "  broad opener (e.g., \"tell me about your neighborhood\") does NOT count.",
                    "  Apply the same gate logic as Tier 3 (a/b/c/d), but item-specific cues are",
                    "  sufficient — the student does not need to name an underlying tension for",
                    "  Tier 2.",
                ])
            )
        return "\n".join(lines)

    def _format_transcript(self, turns: List[dict]) -> str:
        lines = []
        for t in turns:
            speaker = t.get("speaker") or t.get("role", "Unknown")
            text = (t.get("text") or "").strip()
            if text:
                lines.append(f"[{speaker}]: {text}")
        return "\n".join(lines)

    def _build_chain(self, llm):
        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            (
                "user",
                "Grade the following interview transcript against the SIC catalog.\n\n"
                "TRANSCRIPT:\n```\n{transcript}\n```\n\n"
                "{rubric}\n\n"
                "Return a JSON object with a 'grades' array. Each entry must have:\n"
                "  - chunk_id   (string — must match exactly)\n"
                "  - elicited   (boolean)\n"
                "  - earned_mode (string — required for every item):\n"
                "      'earned'      — student's question or framing directly preceded the persona's mention\n"
                "                      (elicited=true requires earned_mode='earned' by definition)\n"
                "      'volunteered' — the content appears in the transcript but the persona raised it\n"
                "                      unprompted or in response to a broad opener that did not target\n"
                "                      this item; elicited must be false in this case\n"
                "      'not_present' — the content does not appear in the transcript at all\n"
                "  - credit_mode (string or null — see schema)\n"
                "  - omission_classification (string or null — Tier 3 only when elicited=false)\n"
                "  - evidence_quote (string — verbatim from student turns only; empty if no relevant student behavior)\n"
                "  - surfacing_cues_used (array of strings — verbatim entries from the item's surfacing_cues list that the student demonstrated)\n\n"
                "You MUST include one entry for every chunk_id listed above. No omissions.",
            ),
        ])
        return prompt | llm.with_structured_output(SICGradingResult)

    # ── Public API ────────────────────────────────────────────────────────────

    async def evaluate(self, persona_id: str, turns: List[dict]) -> List[dict]:
        """
        Grade the transcript turns against the SIC key for persona_id.

        Returns a list of TierCoverage dicts (sorted Tier 1 → N) ready for
        the frontend. Raises FileNotFoundError if no SIC key exists for
        persona_id.
        """
        sic_key = self._load_sic_key(persona_id)
        catalog: List[dict] = sic_key.get("sic_catalog", [])
        tier_metadata: dict = sic_key.get("tier_metadata", {})
        omission_policy: Optional[dict] = sic_key.get("omission_policy")
        grading_rules: Optional[dict] = sic_key.get("grading_rules")
        do_not_reward = set((omission_policy or {}).get("do_not_reward", []))
        persona_name: Optional[str] = sic_key.get("persona_name")
        persona_archetype: Optional[str] = sic_key.get("archetype")

        if not catalog:
            return []

        rubric_text = self._build_rubric_text(
            catalog,
            omission_policy,
            persona_name=persona_name,
            persona_archetype=persona_archetype,
            grading_rules=grading_rules,
        )
        transcript_text = self._format_transcript(turns)

        chain = self._build_chain(self._llm)
        chain_input = {
            "system_prompt": self._system_prompt,
            "transcript": transcript_text,
            "rubric": rubric_text,
        }

        try:
            result: SICGradingResult = await chain.ainvoke(chain_input)
        except Exception:
            # Fallback to gpt-4o-mini if primary call fails
            fallback_llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.0,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
            fallback_chain = self._build_chain(fallback_llm)
            result = await fallback_chain.ainvoke(chain_input)

        grades_by_id: Dict[str, SICItemGrade] = {g.chunk_id: g for g in result.grades}

        # ── Group catalog items by tier ──────────────────────────────────────
        tiers_raw: Dict[int, List[dict]] = {}
        for item in catalog:
            tier_num = int(item.get("tier", 0))
            tiers_raw.setdefault(tier_num, []).append(item)

        # ── Build one TierCoverage dict per tier ─────────────────────────────
        tier_coverages = []
        for tier_num in sorted(tiers_raw.keys()):
            items = tiers_raw[tier_num]
            meta = tier_metadata.get(str(tier_num), {})

            # Per-item view: pair each catalog entry with its grade so the
            # frontend can render the heatmap and per-item details.
            item_views: List[dict] = []
            for item in items:
                grade = grades_by_id.get(
                    item["chunk_id"],
                    SICItemGrade(
                        chunk_id=item["chunk_id"],
                        elicited=False,
                        credit_mode=None,
                        omission_classification=None,
                        evidence_quote="",
                        surfacing_cues_used=[],
                    ),
                )
                catalog_cues: List[str] = list(item.get("surfacing_cues") or [])
                cues_used: List[str] = [c for c in grade.surfacing_cues_used if c in catalog_cues]
                cues_missing: List[str] = [c for c in catalog_cues if c not in cues_used]

                # Resolve credit_mode: for tier 1/2 facts, normalize to "explicit"
                # when elicited; for tier 3 signals leave as-is from grader.
                item_type = item.get("type", "fact")
                credit_mode: Optional[str] = grade.credit_mode
                if grade.elicited and item_type != "signal" and credit_mode is None:
                    credit_mode = "explicit"
                if not grade.elicited:
                    credit_mode = None

                omission_cls: Optional[str] = grade.omission_classification
                # Only signals carry omission classifications; clear for facts.
                if item_type != "signal" or grade.elicited:
                    omission_cls = None

                item_views.append({
                    "chunk_id": item["chunk_id"],
                    "domain": item.get("domain", ""),
                    "type": item_type,
                    "fact_summary": item.get("fact_summary", ""),
                    "suggested_follow_up": item.get("suggested_follow_up", ""),
                    "elicited": grade.elicited,
                    "earned_mode": grade.earned_mode,
                    "credit_mode": credit_mode,
                    "omission_classification": omission_cls,
                    "evidence_quote": grade.evidence_quote if grade.elicited else "",
                    "surfacing_cues_used": cues_used,
                    "surfacing_cues_missing": cues_missing,
                })

            status, pct, skill_label = _compute_status_for_tier(tier_num, item_views)
            found = sum(1 for v in item_views if v["elicited"])
            total = len(item_views)

            # Choose an actionable_tip ONLY from items whose suggested_follow_up
            # does not violate the omission_policy. For Tier 3, we additionally
            # prefer items classified as 'insufficient_framing' over
            # 'appropriate_non_disclosure' — the latter is in-character restraint
            # and does not warrant a "try this next time" tip.
            actionable_tip: Optional[str] = None
            for v in item_views:
                if v["elicited"]:
                    continue
                if v["type"] == "signal" and v.get("omission_classification") == "appropriate_non_disclosure":
                    # Don't push the student to extract Tier 3 content that was
                    # legitimately withheld in response to good framing.
                    continue
                sf = v.get("suggested_follow_up") or ""
                if not sf:
                    continue
                # Guardrail: skip any suggested_follow_up that smells like a
                # do_not_reward behavior (extractive language).
                lower_sf = sf.lower()
                guard_phrases = [
                    "which neighborhoods", "when will", "name the areas",
                    "be abandoned", "won't be protected", "cannot be protected",
                    "internal timeline", "point of no return", "tell me your honest",
                ]
                if any(p in lower_sf for p in guard_phrases):
                    continue
                actionable_tip = sf
                break

            quick_win: Optional[str] = None
            if status == "full":
                quick_win = "You captured this tier well!"
            elif status == "not_accessed_appropriate_restraint":
                quick_win = "Mature framing — you respected Alex's institutional restraint"

            coverage: dict = {
                "tier": tier_num,
                "title": meta.get("title", f"Tier {tier_num}"),
                "category": meta.get("category", ""),
                "description": meta.get("description", ""),
                "status": status,
                "percentage": pct,
                "cues_found": found,
                "cues_total": total,
                "why_it_matters": meta.get("why_it_matters"),
                "quick_win": quick_win,
                "actionable_tip": actionable_tip,
                "items": item_views,
            }
            if skill_label:
                coverage["skill_label"] = skill_label

            tier_coverages.append(coverage)

        return tier_coverages
