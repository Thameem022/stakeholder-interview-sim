// ---------------------------------------------------------------------------
// Types matching the IQR v2 SessionEvaluation schema.
//
// The four dimension keys deliberately keep their original names: the renames
// in this report are display-only, and changing the keys would orphan every
// stored evaluation row. See copy.ts for what the student actually reads.
// ---------------------------------------------------------------------------

export type DimensionName =
  | 'framing_and_stakeholder_fit'
  | 'question_quality_and_precision'
  | 'probing_and_follow_up_depth'
  | 'listening_interpretation_and_stewardship';

export type StakeholderResponsePattern = 'became_guarded' | 'opened_up' | 'neutral';

export interface DimensionAssessment {
  dimension: DimensionName;
  score: number;
  assessment: string;
  // Still generated — it informs the Framing moment — but no longer displayed
  // on the dimension card, where it restated the moment with less context.
  stakeholder_response_pattern?: StakeholderResponsePattern | null;
  cause_effect_explanation?: string | null;
}

// ── The three moments ──────────────────────────────────────────────────────

export type ProducedLabel = 'persona_did' | 'you_learned';
export type OutcomeKind = 'it_cost_you' | 'out_of_reach' | 'go_further';
/** Resolved server-side against the real coverage grade, never by the judge. */
export type ItemState = 'earned' | 'opened' | 'not_opened';

export interface Moment {
  headline: string;
  dimension: DimensionName;
  persona_offered?: string | null;
  student_quote: string;
  what_it_produced: string;
  produced_label: ProducedLabel;
  outcome: string;
  outcome_kind: OutcomeKind;
  out_of_reach_item?: string | null;
  out_of_reach_state?: ItemState;
  technique_name: string;
  technique_stem: string;
}

export interface IQRMetadata {
  session_id?: string;
  persona_key?: string;
  persona?: string;
  started_at?: string;
  ended_at?: string;
  [key: string]: unknown;
}

// ── Knowledge coverage ─────────────────────────────────────────────────────

export type SICCreditMode =
  | 'explicit_acknowledgment'
  | 'indirect_acknowledgment'
  | 'reflective_silence'
  | 'explicit'
  | null;

export type SICOmissionClassification =
  | 'insufficient_framing'
  | 'appropriate_non_disclosure'
  | null;

export type EarnedMode = 'earned' | 'volunteered' | 'not_present';

export interface SICItem {
  chunk_id: string;
  /** Authored in the SIC key. Static — not generated per run. */
  display_label?: string;
  domain: string;
  type?: 'fact' | 'signal';
  fact_summary: string;
  suggested_follow_up: string;
  /** Student-facing move for a closed item; guarded server-side. */
  suggested_move?: string;
  elicited: boolean;
  earned_mode?: EarnedMode;
  evidence_quote: string;
  credit_mode?: SICCreditMode;
  omission_classification?: SICOmissionClassification;
  surfacing_cues_used?: string[];
  surfacing_cues_missing?: string[];
}

export type TierCoverageStatus =
  | 'full'
  | 'partial'
  | 'not_accessed_insufficient_framing'
  | 'not_accessed_appropriate_restraint'
  // Legacy: older sessions emit plain 'not_accessed'.
  | 'not_accessed';

export interface TierCoverage {
  tier: number;
  title: string;
  category: string;
  description: string;
  status: TierCoverageStatus;
  percentage: number;
  cues_found: number;
  cues_total: number;
  skill_label?: string;
  why_it_matters?: string;
  quick_win?: string;
  actionable_tip?: string;
  consequence_text?: string;
  items?: SICItem[];
}

export interface SessionEvaluation {
  metadata: IQRMetadata;
  dimensions: DimensionAssessment[];
  overall_score: number;
  skill_label: string;
  moments?: Moment[];
  insight_coverage?: TierCoverage[];
}

/**
 * Earned / Opened / Not opened — the only three states the student sees.
 *
 * Two cases changed meaning here. A signal the persona withheld in response to
 * good framing is "Opened", not a blank miss: the framing worked, and the
 * restraint is the right outcome. Content the persona volunteered unprompted is
 * "Not opened" — it was never the student's move, however relevant it was.
 */
export function itemState(item: SICItem): ItemState {
  if (item.elicited && item.earned_mode === 'earned') {
    return item.credit_mode === 'explicit' || item.credit_mode === 'explicit_acknowledgment'
      ? 'earned'
      : 'opened';
  }
  if (
    !item.elicited &&
    item.type === 'signal' &&
    item.omission_classification === 'appropriate_non_disclosure'
  ) {
    return 'opened';
  }
  return 'not_opened';
}
