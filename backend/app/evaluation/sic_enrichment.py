from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ItemLabel(BaseModel):
    chunk_id: str
    display_label: str = Field(description="2-3 word noun-phrase label for the knowledge item")


class TierConsequence(BaseModel):
    tier: int
    consequence_text: str = Field(
        description="1-2 sentence explanation of what the student's plan will miss or get wrong"
    )


class SICEnrichmentResult(BaseModel):
    item_labels: List[ItemLabel]
    tier_consequences: List[TierConsequence]


_ENRICHMENT_SYSTEM_PROMPT = """\
You are a feedback writer for a stakeholder interview simulation in climate adaptation planning.

You will receive a list of knowledge items grouped by tier, each with a fact_summary and whether the student accessed it.

Produce two things:

1. **display_label** for EVERY item (accessed or not): a short 2–3 word noun phrase derived from the fact_summary. \
These labels appear on small cards so they must be scannable. Examples:
   - "Climate risk baseline for Harbortown — Alex acknowledges increasing nuisance flooding..." → "Climate Baseline"
   - "Planning, engineering, and public works staff are stretched thin..." → "Staff Capacity"
   - "Alex carries moral stress around displacement and buyouts..." → "Displacement Stress"
   - "Even short flooding closures cause unrecoverable revenue losses..." → "Revenue Sensitivity"
   - "Visibility and perception matter as much as physical damage..." → "Perception Fragility"

2. **consequence_text** for each tier that has at least one missed item: a 1–2 sentence explanation \
of what the student's climate adaptation plan will specifically lack because they missed those items. \
Use second person ("Your plan…", "You won't account for…"). Be concrete — name the planning gap, \
not generic advice like "probe deeper." If a tier has NO missed items, omit it from tier_consequences.

Examples of good consequence_text:
- "Because you missed this threshold, your final climate adaptation plan will be blind to the economic survival of the downtown commercial corridor."
- "Your plan may propose timelines that look reasonable on paper but ignore how quickly a small business can go from disrupted to closed."
- "Missing this knowledge means your adaptation strategy won't account for the questions the planning system itself is deferring."
"""


async def enrich_sic_results(
    tier_coverages: List[dict],
    tier_metadata: Dict[str, dict],
) -> Optional[SICEnrichmentResult]:
    """Generate display labels and consequence text for SIC results.

    Returns None if the LLM call fails — callers should treat this as
    non-fatal and proceed with empty labels/consequences.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; skipping SIC enrichment")
        return None

    user_lines: list[str] = []
    for tc in tier_coverages:
        tier_num = tc["tier"]
        meta = tier_metadata.get(str(tier_num), {})
        user_lines.append(f"## Tier {tier_num}: {meta.get('title', '')} ({meta.get('category', '')})")
        for item in tc.get("items", []):
            status = "ACCESSED" if item.get("elicited") else "MISSED"
            user_lines.append(f"- [{status}] {item['chunk_id']}: {item.get('fact_summary', '')}")
        user_lines.append("")

    user_message = "\n".join(user_lines)

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.2,
            api_key=api_key,
        )
        structured_llm = llm.with_structured_output(SICEnrichmentResult)
        result = await structured_llm.ainvoke([
            {"role": "system", "content": _ENRICHMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ])
        return result
    except Exception:
        logger.exception("SIC enrichment LLM call failed; proceeding without labels/consequences")
        return None
