import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ChevronDown,
  ChevronUp,
  ClipboardList,
  Search,
  Ear,
  HeartHandshake,
  AlertTriangle,
  ArrowLeft,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Types matching the IQR v1 SessionEvaluation schema
// ---------------------------------------------------------------------------

export type DimensionName =
  | 'framing_and_stakeholder_fit'
  | 'question_quality_and_precision'
  | 'probing_and_follow_up_depth'
  | 'listening_interpretation_and_stewardship';

export type StakeholderResponsePattern =
  | 'became_guarded'
  | 'opened_up'
  | 'neutral';

export interface DimensionAssessment {
  dimension: DimensionName;
  score: number;
  assessment: string;
  evidence_quote: string;
  what_was_missed: string;
  stakeholder_response_pattern?: StakeholderResponsePattern | null;
  cause_effect_explanation?: string | null;
}

export interface TopStrip {
  strength: string;
  missed_opportunities: string[];
  next_move: string;
}

export interface IQRMetadata {
  session_id?: string;
  persona_key?: string;
  persona?: string;
  started_at?: string;
  ended_at?: string;
  [key: string]: unknown;
}

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
  domain: string;
  type?: 'fact' | 'signal';
  fact_summary: string;
  suggested_follow_up: string;
  elicited: boolean;
  earned_mode?: EarnedMode;
  evidence_quote: string;
  credit_mode?: SICCreditMode;
  omission_classification?: SICOmissionClassification;
  surfacing_cues_used?: string[];
  surfacing_cues_missing?: string[];
  display_label?: string;
}

export type TierCoverageStatus =
  | 'full'
  | 'partial'
  | 'not_accessed_insufficient_framing'
  | 'not_accessed_appropriate_restraint'
  // Legacy: older sessions emit plain 'not_accessed'. Treated as
  // 'not_accessed_insufficient_framing' for display purposes.
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
  overall_summary: string;
  depth_note: string;
  earned_vs_volunteered_note: string;
  top_strip: TopStrip;
  insight_coverage?: TierCoverage[];
}

// ---------------------------------------------------------------------------
// Theme — mirrors Streamlit Diagnostic Coach palette exactly
// ---------------------------------------------------------------------------

const T = {
  pageBg:      '#fafaf9',
  cardBg:      '#ffffff',
  cardBorder:  '#e7e5e4',
  divider:     '#d6d3d1',

  textPrimary: '#0c0a09',
  textHeading: '#292524',
  textBody:    '#44403c',
  textMuted:   '#57534e',
  textFaint:   '#78716c',
  textGhost:   '#a8a29e',

  amberAccent: '#d97706',
  amberDeep:   '#b45309',
  amberBg:     '#fffbeb',
  amberBg2:    '#fff7ed',
  amberSoft:   '#fef3c7',
  amberBorder: '#fbbf24',

  greenAccent: '#059669',
  greenDeep:   '#047857',
  greenBg:     '#ecfdf5',
  greenBg2:    '#f0fdf4',
  greenSoft:   '#d1fae5',
  greenBorder: '#34d399',

  redAccent:   '#dc2626',
  redDeep:     '#b91c1c',
  redBg:       '#fef2f2',
  redBg2:      '#fff1f2',
  redSoft:     '#fee2e2',
  redBorder:   '#fca5a5',
};

function coachColors(score: number) {
  if (score < 6.0)
    return { accent: T.redAccent, deep: T.redDeep, bg: T.redBg, bg2: T.redBg2, soft: T.redSoft, border: T.redAccent, quoteBorder: T.redBorder };
  return score >= 8
    ? { accent: T.greenAccent, deep: T.greenDeep, bg: T.greenBg, bg2: T.greenBg2, soft: T.greenSoft, border: T.greenAccent, quoteBorder: T.greenBorder }
    : { accent: T.amberAccent, deep: T.amberDeep, bg: T.amberBg, bg2: T.amberBg2, soft: T.amberSoft, border: T.amberAccent, quoteBorder: T.amberBorder };
}

function scoreBarColor(score: number) {
  if (score < 6.0) return T.redAccent;
  if (score >= 8) return T.greenAccent;
  if (score >= 7) return '#2563eb';
  return T.amberAccent;
}

// ---------------------------------------------------------------------------
// IQR v1 constants
// ---------------------------------------------------------------------------

const DIMENSION_DISPLAY_ORDER: DimensionName[] = [
  'framing_and_stakeholder_fit',
  'question_quality_and_precision',
  'probing_and_follow_up_depth',
  'listening_interpretation_and_stewardship',
];

const DIMENSION_LABELS: Record<DimensionName, string> = {
  framing_and_stakeholder_fit:              'Framing & Stakeholder Fit',
  question_quality_and_precision:           'Question Quality & Precision',
  probing_and_follow_up_depth:              'Probing & Follow-Up Depth',
  listening_interpretation_and_stewardship: 'Listening, Interpretation & Stewardship',
};

