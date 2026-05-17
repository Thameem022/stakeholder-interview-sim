# SIC key contract (Stakeholder Information Coverage)

This doc defines the schema and semantics of the Strategic Information Capture (SIC) key files that live in `sic_keys/<persona_id>_sic_key.json`. The schema was rewritten for Alex Martinez (Municipal Planner) per stakeholder review; this document is the spec for propagating the same shape to the other personas.

## Why this exists

The original SIC key was a flat `sic_catalog` of facts per tier — read by the scorer as "facts the student should extract." That framing is wrong for sensitive ethical/strategic content. Persona dossiers (e.g., `Persona 1_Municipal Planner Dossier.docx`, §5.3, §5.5, §6, §8.7, §9.6–9.7) make clear that Tier 3 knowledge is:

- **Rarely articulated directly** — it surfaces as hesitation, qualification, indirect framing, or reflective silence, not as named facts.
- **Conditionally disclosed** — emergence depends on the interviewer's framing, not the persona's willingness.
- **Legitimately withholdable** — institutional restraint is in-character professional behavior, not failure. A student who frames well and still doesn't get an explicit disclosure has not failed.

The schema below encodes that.

## Top-level shape

```jsonc
{
  "persona_id": "alex_martinez",
  "persona_name": "Alex Martinez",
  "schema_version": "2.0",

  "omission_policy": { /* policy block — see below */ },

  "tier_metadata": {
    "1": { "title": "...", "category": "...", "description": "...", "why_it_matters": "..." },
    "2": { /* ... */ },
    "3": { /* ... */ }
  },

  "sic_catalog": [
    { /* per-item entries — see below */ }
  ]
}
```

### `omission_policy`

Read by the SIC scorer (`evaluator prompts/sic_system_prompt.txt`) and the frontend status renderer.

```jsonc
{
  "tier_3_not_accessed_is_not_failure": true,
  "valid_interpretations": ["insufficient_framing", "appropriate_non_disclosure"],
  "do_not_reward": ["assertiveness", "policy_advocacy", "solution_first_thinking",
                    "moral_certainty", "pressing_for_positions",
                    "extracting_locations_or_timelines"],
  "do_reward":     ["patience", "interpretation", "ethical_restraint",
                    "respect_for_ambiguity", "hypothetical_framing",
                    "reflective_listening", "paraphrasing_tension"]
}
```

The scorer treats `do_not_reward` as a hard guardrail: `actionable_tip` MUST NOT push the student toward extracting sensitive Tier 3 content. `sic_scorer.py` enforces this by skipping any `suggested_follow_up` that contains extractive language patterns AND by suppressing tips altogether for items classified as `appropriate_non_disclosure`.

### Catalog items — three flavors

**Tier 1 — `fact` items** (low threshold, public knowledge):

```jsonc
{
  "chunk_id": "alex_m_t1_studies",
  "domain": "technical",
  "tier": 1,
  "type": "fact",
  "fact_summary": "What the student should elicit, in plain language",
  "evaluation_criteria": "What student behavior counts as elicitation",
  "suggested_follow_up": "What to ask if they missed it"
}
```

**Tier 2 — `fact` items with `surfacing_cues`** (contextual, conditionally disclosed):

```jsonc
{
  "chunk_id": "alex_m_t2_capacity",
  "domain": "contextual",
  "tier": 2,
  "type": "fact",
  "fact_summary": "...",
  "evaluation_criteria": "...",
  "surfacing_cues": [
    "Student asks how decisions move, not what the city 'plans' to do",
    "..."
  ],
  "suggested_follow_up": "..."
}
```

**Tier 3 — `signal` items** (the substantive change):

