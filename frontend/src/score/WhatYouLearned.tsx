import { useState } from 'react';
import { MONO, PROSE_MAX, SERIF, STATE_STYLE, T } from './theme';
import {
  LEGEND_HINT,
  RESTRAINT_NOTE,
  STATE_WORDS,
  VOLUNTEERED_NOTE,
  WHY_IT_MATTERS,
  itemEvidenceLabel,
  learnedHeading,
  legendCopy,
} from './copy';
import { itemState } from './types';
import type { ItemState, SICItem, TierCoverage } from './types';

/**
 * The second tab: which parts of the stakeholder's knowledge the student opened.
 *
 * Every box opens on click, including the ones that stayed closed — those hold
 * the most useful content on the page, and reading as dead squares was the
 * single biggest loss in the old layout.
 */

function Chip({ state }: { state: ItemState }) {
  return (
    <span style={{
      fontFamily: MONO,
      fontSize: '0.58rem',
      letterSpacing: '0.08em',
      textTransform: 'uppercase',
      color: STATE_STYLE[state].chip,
      flexShrink: 0,
    }}>
      {STATE_WORDS[state]}
    </span>
  );
}

function ItemBox({
  item, state, selected, onSelect,
}: {
  item: SICItem; state: ItemState; selected: boolean; onSelect: () => void;
}) {
  const style = STATE_STYLE[state];
  const label = item.display_label || item.chunk_id;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${label}: ${STATE_WORDS[state]}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        background: style.bg,
        border: selected ? `1.5px solid ${T.textPrimary}` : `1.5px solid ${style.border}`,
        boxShadow: selected ? `0 0 0 3px rgba(28,25,23,0.10)` : 'none',
        borderRadius: 8,
        padding: '0.5rem 0.8rem',
        cursor: 'pointer',
        textAlign: 'left',
        font: 'inherit',
      }}
    >
      <span style={{
        display: 'inline-block', width: 7, height: 7, borderRadius: 999,
        background: style.chip, flexShrink: 0,
      }} />
      <span style={{ fontSize: '0.85rem', fontWeight: 600, color: style.text }}>{label}</span>
      <Chip state={state} />
      <span aria-hidden="true" style={{ color: style.chip, fontSize: '0.9rem', flexShrink: 0 }}>›</span>
    </button>
  );
}

function ItemDetail({ item, tier }: { item: SICItem; tier: TierCoverage }) {
  const state = itemState(item);
  const showedUpUnasked = state === 'not_opened' && item.earned_mode === 'volunteered';
  const restraint =
    state === 'opened' &&
    !item.elicited &&
    item.omission_classification === 'appropriate_non_disclosure';

  // Capped at one. Listing every cue on a closed item turned the most useful
  // panel on the page into a wall the student scrolls past.
  //
  // suggested_move is written as something the student could actually say.
  // surfacing_cues describe the behaviour to a grader ("Student paraphrases the
  // tension before probing"), so they read as stage directions and are only the
  // fallback.
  const oneMove = item.suggested_move || (item.surfacing_cues_missing ?? [])[0] || '';

  return (
    <div style={{
      marginTop: '0.9rem',
      border: `1px solid ${T.cardBorder}`,
      borderRadius: 10,
      overflow: 'hidden',
      background: T.cardBg,
    }}>
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '1rem',
        padding: '0.7rem 1.1rem', background: '#f5f5f4', borderBottom: `1px solid ${T.cardBorder}`,
      }}>
        <span style={{ fontWeight: 700, fontSize: '0.95rem', color: T.textPrimary }}>
          {item.display_label || item.chunk_id}
        </span>
        <span style={{
          fontFamily: MONO, fontSize: '0.66rem', color: T.textFaint, flexShrink: 0,
        }}>
          Tier {tier.tier} · {STATE_WORDS[state]}
        </span>
      </div>

      <div style={{ padding: '0.9rem 1.1rem 1rem' }}>
        <p style={{ margin: 0, color: T.textBody, fontSize: '0.92rem', lineHeight: 1.65 }}>
          {item.fact_summary}
        </p>

        {showedUpUnasked && (
          <p style={{
            margin: '0.75rem 0 0', color: T.textMuted, fontSize: '0.87rem', lineHeight: 1.6,
          }}>
            {VOLUNTEERED_NOTE}
          </p>
        )}

        <div style={{
          fontFamily: MONO, fontSize: '0.64rem', letterSpacing: '0.07em',
          textTransform: 'uppercase', color: T.textFaint, margin: '1rem 0 0.4rem',
        }}>
          {itemEvidenceLabel(state)}
        </div>

        {state === 'earned' && item.evidence_quote && (
          <div style={{
            borderLeft: `3px solid ${STATE_STYLE.earned.border}`,
            padding: '0.4rem 0.9rem',
            fontFamily: SERIF, fontStyle: 'italic',
            color: T.textPrimary, fontSize: '0.93rem', lineHeight: 1.6,
          }}>
            &ldquo;{item.evidence_quote}&rdquo;
          </div>
        )}

        {state === 'opened' && (
          <div style={{ color: T.textBody, fontSize: '0.9rem', lineHeight: 1.65 }}>
            {item.evidence_quote && (
              <div style={{
                borderLeft: `3px solid ${STATE_STYLE.opened.border}`,
                padding: '0.4rem 0.9rem', marginBottom: restraint ? '0.6rem' : 0,
                fontFamily: SERIF, fontStyle: 'italic', color: T.textPrimary,
              }}>
                &ldquo;{item.evidence_quote}&rdquo;
              </div>
            )}
            {restraint && <span>{RESTRAINT_NOTE}</span>}
          </div>
        )}

        {state === 'not_opened' && oneMove && (
          <div style={{
            background: T.tealBg,
            borderLeft: `3px solid ${T.teal}`,
            borderRadius: '0 8px 8px 0',
            padding: '0.55rem 0.9rem',
            color: T.textBody, fontSize: '0.9rem', lineHeight: 1.6,
          }}>
            {oneMove}
          </div>
        )}
      </div>
    </div>
  );
}