const DIMENSION_ICONS: Record<DimensionName, React.ReactNode> = {
  framing_and_stakeholder_fit:              <HeartHandshake size={16} />,
  question_quality_and_precision:           <ClipboardList size={16} />,
  probing_and_follow_up_depth:              <Search size={16} />,
  listening_interpretation_and_stewardship: <Ear size={16} />,
};

const DIMENSION_EMOJI: Record<DimensionName, string> = {
  framing_and_stakeholder_fit:              '🎯',
  question_quality_and_precision:           '📋',
  probing_and_follow_up_depth:              '🔍',
  listening_interpretation_and_stewardship: '👂',
};

const CREDIT_MODE_LABEL: Record<string, string> = {
  explicit_acknowledgment: 'Explicit acknowledgment',
  indirect_acknowledgment: 'Indirect acknowledgment',
  reflective_silence:      'Reflective silence (credit for framing)',
  explicit:                'Captured',
};

// ── Card color for SIC items ──
function cardColor(item: SICItem): { bg: string; border: string; text: string; state: 'accessed' | 'partial' | 'not_accessed' } {
  if (item.elicited && item.earned_mode === 'earned') {
    if (item.credit_mode === 'explicit_acknowledgment' || item.credit_mode === 'explicit') {
      return { bg: '#a3c952', border: '#7fa33e', text: '#1a2e05', state: 'accessed' };
    }
    if (item.credit_mode === 'indirect_acknowledgment' || item.credit_mode === 'reflective_silence') {
      return { bg: '#fde047', border: '#ca8a04', text: '#713f12', state: 'partial' };
    }
    return { bg: '#a3c952', border: '#7fa33e', text: '#1a2e05', state: 'accessed' };
  }
  if (item.earned_mode === 'volunteered') {
    return { bg: '#fde047', border: '#ca8a04', text: '#713f12', state: 'partial' };
  }
  return { bg: '#ffffff', border: '#d1d5db', text: '#6b7280', state: 'not_accessed' };
}

