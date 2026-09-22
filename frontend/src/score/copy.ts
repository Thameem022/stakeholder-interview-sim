import type { DimensionName, ItemState, OutcomeKind, ProducedLabel } from './types';

/**
 * Every student-facing string in the report.
 *
 * Kept in one file on purpose. The internal names — IQR, SIC, "framing gap",
 * "partially accessed" — stay in the database, the prompts and the instructor
 * materials; none of them belongs in front of a student, and a single file is
 * what makes that checkable.
 */

// ── Persona ────────────────────────────────────────────────────────────────

interface PersonaCopy {
  /** What the report calls them in running prose. */
  firstName: string;
  fullName: string;
  /** Their actual job title, not their archetype. */
  roleTitle: string;
  /** Third-person pronouns used in the static copy. */
  they: string;
  their: string;
}

const PERSONAS: Record<string, PersonaCopy> = {
  alex_martinez: {
    firstName: 'Alex',
    fullName: 'Alex Martinez',
    roleTitle: 'Coastal Resilience Officer',
    they: 'she',
    their: 'her',
  },
  michael_mike_alvarez: {
    firstName: 'Mike',
    fullName: 'Michael "Mike" Alvarez',
    roleTitle: 'East Harbor resident',
    they: 'he',
    their: 'his',
  },
  sarah_donnelly: {
    firstName: 'Sarah',
    fullName: 'Sarah Donnelly',
    roleTitle: 'Downtown business owner',
    they: 'she',
    their: 'her',
  },
  thomas_tom_caldwell: {
    firstName: 'Tom',
    fullName: 'Thomas "Tom" Caldwell',
    roleTitle: 'Waterfront developer',
    they: 'he',
    their: 'his',
  },
};

const FALLBACK_PERSONA: PersonaCopy = {
  firstName: 'the stakeholder',
  fullName: 'the stakeholder',
  roleTitle: '',
  they: 'they',
  their: 'their',
};

export const personaCopy = (key?: string): PersonaCopy =>
  (key && PERSONAS[key]) || FALLBACK_PERSONA;

// ── Tabs ───────────────────────────────────────────────────────────────────

export const TAB_LABELS = {
  interview: 'How you interviewed',
  learned: 'What you learned',
} as const;

// ── How you interviewed ────────────────────────────────────────────────────

/** Two lines under the score, saying what it does and does not measure. */
export const scoreExplainer = (firstName: string) => ({
  line1: `Scores how you interviewed ${firstName} — your setup, your questions, your follow-up, your listening.`,
  line2: `It does not score what you learned about Harbortown. That's on the next tab.`,
});

export const ASSESSMENT_HEADING = 'Your interview assessment';

export const DIMENSION_DISPLAY_ORDER: DimensionName[] = [
  'framing_and_stakeholder_fit',
  'question_quality_and_precision',
  'probing_and_follow_up_depth',
  'listening_interpretation_and_stewardship',
];

export const DIMENSION_LABELS: Record<DimensionName, string> = {
  framing_and_stakeholder_fit: 'Framing & Fit',
  question_quality_and_precision: 'Question Quality',
  probing_and_follow_up_depth: 'Follow-Up Depth',
  listening_interpretation_and_stewardship: 'Listening & Interpretation',
};

/** One emoji per dimension. Not an emoji and an icon — one mark. */
export const DIMENSION_EMOJI: Record<DimensionName, string> = {
  framing_and_stakeholder_fit: '🎯',
  question_quality_and_precision: '📋',
  probing_and_follow_up_depth: '🔍',
  listening_interpretation_and_stewardship: '👂',
};

/** Static sub-heading under each dimension name, naming the persona. */
export const dimensionSubheads = (
  firstName: string,
  their: string,
): Record<DimensionName, string> => ({
  framing_and_stakeholder_fit:
    `How you set up the interview, and how well your questions matched what ${firstName} can actually speak to.`,
  question_quality_and_precision:
    'How clear, focused and answerable your individual questions were.',
  probing_and_follow_up_depth:
    `Whether you followed up on ${firstName}'s answers skilfully enough to draw out richer, more specific responses.`,
  listening_interpretation_and_stewardship:
    `Whether you showed ${firstName} you understood — reflecting back, checking meaning, building on ${their} answers.`,
});

// ── The three moments ──────────────────────────────────────────────────────

export const MOMENTS_HEADING = 'Three moments that shaped this interview';

export const momentRowLabels = (firstName: string) => ({
  personaOffered: `${firstName.toUpperCase()} OFFERED`,
  youSaid: 'YOU SAID',
  produced: {
    persona_did: `${firstName.toUpperCase()} DID`,
    you_learned: 'YOU LEARNED',
  } as Record<ProducedLabel, string>,
  outcome: {
    it_cost_you: 'IT COST YOU',
    out_of_reach: 'OUT OF REACH',
    go_further: 'GO FURTHER',
  } as Record<OutcomeKind, string>,
  tryInstead: 'TRY INSTEAD',
});

/**
 * How a moment refers to a knowledge item. The verb comes from the coverage
 * grade resolved on the server, never from the judge that wrote the moment —
 * the two are scored in parallel and would otherwise be free to disagree.
 */
export const itemReferenceVerb = (state: ItemState | undefined): string => {
  if (state === 'earned') return 'was already yours';
  if (state === 'opened') return 'opened';
  return 'stayed closed';
};

/** The block at the foot of the first tab, pointing at the second. */
export const handOff = (firstName: string) => ({
  body:
    `That's how you interviewed. Now see what it got you — which parts of ${firstName}'s ` +
    `knowledge you actually opened, and what your adaptation plan will be missing without them.`,
  cta: `${TAB_LABELS.learned} →`,
});

// ── What you learned ───────────────────────────────────────────────────────

export const learnedHeading = (firstName: string) => ({
  title: TAB_LABELS.learned,
  subtitle: `Which parts of what ${firstName} knows you opened up, and how deeply.`,
});

export const STATE_WORDS: Record<ItemState, string> = {
  earned: 'Earned',
  opened: 'Opened',
  not_opened: 'Not opened',
};

export const legendCopy = (
  firstName: string,
  their: string,
): Array<{ state: ItemState; text: string }> => [
  { state: 'earned', text: `you asked, and ${firstName} answered.` },
  {
    state: 'opened',
    text:
      `your framing made room. ${firstName} either acknowledged it indirectly, or held back the ` +
      `way a professional in ${their} position would. Either way, that's your move working.`,
  },
  { state: 'not_opened', text: 'nothing in the interview made space for this.' },
];

export const LEGEND_HINT =
  "Tap any box — including the ones you didn't open — to see what it covers and what would have surfaced it.";

export const WHY_IT_MATTERS = 'Why it matters';

/** Label above the evidence in an item's detail panel. */
export const itemEvidenceLabel = (state: ItemState): string => {
  if (state === 'earned') return 'How you accessed it';
  if (state === 'opened') return 'How you opened it';
  return 'One move that would have opened this';
};

export const VOLUNTEERED_NOTE =
  'This came up, but you did not ask for it — the stakeholder raised it on their own. ' +
  'Asking directly is what turns it into something you can rely on.';

export const RESTRAINT_NOTE =
  'They stayed with the difficulty and declined to go further. That is the right outcome here, ' +
  'not a miss — your framing did its job.';

export const TRANSCRIPT_LABEL = 'Conversation transcript';
