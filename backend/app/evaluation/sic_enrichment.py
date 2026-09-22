from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TierConsequence(BaseModel):
    tier: int
    consequence_text: str = Field(
        description="1-3 sentences naming what the student's plan will miss, item by item"
    )


class SICEnrichmentResult(BaseModel):
    tier_consequences: List[TierConsequence]


_ENRICHMENT_SYSTEM_PROMPT = """\
You are a feedback writer for a stakeholder interview simulation in climate adaptation planning.

You will receive a list of knowledge items grouped by tier. Each item has a short label, \
a summary of what it covers, and whether the student opened it.

For each tier that has at least one item the student did NOT open, write a `consequence_text`: \
what this student's climate adaptation plan will specifically lack because those items stayed closed.

Rules:

1. ACCOUNT FOR EVERY MISSED ITEM IN THE TIER. If four items were missed, the sentence has to carry \
all four — name what each one was, compactly, in one flowing sentence or two. Naming one and \
implying the rest is the specific failure this field exists to fix. If exactly one was missed, \
write about that one.

2. Use second person: "Your plan…", "You won't account for…". Never "the student".

3. Be concrete. Name the planning gap, not generic advice. Never write "probe deeper", \
"ask more follow-up questions", or any other coaching instruction — this field says what is \
missing from the plan, not what the student should have done.

4. Plain language a first-year student understands. Do NOT use the phrases "framing gap", \
"leading question", "open-ended", "rapport-building", "extractive" or "reflective silence". \
"FRAMING GAP" in particular must never appear.

5. If a tier has NO missed items, omit it from tier_consequences entirely.

Examples of the right shape:

- "Four things stayed closed here, and together they are the reality your plan has to survive: how \
thin staff capacity already is, how election cycles shape what Council will back, how far planning \
horizons lag behind the risk timeline, and which current measures are only buying time. Without \
them, recommendations can look sound on paper and stall in practice."

- "You didn't get to how adaptation decisions actually get approved and funded, so your plan may \
recommend things that can't move through the town's process or attract the grants it depends on."

- "You reached most of what Alex holds here, but not his worry that visible investment can signal a \
permanence the assumptions don't support. Without it, your plan may lean on measures that reassure \
the public while deferring the harder decision."
"""


def _item_state(item: dict) -> str:
    """Earned / Opened / Not opened — the three states the student sees.

    Mirrors the frontend's state model exactly. A signal the persona withheld in
    response to good framing counts as Opened, not a miss: the student's move
    worked, and the writer must not describe it as a gap.
    """
    if item.get("elicited") and item.get("earned_mode") == "earned":
        if item.get("credit_mode") in ("explicit", "explicit_acknowledgment"):
            return "EARNED"
        return "OPENED"
    if (
        not item.get("elicited")
        and item.get("type") == "signal"
        and item.get("omission_classification") == "appropriate_non_disclosure"
    ):
        return "OPENED"
    return "NOT OPENED"


async def enrich_sic_results(
    tier_coverages: List[dict],
    tier_metadata: Dict[str, dict],
) -> Optional[SICEnrichmentResult]:
    """Generate the per-tier "why it matters" consequence line.

    Display labels are authored in the SIC keys and are NOT produced here — this
    pass exists only for the one sentence that depends on which items this
    particular student missed.

    Returns None if the LLM call fails — callers should treat this as
    non-fatal and fall back to tier_metadata.why_it_matters.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; skipping SIC enrichment")
        return None

    user_lines: list[str] = []
    for tc in tier_coverages:
        tier_num = tc["tier"]
        meta = tier_metadata.get(str(tier_num), {})
        missed = [i for i in tc.get("items", []) if _item_state(i) == "NOT OPENED"]
        user_lines.append(
            f"## Tier {tier_num}: {meta.get('title', '')} "
            f"— {len(missed)} item(s) not opened"
        )
        for item in tc.get("items", []):
            label = item.get("display_label") or item["chunk_id"]
            user_lines.append(
                f"- [{_item_state(item)}] {label}: {item.get('fact_summary', '')}"
            )
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
        logger.exception("SIC enrichment LLM call failed; proceeding without consequence text")
        return None