function fallbackLabel(factSummary: string): string {
  const words = factSummary
    .replace(/^[A-Z][a-z]+ (understands|carries|frames|acknowledges|holds|does not|is aware) .*? (that |— )/, '')
    .replace(/ — .*$/, '')
    .split(/\s+/)
    .slice(0, 3);
  return words.map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

// ---------------------------------------------------------------------------
// SIC Card Grid — replaces CueHeatmap + InsightCoveragePanel
// ---------------------------------------------------------------------------

function SICCardGrid({ tiers }: { tiers: TierCoverage[] }) {
  const sorted = [...tiers].sort((a, b) => a.tier - b.tier);
  const [selected, setSelected] = useState<{ tier: number; idx: number } | null>(null);
  const [hover, setHover] = useState<{
    item: SICItem;
    label: string;
    rect: { top: number; left: number; width: number; height: number };
  } | null>(null);

  const hasItems = sorted.some(t => Array.isArray(t.items) && t.items.length > 0);
  if (!hasItems) return null;

  const sel = selected
    ? sorted.find(t => t.tier === selected.tier)?.items?.[selected.idx] ?? null
    : null;
  const selTier = selected ? sorted.find(t => t.tier === selected.tier) ?? null : null;

  return (
    <div style={{
      background: T.cardBg,
      border: `1px solid ${T.cardBorder}`,
      borderRadius: 14,
      overflow: 'hidden',
      marginBottom: '1.35rem',
      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    }}>
      <div style={{
        padding: '1rem 1.5rem',
        borderBottom: `1px solid ${T.cardBorder}`,
        background: '#f5f5f4',
      }}>
        <h3 style={{ margin: 0, fontWeight: 700, fontSize: '1.05rem', color: T.textHeading }}>
          Stakeholder Information Coverage
        </h3>
        <p style={{ margin: '0.2rem 0 0', fontSize: '0.83rem', color: T.textMuted }}>
          Which knowledge items you accessed during the interview
        </p>
      </div>

      {sorted.map((tier, idx) => {
        const items = tier.items ?? [];
        if (items.length === 0) return null;
        const hasMissed = items.some(it => cardColor(it).state === 'not_accessed');
        const allAccessed = items.every(it => cardColor(it).state === 'accessed');

        return (
          <div key={tier.tier} style={{
            padding: '1.25rem 1.5rem',
            borderTop: idx > 0 ? `1px solid ${T.cardBorder}` : 'none',
          }}>
            <div style={{ marginBottom: '0.75rem' }}>
              <span style={{ fontWeight: 700, fontSize: '0.97rem', color: T.textHeading }}>
                Tier {tier.tier}
              </span>
              {tier.title && (
                <span style={{ fontWeight: 500, fontSize: '0.88rem', color: T.textMuted, marginLeft: 6 }}>
                  {tier.title}
                </span>
              )}
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              {items.map((item, itemIdx) => {
                const color = cardColor(item);
                const label = item.display_label || fallbackLabel(item.fact_summary);
                const isSel = selected?.tier === tier.tier && selected?.idx === itemIdx;
                return (
                  <button
                    key={item.chunk_id}
                    type="button"
                    onClick={() => setSelected(isSel ? null : { tier: tier.tier, idx: itemIdx })}
                    onMouseEnter={e => {
                      e.currentTarget.style.transform = 'translateY(-2px)';
                      const r = e.currentTarget.getBoundingClientRect();
                      setHover({
                        item,
                        label,
                        rect: { top: r.top, left: r.left, width: r.width, height: r.height },
                      });
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.transform = 'translateY(0)';
                      setHover(null);
                    }}
                    aria-label={`${label}: ${color.state === 'accessed' ? 'Accessed' : color.state === 'partial' ? 'Partially accessed' : 'Not accessed'}`}
                    aria-pressed={isSel}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: 130,
                      height: 58,
                      borderRadius: 8,
                      background: color.bg,
                      border: isSel ? `2px solid #1c1917` : `1.5px solid ${color.border}`,
                      boxShadow: isSel ? '0 0 0 3px rgba(28,25,23,0.12)' : '0 1px 3px rgba(0,0,0,0.06)',
                      cursor: 'pointer',
                      padding: '6px 10px',
                      transition: 'transform 0.1s ease, box-shadow 0.15s ease',
                      textAlign: 'center',
                    }}
                  >
                    <span style={{
                      fontSize: '0.82rem',
                      fontWeight: 600,
                      color: color.text,
                      lineHeight: 1.3,
                      overflow: 'hidden',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}>
                      {label}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Consequence / positive text below cards */}
            {tier.consequence_text && hasMissed && (
              <p style={{
                margin: '0.75rem 0 0',
                fontSize: '0.85rem',
                color: '#57534e',
                lineHeight: 1.55,
                fontStyle: 'italic',
              }}>
                {tier.consequence_text}
              </p>
            )}
            {allAccessed && tier.quick_win && (
              <p style={{
                margin: '0.75rem 0 0',
                fontSize: '0.85rem',
                color: T.greenDeep,
                lineHeight: 1.55,
                fontWeight: 600,
              }}>
                {tier.quick_win}
              </p>
            )}
            {!tier.consequence_text && hasMissed && tier.why_it_matters && (
              <p style={{
                margin: '0.75rem 0 0',
                fontSize: '0.85rem',
                color: '#57534e',
                lineHeight: 1.55,
                fontStyle: 'italic',
              }}>
                {tier.why_it_matters}
              </p>
            )}

            {/* Detail panel when a card in this tier is clicked */}
            {sel && selTier && selTier.tier === tier.tier && (() => {
              const isSignal = sel.type === 'signal';
              const isAppropriateRestraint = !sel.elicited && isSignal && sel.omission_classification === 'appropriate_non_disclosure';
              const isFramingGap = !sel.elicited && (!isSignal || sel.omission_classification === 'insufficient_framing' || sel.omission_classification == null);
              const selColor = cardColor(sel);
              const panelBg = selColor.state === 'accessed'
                ? T.greenBg2
                : selColor.state === 'partial' ? '#fefce8' : (isAppropriateRestraint ? '#f8fafc' : T.redBg2);

              const stateLabel = sel.elicited
                ? (sel.credit_mode && CREDIT_MODE_LABEL[sel.credit_mode]) || 'Elicited'
                : (isAppropriateRestraint ? 'Appropriate restraint — in-character' : 'Framing gap');

              return (
                <div style={{
                  marginTop: '0.75rem',
                  padding: '1rem 1.25rem',
                  borderRadius: 10,
                  background: panelBg,
                  border: `1px solid ${T.cardBorder}`,
                }}>
                  <div style={{
                    fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.07em',
                    textTransform: 'uppercase', color: T.textFaint, marginBottom: '0.4rem',
                  }}>
                    Tier {selTier.tier} · {sel.domain || 'cue'} · {stateLabel}
                  </div>
                  <p style={{
                    margin: '0 0 0.65rem 0',
                    fontSize: '0.95rem', color: T.textHeading, lineHeight: 1.55, fontWeight: 600,
                  }}>
                    {sel.fact_summary}
                  </p>
                  {!sel.elicited && sel.earned_mode === 'volunteered' && (
                    <div style={{
                      display: 'flex', alignItems: 'flex-start', gap: 8,
                      background: '#fff7ed',
                      border: '1px solid #fb923c',
                      borderRadius: 8,
                      padding: '0.55rem 0.85rem',
                      marginBottom: '0.6rem',
                    }}>
                      <AlertTriangle size={14} style={{ color: '#c2410c', marginTop: 2, flexShrink: 0 }} />
                      <span style={{ fontSize: '0.86rem', color: '#7c2d12', lineHeight: 1.55 }}>
                        <strong style={{ color: '#c2410c' }}>Mentioned but not elicited</strong> — the stakeholder volunteered this content unprompted. Try probing this directly next time.
                      </span>
                    </div>
                  )}
                  {sel.elicited && sel.evidence_quote && (
                    <div style={{
                      borderLeft: `3px solid ${T.greenBorder}`,
                      padding: '0.4rem 0.8rem',
                      background: 'white', borderRadius: '0 8px 8px 0',
                      fontFamily: "Georgia, 'Times New Roman', serif",
                      fontSize: '0.92rem', color: T.textPrimary, lineHeight: 1.55,
                      marginBottom: '0.5rem',
                    }}>
                      &ldquo;{sel.evidence_quote}&rdquo;
                    </div>
                  )}
                  {!!sel.surfacing_cues_used?.length && (
                    <div style={{ marginBottom: '0.5rem' }}>
                      <div style={{ fontWeight: 700, color: T.greenDeep, fontSize: '0.72rem',
                        textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
                        What you did
                      </div>
                      <ul style={{ margin: 0, padding: '0 0 0 1.1rem', color: T.textBody, fontSize: '0.86rem', lineHeight: 1.55 }}>
                        {sel.surfacing_cues_used.map((cue, i) => <li key={i}>{cue}</li>)}
                      </ul>
                    </div>
                  )}
                  {isAppropriateRestraint && (
                    <div style={{
                      borderLeft: `3px solid #94a3b8`,
                      padding: '0.4rem 0.8rem',
                      background: 'white', borderRadius: '0 8px 8px 0',
                      fontSize: '0.88rem', color: T.textBody, lineHeight: 1.55,
                    }}>
                      <span style={{ fontWeight: 700, color: '#475569' }}>In-character restraint: </span>
                      The stakeholder maintained appropriate professional restraint here. Your framing was respectful — this is not a missed quota.
                    </div>
                  )}
                  {isFramingGap && isSignal && !!sel.surfacing_cues_missing?.length && (
                    <div>
                      <div style={{ fontWeight: 700, color: T.amberDeep, fontSize: '0.72rem',
                        textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
                        What would have surfaced this
                      </div>
                      <ul style={{ margin: 0, padding: '0 0 0 1.1rem', color: T.textBody, fontSize: '0.86rem', lineHeight: 1.55 }}>
                        {sel.surfacing_cues_missing.map((cue, i) => <li key={i}>{cue}</li>)}
                      </ul>
                    </div>
                  )}
                  {isFramingGap && !isSignal && sel.suggested_follow_up && (
                    <div style={{
                      borderLeft: `3px solid ${T.amberBorder}`,
                      padding: '0.4rem 0.8rem',
                      background: 'white', borderRadius: '0 8px 8px 0',
                      fontSize: '0.88rem', color: T.textBody, lineHeight: 1.55,
                    }}>
                      <span style={{ fontWeight: 700, color: T.amberDeep }}>Try next time: </span>
                      {sel.suggested_follow_up}
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        );
      })}

      {/* Legend */}
      <div style={{
        padding: '0.75rem 1.5rem',
        borderTop: `1px solid ${T.cardBorder}`,
        background: '#f5f5f4',
        display: 'flex',
        gap: '1.5rem',
        alignItems: 'center',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ display: 'inline-block', width: 16, height: 16, borderRadius: 4,
            background: '#a3c952', border: '1.5px solid #7fa33e' }} />
          <span style={{ fontSize: '0.82rem', color: T.textMuted, fontWeight: 600 }}>Accessed</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ display: 'inline-block', width: 16, height: 16, borderRadius: 4,
            background: '#fde047', border: '1.5px solid #ca8a04' }} />
          <span style={{ fontSize: '0.82rem', color: T.textMuted, fontWeight: 600 }}>Partially accessed</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ display: 'inline-block', width: 16, height: 16, borderRadius: 4,
            background: '#ffffff', border: '1.5px solid #d1d5db' }} />
          <span style={{ fontSize: '0.82rem', color: T.textMuted, fontWeight: 600 }}>Not accessed</span>
        </div>
      </div>

      {hover && (() => {
        const TOOLTIP_W = 280;
        const TOOLTIP_MARGIN = 8;
        const ESTIMATED_H = 160;
        const flipBelow = hover.rect.top < ESTIMATED_H + TOOLTIP_MARGIN + 8;
        const top = flipBelow
          ? hover.rect.top + hover.rect.height + TOOLTIP_MARGIN
          : hover.rect.top - TOOLTIP_MARGIN;
        let left = hover.rect.left + hover.rect.width / 2 - TOOLTIP_W / 2;
        const maxLeft = Math.max(8, window.innerWidth - TOOLTIP_W - 8);
        left = Math.max(8, Math.min(left, maxLeft));
        return (
          <div style={{
            position: 'fixed',
            top,
            left,
            width: TOOLTIP_W,
            transform: flipBelow ? 'none' : 'translateY(-100%)',
            background: '#1c1917',
            color: '#f5f5f4',
            borderRadius: 10,
            padding: '0.75rem 0.9rem',
            fontSize: '0.82rem',
            lineHeight: 1.55,
            pointerEvents: 'none',
            zIndex: 1000,
            boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
          }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>{hover.label}</div>
            <div style={{ color: '#d6d3d1' }}>{hover.item.fact_summary}</div>
            {hover.item.elicited && hover.item.evidence_quote && (
              <div style={{
                marginTop: 8,
                fontStyle: 'italic',
                color: '#a8a29e',
                borderLeft: '2px solid #57534e',
                paddingLeft: 8,
              }}>
                &ldquo;{hover.item.evidence_quote}&rdquo;
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}



// ---------------------------------------------------------------------------
// CoachingStrip — first thing the student sees in the IQR tab
// ---------------------------------------------------------------------------

function CoachingStrip({ strip, overallScore }: { strip: TopStrip; overallScore: number }) {
  const isExtension = overallScore >= 8;
  const middleLabel = isExtension ? 'Extend' : 'Missed';
  const middleBg = isExtension ? T.greenBg : T.amberBg;
  const middleBorder = isExtension ? T.greenBorder : T.amberBorder;
  const middleAccent = isExtension ? T.greenDeep : T.amberDeep;
  return (
    <div style={{
      background: T.cardBg,
      border: `1px solid ${T.cardBorder}`,
      borderRadius: 14,
      overflow: 'hidden',
      marginBottom: '1.35rem',
      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    }}>
      <div style={{
        padding: '0.75rem 1.25rem',
        borderBottom: `1px solid ${T.cardBorder}`,
        background: '#f5f5f4',
      }}>
        <span style={{ fontWeight: 700, fontSize: '0.95rem', color: T.textHeading }}>
          Interview Process Summary
        </span>
      </div>
      <div style={{ padding: '1rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '0.65rem' }}>
        {/* Strength — green */}
        <div style={{
          background: T.greenBg,
          border: `1px solid ${T.greenBorder}`,
          borderRadius: 10,
          padding: '0.7rem 1rem',
          display: 'flex', alignItems: 'flex-start', gap: 10,
        }}>
          <span style={{
            fontWeight: 700, color: T.greenDeep, fontSize: '0.72rem',
            textTransform: 'uppercase', letterSpacing: '0.07em',
            flexShrink: 0, marginTop: 3, minWidth: 60,
          }}>
            Strength
          </span>
          <span style={{ color: T.textBody, fontSize: '0.93rem', lineHeight: 1.55 }}>
            {strip.strength}
          </span>
        </div>
        {/* Missed opportunities — amber by default, green "Extend" at score ≥ 8 */}
        {strip.missed_opportunities.map((opp, i) => (
          <div key={i} style={{
            background: middleBg,
            border: `1px solid ${middleBorder}`,
            borderRadius: 10,
            padding: '0.7rem 1rem',
            display: 'flex', alignItems: 'flex-start', gap: 10,
          }}>
            <span style={{
              fontWeight: 700, color: middleAccent, fontSize: '0.72rem',
              textTransform: 'uppercase', letterSpacing: '0.07em',
              flexShrink: 0, marginTop: 3, minWidth: 60,
            }}>
              {middleLabel}
            </span>
            <span style={{ color: T.textBody, fontSize: '0.93rem', lineHeight: 1.55 }}>{opp}</span>
          </div>
        ))}
        {/* Next move — blue */}
        <div style={{
          background: '#eff6ff',
          border: '1px solid #93c5fd',
          borderRadius: 10,
          padding: '0.7rem 1rem',
          display: 'flex', alignItems: 'flex-start', gap: 10,
        }}>
          <span style={{
            fontWeight: 700, color: '#1d4ed8', fontSize: '0.72rem',
            textTransform: 'uppercase', letterSpacing: '0.07em',
            flexShrink: 0, marginTop: 3, minWidth: 60,
          }}>
            Next move
          </span>
          <span style={{ color: T.textBody, fontSize: '0.93rem', lineHeight: 1.55 }}>
            {strip.next_move}
          </span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DimensionCard — IQR v1 structure
// ---------------------------------------------------------------------------

function DimensionCard({ res }: { res: DimensionAssessment }) {
  const score = res.score;
  const c = coachColors(score);
  const label = DIMENSION_LABELS[res.dimension] ?? res.dimension;
  const icon  = DIMENSION_ICONS[res.dimension]  ?? null;
  const emoji = DIMENSION_EMOJI[res.dimension]  ?? '•';

  return (
    <div style={{
      background: T.cardBg, border: `1px solid ${T.cardBorder}`,
      borderRadius: 12, marginBottom: '1.1rem',
      overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
    }}>
      {/* Header */}
      <div style={{
        padding: '0.85rem 1.1rem',
        borderBottom: `1px solid ${T.cardBorder}`,
        display: 'flex', alignItems: 'center', gap: 8,
        background: T.cardBg,
      }}>
        <span style={{ color: c.accent }}>{icon}</span>
        <span style={{ fontWeight: 700, fontSize: '1.05rem', color: T.textPrimary }}>
          {emoji} {label}
        </span>
        <span style={{ color: c.deep, fontWeight: 700, marginLeft: 4 }}>
          — {score.toFixed(1)}/10
        </span>
      </div>

      {/* Score bar */}
      <div style={{ padding: '0.45rem 1.1rem 0', background: T.cardBg }}>
        <div style={{ height: 5, borderRadius: 999, background: T.cardBorder, overflow: 'hidden' }}>
          <div style={{
            height: '100%', borderRadius: 999,
            width: `${(score / 10) * 100}%`,
            background: scoreBarColor(score),
            transition: 'width 0.6s ease',
          }} />
        </div>
      </div>

      {/* Assessment */}
      <div style={{
        borderTop: `1px solid ${T.cardBorder}`,
        padding: '0.9rem 1.15rem 0.75rem',
      }}>
        <div style={{
          fontWeight: 800, fontSize: '0.68rem', letterSpacing: '0.07em',
          textTransform: 'uppercase', color: T.textMuted, marginBottom: '0.45rem',
        }}>
          Assessment
        </div>
        <p style={{ color: T.textBody, lineHeight: 1.65, margin: 0, fontSize: '0.95rem' }}>
          {res.assessment}
        </p>
      </div>

      {/* Evidence quote */}
      <div style={{
        borderTop: `1px solid ${T.cardBorder}`,
        padding: '0.9rem 1.15rem 0.85rem',
        background: c.bg,
        borderLeft: `4px solid ${c.quoteBorder}`,
      }}>
        <div style={{
          fontWeight: 800, fontSize: '0.68rem', letterSpacing: '0.07em',
          textTransform: 'uppercase', color: T.textMuted, marginBottom: '0.55rem',
        }}>
          Evidence
        </div>
        <div style={{
          fontFamily: "Georgia, 'Times New Roman', serif",
          fontSize: '0.98rem', color: T.textPrimary, lineHeight: 1.6,
        }}>
          &ldquo;{res.evidence_quote}&rdquo;
        </div>
      </div>

      {/* Stakeholder response pattern callout — Framing dimension only */}
      {res.dimension === 'framing_and_stakeholder_fit'
        && res.cause_effect_explanation
        && (res.stakeholder_response_pattern === 'became_guarded'
            || res.stakeholder_response_pattern === 'opened_up')
        && (() => {
        const guarded = res.stakeholder_response_pattern === 'became_guarded';
        const bg = guarded ? T.amberBg : T.greenBg;
        const border = guarded ? T.amberBorder : T.greenBorder;
        const accent = guarded ? T.amberDeep : T.greenDeep;
        const label = guarded
          ? 'How the stakeholder responded'
          : 'How the stakeholder responded';
        return (
          <div style={{
            borderTop: `1px dashed ${T.divider}`,
            padding: '0.85rem 1.15rem 0.5rem',
            background: T.pageBg,
          }}>
            <div style={{
              display: 'flex', gap: 8, alignItems: 'flex-start',
              background: bg, border: `1px solid ${border}`,
              borderRadius: 8, padding: '0.6rem 0.85rem',
            }}>
              <HeartHandshake size={14} style={{ color: accent, marginTop: 2, flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 700, color: accent, fontSize: '0.78rem', marginBottom: 2 }}>
                  {label}
                </div>
                <div style={{ color: T.textBody, fontSize: '0.88rem', lineHeight: 1.55 }}>
                  {res.cause_effect_explanation}
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* What was missed / Go further — extension framing kicks in at score ≥ 8 */}
      {res.what_was_missed && (() => {
        const isExtension = score >= 8;
        const bg = isExtension ? T.greenBg : T.amberBg;
        const border = isExtension ? T.greenBorder : T.amberBorder;
        const accent = isExtension ? T.greenDeep : T.amberDeep;
        const label = isExtension ? 'Go further' : 'What was missed';
        return (
          <div style={{
            borderTop: `1px dashed ${T.divider}`,
            padding: '0.85rem 1.15rem 1rem',
            background: T.pageBg,
          }}>
            <div style={{
              display: 'flex', gap: 8, alignItems: 'flex-start',
              background: bg, border: `1px solid ${border}`,
              borderRadius: 8, padding: '0.6rem 0.85rem',
            }}>
              <AlertTriangle size={14} style={{ color: accent, marginTop: 2, flexShrink: 0 }} />
              <div>
                <div style={{ fontWeight: 700, color: accent, fontSize: '0.78rem', marginBottom: 2 }}>
                  {label}
                </div>
                <div style={{ color: T.textBody, fontSize: '0.88rem', lineHeight: 1.55 }}>
                  {res.what_was_missed}
                </div>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Collapsible section (transcript + glossary)
// ---------------------------------------------------------------------------

function Collapsible({ label, icon, children }: { label: string; icon?: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{
      background: T.cardBg, border: `1px solid ${T.cardBorder}`,
      borderRadius: 12, overflow: 'hidden', marginBottom: '0.75rem',
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1.25rem', background: '#f5f5f4',
          border: 'none', cursor: 'pointer',
          color: T.textHeading, fontSize: '0.92rem', fontWeight: 600, gap: 8,
        }}
        onMouseEnter={e => (e.currentTarget.style.background = T.cardBorder)}
        onMouseLeave={e => (e.currentTarget.style.background = '#f5f5f4')}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {icon}
          {label}
        </span>
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {open && (
        <div style={{ padding: '1.25rem', borderTop: `1px solid ${T.cardBorder}` }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ScoreReport component
// ---------------------------------------------------------------------------

interface ScoreReportProps {
  evaluation: SessionEvaluation;
  onClose?: () => void;
}

export default function ScoreReport({ evaluation, onClose }: ScoreReportProps) {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<'iqr' | 'sic'>('iqr');

  const overallScore = evaluation.overall_score ?? 0;
  const c            = coachColors(overallScore);
  const turns        = (evaluation.metadata as any)?.turns as any[] | undefined;
  const hasSicData   = Array.isArray(evaluation.insight_coverage) && evaluation.insight_coverage.length > 0;

  // Sort dimensions into canonical display order; append any extras at the end.
  const dimMap = new Map(evaluation.dimensions.map(d => [d.dimension, d]));
  const orderedDims: DimensionAssessment[] = [
    ...DIMENSION_DISPLAY_ORDER.filter(k => dimMap.has(k)).map(k => dimMap.get(k)!),
    ...evaluation.dimensions.filter(d => !DIMENSION_DISPLAY_ORDER.includes(d.dimension)),
  ];

  return (
    <div style={{ minHeight: '100vh', background: T.pageBg, fontFamily: "'Segoe UI', system-ui, -apple-system, sans-serif" }}>
      <div style={{ padding: '2rem 2.5rem' }}>

        {/* ── Back button ── */}
        <button
          onClick={() => onClose ? onClose() : navigate('/')}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: 'none', border: 'none', cursor: 'pointer',
            color: T.textMuted, fontSize: '0.88rem', fontWeight: 600,
            marginBottom: '1.5rem', padding: 0,
          }}
          onMouseEnter={e => (e.currentTarget.style.color = T.textPrimary)}
          onMouseLeave={e => (e.currentTarget.style.color = T.textMuted)}
        >
          <ArrowLeft size={16} />
          Back to interview
        </button>

        {/* ── Page title + tab toggle ── */}
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '1.25rem', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ color: T.textPrimary, fontWeight: 700, fontSize: '1.9rem', margin: '0 0 0.15rem 0' }}>
              Interview Diagnostic Coach
            </h1>
            <p style={{ color: T.textMuted, fontSize: '1rem', margin: 0 }}>
              {activeTab === 'iqr'
                ? 'Interview process — feedback for your next session'
                : 'Content coverage — which knowledge tiers you accessed'}
            </p>
          </div>
          <div style={{
            display: 'inline-flex',
            background: T.cardBorder,
            borderRadius: 10,
            padding: 3,
            gap: 2,
            flexShrink: 0,
          }}>
            {(['iqr', ...(hasSicData ? ['sic'] : [])] as const).map(tab => {
              const active = activeTab === tab;
              return (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab as 'iqr' | 'sic')}
                  style={{
                    padding: '0.45rem 1.1rem',
                    borderRadius: 8,
                    border: 'none',
                    cursor: 'pointer',
                    fontWeight: 700,
                    fontSize: '0.88rem',
                    letterSpacing: '0.03em',
                    transition: 'all 0.18s ease',
                    background: active ? T.cardBg : 'transparent',
                    color: active ? T.textPrimary : T.textFaint,
                    boxShadow: active ? '0 1px 4px rgba(0,0,0,0.10)' : 'none',
                  }}
                >
                  {tab === 'iqr' ? 'IQR' : 'SIC'}
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Overall score badge ── */}
        {orderedDims.length > 0 && activeTab === 'iqr' && (
          <div style={{
            background: `linear-gradient(135deg, ${c.bg} 0%, ${c.bg2} 50%, #ffffff 100%)`,
            border: `1px solid ${T.cardBorder}`,
            borderLeft: `6px solid ${c.border}`,
            borderRadius: 14,
            padding: '1.25rem 1.75rem',
            marginBottom: '1.35rem',
            boxShadow: '0 8px 24px rgba(28, 25, 23, 0.08)',
            display: 'flex', alignItems: 'baseline', gap: '1rem', flexWrap: 'wrap',
          }}>
            <div style={{ fontSize: '2rem', fontWeight: 800, color: c.deep, lineHeight: 1.2, letterSpacing: '-0.02em' }}>
              {overallScore.toFixed(1)}/10
            </div>
            {evaluation.skill_label && (
              <div style={{ fontSize: '1.05rem', fontWeight: 600, color: c.accent }}>
                {evaluation.skill_label}
              </div>
            )}
            <div style={{
              flexBasis: '100%',
              fontSize: '0.78rem',
              fontWeight: 600,
              color: T.textFaint,
              marginTop: '0.35rem',
              letterSpacing: '0.01em',
            }}>
              Interview process score · doesn't include content coverage (SIC)
            </div>
          </div>
        )}

        {/* ══════════════ SIC TAB ══════════════ */}
        {activeTab === 'sic' && hasSicData && (
          <>
            <SICCardGrid tiers={evaluation.insight_coverage!} />
          </>
        )}

        {/* ══════════════ IQR TAB ══════════════ */}
        {activeTab === 'iqr' && (
          <>
            {/* ── 1. Coaching strip (first thing the student sees) ── */}
            {evaluation.top_strip && (
              <CoachingStrip strip={evaluation.top_strip} overallScore={overallScore} />
            )}

            {/* ── 2. Dimension panels ── */}
            {orderedDims.length > 0 && (
              <>
                <h2 style={{ color: T.textHeading, fontWeight: 600, fontSize: '1.1rem', margin: '0 0 1rem 0' }}>
                  Dimension diagnostics
                </h2>
                {orderedDims.map(d => <DimensionCard key={d.dimension} res={d} />)}
              </>
            )}

            {/* ── 3. Overall summary + callouts ── */}
            <div style={{
              background: T.cardBg, border: `1px solid ${T.cardBorder}`,
              borderRadius: 12, padding: '1.25rem',
              boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
              marginBottom: '1rem',
            }}>
              <h3 style={{ color: T.textHeading, fontWeight: 600, margin: '0 0 0.65rem 0', fontSize: '1rem' }}>
                Overall summary
              </h3>
              <p style={{ color: T.textMuted, lineHeight: 1.65, fontSize: '0.9rem', margin: 0 }}>
                {evaluation.overall_summary}
              </p>
            </div>

            {/* Depth + earned-vs-volunteered callouts */}
            {(evaluation.depth_note || evaluation.earned_vs_volunteered_note) && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '1.35rem' }}>
                {evaluation.depth_note && (
                  <div style={{
                    background: T.cardBg, border: `1px solid ${T.cardBorder}`,
                    borderRadius: 10, padding: '0.85rem 1rem',
                  }}>
                    <div style={{
                      fontWeight: 800, fontSize: '0.68rem', letterSpacing: '0.07em',
                      textTransform: 'uppercase', color: T.textMuted, marginBottom: '0.4rem',
                    }}>
                      Depth
                    </div>
                    <p style={{ margin: 0, color: T.textBody, fontSize: '0.88rem', lineHeight: 1.55 }}>
                      {evaluation.depth_note}
                    </p>
                  </div>
                )}
                {evaluation.earned_vs_volunteered_note && (
                  <div style={{
                    background: T.cardBg, border: `1px solid ${T.cardBorder}`,
                    borderRadius: 10, padding: '0.85rem 1rem',
                  }}>
                    <div style={{
                      fontWeight: 800, fontSize: '0.68rem', letterSpacing: '0.07em',
                      textTransform: 'uppercase', color: T.textMuted, marginBottom: '0.4rem',
                    }}>
                      Earned vs. volunteered
                    </div>
                    <p style={{ margin: 0, color: T.textBody, fontSize: '0.88rem', lineHeight: 1.55 }}>
                      {evaluation.earned_vs_volunteered_note}
                    </p>
                  </div>
                )}
              </div>
            )}

            <hr style={{ border: 'none', borderTop: `1px solid ${T.cardBorder}`, margin: '0 0 1.25rem 0' }} />

            {/* ── Conversation transcript (collapsible) ── */}
            <Collapsible label="Conversation transcript">
              <p style={{ color: T.textFaint, fontSize: '0.87rem', margin: '0 0 0.75rem 0' }}>
                Full dialogue from the selected interview.
              </p>
              {turns && turns.length > 0 ? turns.map((t: any) => (
                <div key={t.turn_id} style={{ marginBottom: '0.85rem' }}>
                  <p style={{ color: T.textHeading, fontWeight: 600, margin: '0 0 0.2rem 0', fontSize: '0.88rem' }}>
                    Turn {t.turn_id} — {t.speaker}
                  </p>
                  <p style={{
                    marginLeft: '0.75rem', padding: '0.6rem 0.85rem',
                    borderLeft: `3px solid ${T.divider}`,
                    background: T.pageBg, borderRadius: '0 8px 8px 0',
                    color: T.textBody, lineHeight: 1.55, fontSize: '0.9rem', margin: '0 0 0 0.75rem',
                  }}>
                    {t.text}
                  </p>
                </div>
              )) : (
                <p style={{ color: T.textFaint, fontSize: '0.88rem' }}>Transcript not available.</p>
              )}
            </Collapsible>

          </>
        )}

      </div>
    </div>
  );
}