```jsonc
{
  "chunk_id": "alex_m_t3_false_security",
  "domain": "ethical",
  "tier": 3,
  "type": "signal",
  "fact_summary": "A description of the SIGNAL — what Alex carries internally and how it shows up indirectly. NOT an extractable fact.",
  "evaluation_criteria": "Student created the conditions for this signal to surface (not 'student extracted X')",
  "indicator_behaviors": [
    "How Alex expresses this signal — hesitation, qualification, slower speech, etc."
  ],
  "surfacing_cues": [
    "What the interviewer must do for this signal to be possible"
  ],
  "credit_modes": {
    "explicit_acknowledgment": "Alex names the concern directly under reflective or hypothetical framing",
    "indirect_acknowledgment": "Alex hedges, qualifies, or references the underlying tension",
    "reflective_silence":      "Alex pauses or redirects — credit the student's framing, not extraction"
  },
  "omission_interpretation": {
    "insufficient_framing":      "Student never created conditions for disclosure",
    "appropriate_non_disclosure": "Student framed well; persona's restraint is in-character — NOT a student failure"
  },
  "suggested_follow_up": "A framing-modeling question (NOT an extractive probe). Used only when omission is 'insufficient_framing'."
}
```

## Scorer output shape (`sic_scorer.py`)

The scorer emits a `TierCoverage[]` (attached to `SessionEvaluation.insight_coverage`):

- `status`: one of `'full' | 'partial' | 'not_accessed_insufficient_framing' | 'not_accessed_appropriate_restraint'`. (Legacy `'not_accessed'` is still accepted by the frontend for backward compatibility.)
- `percentage`: For Tier 3, weighted by `credit_mode` (explicit = 1.0, indirect = 0.7, reflective_silence = 0.5). For Tier 1/2, simple ratio.
- `items[]`: each entry includes `chunk_id`, `type`, `elicited`, `credit_mode`, `omission_classification`, `evidence_quote`, `surfacing_cues_used`, `surfacing_cues_missing`.

## Frontend rendering rules (`ScoreReport.tsx`)

- Tier 1/2 cards keep the "N of M cues found" quantitative display.
- Tier 3 cards never show "0 / N" — that framing implies the student missed a quota. Instead they show qualitative status:
  - `not_accessed_appropriate_restraint` renders in soft slate with copy "In-character professional restraint — not a missed quota."
  - `not_accessed_insufficient_framing` renders in muted gray with "Framing didn't yet make space for these signals."
- Cue Heatmap squares use a 5-color palette: green (explicit), amber (indirect), slate (reflective silence), slate dashed (appropriate restraint), red dashed (framing gap).
- The detail panel for any item shows `surfacing_cues_used` ("What you did") and — for signal items missed via insufficient framing — `surfacing_cues_missing` ("What would have surfaced this") instead of the legacy `suggested_follow_up`.

## Backwards compatibility

- Personas without an `omission_policy` block fall through to the legacy grading path: Tier 3 is graded binarily, statuses default to `'partial'` / `'not_accessed_insufficient_framing'`, and no `credit_mode` / `omission_classification` fields are populated.
- The frontend's `TierCoverage.status` union still accepts the legacy `'not_accessed'` value and renders it identically to `not_accessed_insufficient_framing`.
- Catalog items without `type` are treated as `'fact'`.

## Migration checklist (per persona)

When extending this pattern to Sarah Donnelly, Michael Alvarez, or Thomas Caldwell:

1. Read the persona dossier sections analogous to §5, §6, §9 of Persona 1.
2. Add `schema_version: "2.0"` and an `omission_policy` block. `do_not_reward` / `do_reward` should reflect that persona's specific ethical guardrails (e.g., for a community resident, do NOT reward technocratic reframing of lived experience).
3. For each catalog item, add `type` (`fact` or `signal`).
4. For Tier 2 `fact` items, add `surfacing_cues`.
5. For Tier 3 items, change `type` to `signal`, restate `fact_summary` as a description of how the signal manifests (not an extractable fact), and add `indicator_behaviors`, `surfacing_cues`, `credit_modes`, `omission_interpretation`. Rewrite `suggested_follow_up` so it models good framing, not extraction.
6. Verify a smoke session through the scorer — the disclosure-aware grading sections of the prompt activate only when `omission_policy` is present, so legacy personas keep working.