function TierSection({ tier }: { tier: TierCoverage }) {
  const [selected, setSelected] = useState<string | null>(null);
  const items = tier.items ?? [];
  if (items.length === 0) return null;

  const states = new Map(items.map(i => [i.chunk_id, itemState(i)] as const));
  const opened = items.filter(i => states.get(i.chunk_id) !== 'not_opened').length;
  const anyClosed = opened < items.length;
  // Never on Tier 3: a number there implies a quota, and restraint is a
  // legitimate outcome. Never a percentage either — personas hold different
  // numbers of items, so a percentage invites a comparison that isn't valid.
  const showCount = tier.tier !== 3;

  const selectedItem = selected ? items.find(i => i.chunk_id === selected) ?? null : null;

  return (
    <section style={{ padding: '1.4rem 0', borderTop: `1px solid ${T.cardBorder}` }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: '0.98rem', color: T.textPrimary }}>
          Tier {tier.tier}
        </span>
        <span style={{ fontWeight: 600, fontSize: '0.95rem', color: T.textHeading, flex: 1 }}>
          {tier.title}
        </span>
        {showCount && (
          <span style={{ fontFamily: MONO, fontSize: '0.72rem', color: T.textFaint, flexShrink: 0 }}>
            {opened} of {items.length}
          </span>
        )}
      </div>

      {tier.description && (
        <p style={{
          margin: '0.45rem 0 0.9rem', color: T.textMuted, fontSize: '0.87rem',
          lineHeight: 1.6, maxWidth: PROSE_MAX,
        }}>
          {tier.description}
        </p>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {items.map(item => (
          <ItemBox
            key={item.chunk_id}
            item={item}
            state={states.get(item.chunk_id) ?? 'not_opened'}
            selected={selected === item.chunk_id}
            onSelect={() => setSelected(selected === item.chunk_id ? null : item.chunk_id)}
          />
        ))}
      </div>

      {anyClosed && (tier.consequence_text || tier.why_it_matters) && (
        <>
          <div style={{
            fontFamily: MONO, fontSize: '0.64rem', letterSpacing: '0.07em',
            textTransform: 'uppercase', color: T.textFaint, margin: '1rem 0 0.35rem',
          }}>
            {WHY_IT_MATTERS}
          </div>
          <p style={{
            margin: 0, paddingLeft: '0.85rem', borderLeft: `2px solid ${T.divider}`,
            color: T.textMuted, fontSize: '0.88rem', lineHeight: 1.65,
            fontStyle: 'italic', maxWidth: PROSE_MAX,
          }}>
            {tier.consequence_text || tier.why_it_matters}
          </p>
        </>
      )}

      {!anyClosed && tier.quick_win && (
        <p style={{
          margin: '1rem 0 0', color: T.greenDeep, fontSize: '0.88rem', fontWeight: 600,
        }}>
          {tier.quick_win}
        </p>
      )}

      {selectedItem && <ItemDetail item={selectedItem} tier={tier} />}
    </section>
  );
}

export default function WhatYouLearned({
  tiers, firstName, their,
}: {
  tiers: TierCoverage[]; firstName: string; their: string;
}) {
  const sorted = [...tiers].sort((a, b) => a.tier - b.tier);
  if (!sorted.some(t => (t.items ?? []).length > 0)) return null;

  const heading = learnedHeading(firstName);

  return (
    <div>
      <h2 style={{ color: T.textPrimary, fontWeight: 700, fontSize: '1.2rem', margin: 0 }}>
        {heading.title}
      </h2>
      <p style={{ margin: '0.25rem 0 1.1rem', color: T.textMuted, fontSize: '0.92rem' }}>
        {heading.subtitle}
      </p>

      {/* Legend sits above Tier 1, not at the foot of the page: the states mean
          something specific here, and a student meets the boxes before it. */}
      <div style={{
        background: '#f5f5f4',
        border: `1px solid ${T.cardBorder}`,
        borderRadius: 10,
        padding: '0.9rem 1.1rem',
      }}>
        {legendCopy(firstName, their).map(({ state, text }) => (
          <div key={state} style={{ display: 'flex', gap: 10, marginBottom: '0.45rem' }}>
            <span style={{
              display: 'inline-block', width: 13, height: 13, borderRadius: 3, marginTop: 3,
              background: STATE_STYLE[state].bg,
              border: `1.5px solid ${STATE_STYLE[state].border}`,
              flexShrink: 0,
            }} />
            <span style={{ color: T.textBody, fontSize: '0.87rem', lineHeight: 1.55 }}>
              <strong style={{ color: T.textPrimary }}>{STATE_WORDS[state]}</strong> — {text}
            </span>
          </div>
        ))}
        <p style={{
          margin: '0.7rem 0 0', color: T.textFaint, fontSize: '0.83rem',
          fontStyle: 'italic', lineHeight: 1.55,
        }}>
          {LEGEND_HINT}
        </p>
      </div>

      {sorted.map(tier => <TierSection key={tier.tier} tier={tier} />)}
    </div>
  );
}
