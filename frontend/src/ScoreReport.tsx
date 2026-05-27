import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ChevronDown,
  ChevronUp,
  ClipboardList,
  Search,
  Ear,
  HeartHandshake,
  Scale,
  AlertTriangle,
  ArrowLeft,
  BookOpen,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Types matching the IQR SessionEvaluation schema
// ---------------------------------------------------------------------------

export interface IQREvidence {
  turn_id: number;
  student_quote: string;
  stakeholder_cue: string;
  alternative_phrasing?: string | null;
}

export interface IQREvaluation {
  dimension_id: string;
  dimension_name: string;
  score: number;
  skill_level_title: string;
  label: string;
  rationale: string;
  line_of_inquiry_impact?: string | null;
  evidence: IQREvidence;
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

export interface SICItem {
  chunk_id: string;
  domain: string;
  type?: 'fact' | 'signal';
  fact_summary: string;
  suggested_follow_up: string;
  elicited: boolean;
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
  items?: SICItem[];
}

export interface SessionEvaluation {
  metadata: IQRMetadata;
  evaluation_results: IQREvaluation[];
  overall_summary: string;
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

function skillTitleFromScore(score: number): string {
  if (score >= 10)  return 'Master Stakeholder Partner';
  if (score >= 9)   return 'Advanced Systems Interviewer';
  if (score >= 8)   return 'Competent Operational Interviewer';
  if (score >= 7)   return 'Emerging Technical Interviewer';
  if (score >= 6)   return 'Novice Fact-Finder';
  if (score >= 5)   return 'Developing Interviewer';
  if (score >= 4)   return 'Developing Questioner';
  if (score >= 3)   return 'Surface-Level Asker';
  return 'Pre-Novice Learner';
}

function meanScore(results: IQREvaluation[]): number {
  if (!results.length) return 0;
  return results.reduce((s, r) => s + r.score, 0) / results.length;
}

// ---------------------------------------------------------------------------
// Skill level descriptions (one sentence per band — from dashboard.py)
// ---------------------------------------------------------------------------

const SKILL_LEVEL_DESCRIPTIONS: Record<string, string> = {
  'Pre-Novice Learner':
    'Minimal structured questioning; relies entirely on yes/no or leading questions with no attempt to probe or acknowledge stakeholder cues.',
  'Surface-Level Asker':
    'Asks basic factual questions but shows no follow-through; misses all emotional, ethical, and technical cues with responses disconnected from stakeholder answers.',
  'Developing Questioner':
    'Shows some intent to gather information but lacks consistent probing; follow-up questions are rare and off-target with rapport and active listening largely absent.',
  'Developing Interviewer':
    'Basic question structure is present but major weaknesses remain in probing depth, active listening, and ethical neutrality; stakeholder cues are frequently missed.',
  'Novice Fact-Finder':
    'A novice fact-finder can collect surface-level information but relies heavily on closed questions, rarely probes beyond the obvious, and tends to capture what happened without uncovering why.',
  'Emerging Technical Interviewer':
    'Demonstrates growing command of open-ended questions and targeted probes, though depth and consistency still vary across topics.',
  'Competent Operational Interviewer':
    'Reliably structures sessions, uses probing effectively, and surfaces most relevant details; minor gaps remain in rapport and adaptive follow-up.',
  'Advanced Systems Interviewer':
    'Combines strong technique with contextual awareness, consistently drawing out root causes and stakeholder perspectives with minimal wasted turns.',
  'Master Stakeholder Partner':
    'Fluently adapts to any interview context, builds genuine rapport, and extracts nuanced insight that less experienced interviewers routinely miss.',
};

// ---------------------------------------------------------------------------
// Glossary
// ---------------------------------------------------------------------------

const GLOSSARY = [
  {
    term: 'Leading question',
    definition:
      "A question that subtly steers the respondent toward a particular answer by embedding an assumption or preferred outcome (e.g., 'You were satisfied with the process, weren't you?'). Leading questions bias the data and should be replaced with neutral, open-ended alternatives.",
  },
  {
    term: 'Double-barreled question',
    definition:
      "A single question that asks about two separate issues at once (e.g., 'Was the process clear and did you feel supported?'). The respondent cannot answer both parts accurately in one reply; split them into two distinct questions.",
  },
  {
    term: 'Closed question',
    definition:
      "A question that invites only a yes/no or single-word answer, limiting the information gathered (e.g., 'Did you attend the meeting?'). Use closed questions sparingly—mainly to confirm facts.",
  },
  {
    term: 'Open-ended question',
    definition:
      "A question that invites the respondent to elaborate freely (e.g., 'Can you walk me through what happened?'). Open-ended questions are the primary tool for uncovering context, reasoning, and detail.",
  },
  {
    term: 'Probing question',
    definition:
      "A follow-up question that digs deeper into a previous response to surface root causes, nuance, or unstated assumptions (e.g., 'What led you to that conclusion?'). Effective probing distinguishes strong interviewers from those who merely collect facts.",
  },
  {
    term: 'Rapport-building',
    definition:
      'Techniques used to establish trust and psychological safety with the interviewee—such as acknowledging responses, using the person\'s name, or briefly normalising difficult topics—so that they feel comfortable sharing openly.',
  },
];

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DIMENSION_ORDER = [
  'Question Formulation',
  'Probing Quality',
  'Active Listening',
  'Rapport-Building',
  'Ethical Conduct',
];

const DIMENSION_ICONS: Record<string, React.ReactNode> = {
  'Question Formulation': <ClipboardList size={16} />,
  'Probing Quality':      <Search        size={16} />,
  'Active Listening':     <Ear           size={16} />,
  'Rapport-Building':     <HeartHandshake size={16} />,
  'Ethical Conduct':      <Scale         size={16} />,
};

const DIMENSION_EMOJI: Record<string, string> = {
  'Question Formulation': '📋',
  'Probing Quality':      '🔍',
  'Active Listening':     '👂',
  'Rapport-Building':     '🤝',
  'Ethical Conduct':      '⚖️',
};

// ── Status colour tokens ──
// `not_accessed_insufficient_framing` is the developmental gray.
// `not_accessed_appropriate_restraint` is soft slate — indicates in-character
// professional restraint, not a student failure.
const STATUS_COLOR: Record<TierCoverageStatus, {
  fg: string; deep: string; track: string; bg: string; border: string;
}> = {
  full:                                 { fg: T.greenAccent, deep: T.greenDeep, track: '#d1fae5', bg: '#f0fdf4', border: '#34d399' },
  partial:                              { fg: T.amberAccent, deep: T.amberDeep, track: '#fef3c7', bg: '#fffbeb', border: '#fbbf24' },
  not_accessed_insufficient_framing:    { fg: '#9ca3af',     deep: '#6b7280',   track: '#f3f4f6', bg: '#fafaf9', border: '#d1d5db' },
  not_accessed_appropriate_restraint:   { fg: '#64748b',     deep: '#475569',   track: '#e2e8f0', bg: '#f8fafc', border: '#94a3b8' },
  not_accessed:                         { fg: '#9ca3af',     deep: '#6b7280',   track: '#f3f4f6', bg: '#fafaf9', border: '#d1d5db' },
};

const STATUS_LABEL: Record<TierCoverageStatus, string> = {
  full:                                 'Full',
  partial:                              'Partial',
  not_accessed_insufficient_framing:    'Not accessed — framing gap',
  not_accessed_appropriate_restraint:   'Not accessed — appropriate restraint',
  not_accessed:                         'Not accessed',
};

function isNotAccessed(status: TierCoverageStatus): boolean {
  return status === 'not_accessed'
    || status === 'not_accessed_insufficient_framing'
    || status === 'not_accessed_appropriate_restraint';
}

const CREDIT_MODE_LABEL: Record<string, string> = {
  explicit_acknowledgment: 'Explicit acknowledgment',
  indirect_acknowledgment: 'Indirect acknowledgment',
  reflective_silence:      'Reflective silence (credit for framing)',
  explicit:                'Captured',
};

// ── SVG donut chart ──
function DonutChart({
  pct,
  status,
  size = 80,
  cuesTotal = 4,
  tier,
}: {
  pct: number;
  status: TierCoverageStatus;
  size?: number;
  cuesTotal?: number;
  tier?: number;
}) {
  const c = STATUS_COLOR[status];
  const strokeW = 7;
  const r = (size - strokeW * 2) / 2;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const cx = size / 2;
  const cy = size / 2;
  const fontSize = size <= 72 ? '1rem' : '1.1rem';

  // For Tier 3 we render a qualitative glyph rather than a misleading "0/N"
  // count, because Tier 3 omissions can be in-character professional restraint
  // (not a missed quota of extractable facts).
  const isAppropriateRestraint = status === 'not_accessed_appropriate_restraint';
  const centerLabel = pct > 0
    ? `${Math.round(pct)}%`
    : (tier === 3 ? (isAppropriateRestraint ? '·' : '—') : `0/${cuesTotal}`);

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: 'rotate(-90deg)', display: 'block' }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke={c.track} strokeWidth={strokeW} />
        {pct > 0 && (
          <circle
            cx={cx} cy={cy} r={r} fill="none"
            stroke={c.fg} strokeWidth={strokeW}
            strokeDasharray={`${dash} ${circ}`}
            strokeLinecap="round"
          />
        )}
      </svg>
      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        lineHeight: 1.1,
      }}>
        <span style={{ fontSize, fontWeight: 800, color: c.deep }}>
          {centerLabel}
        </span>
      </div>
    </div>
  );
}

// ── Legend dot ──
function LegendDot({ status }: { status: TierCoverageStatus }) {
  const c = STATUS_COLOR[status];
  const isPartial = status === 'partial';
  const isNone    = isNotAccessed(status);
  return (
    <span style={{
      display: 'inline-block', width: 16, height: 16, borderRadius: '50%', flexShrink: 0,
      background: isNone || isPartial ? 'transparent' : c.fg,
      border: `2.5px solid ${c.fg}`,
      boxShadow: isPartial ? `inset 0 0 0 3px ${c.track}` : 'none',
      position: 'relative',
    }}>
      {isPartial && (
        <span style={{
          position: 'absolute', top: '50%', left: '50%',
          transform: 'translate(-50%,-50%)',
          width: 7, height: 7, borderRadius: '50%',
          background: c.fg,
          display: 'block',
        }} />
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Cue Heatmap — per-item view of which SIC cues were elicited vs. missed
// Rows = tiers, columns = individual cues within that tier.
// Filled square = elicited; outlined square = missed. Click a square to
// inspect the cue and (for elicited cues) the verbatim student quote.
// ---------------------------------------------------------------------------

// Map a SIC item's state to (background, border, accessible label) for the heatmap cell.
function cellAppearance(item: SICItem): { bg: string; border: string; label: string } {
  if (item.elicited) {
    // Surfaced — colour by credit mode if available.
    switch (item.credit_mode) {
      case 'explicit_acknowledgment':
        return { bg: T.greenAccent, border: T.greenDeep, label: 'Explicit acknowledgment' };
      case 'indirect_acknowledgment':
        return { bg: T.amberAccent, border: T.amberDeep, label: 'Indirect acknowledgment' };
      case 'reflective_silence':
        return { bg: '#94a3b8', border: '#475569', label: 'Reflective silence — credit for framing' };
      case 'explicit':
      default:
        return { bg: T.greenAccent, border: T.greenDeep, label: 'Elicited' };
    }
  }
  // Not surfaced — distinguish framing gap vs appropriate restraint for Tier 3 signals.
  if (item.type === 'signal' && item.omission_classification === 'appropriate_non_disclosure') {
    return { bg: '#f8fafc', border: '#94a3b8', label: 'Appropriate restraint — in-character' };
  }
  return { bg: '#fafaf9', border: T.redAccent, label: 'Framing gap — would have benefited from different framing' };
}

function CueHeatmap({ tiers }: { tiers: TierCoverage[] }) {
  const sorted = [...tiers].sort((a, b) => a.tier - b.tier);
  const [selected, setSelected] = useState<{ tier: number; idx: number } | null>(null);

  // Surface only tiers that include per-item data; if none do, render nothing.
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
      {/* ── Header ── */}
      <div style={{
        padding: '1rem 1.5rem',
        borderBottom: `1px solid ${T.cardBorder}`,
        background: '#f5f5f4',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem',
      }}>
        <div>
          <h3 style={{ margin: 0, fontWeight: 700, fontSize: '1.05rem', color: T.textHeading }}>
            Cue Heatmap
          </h3>
          <p style={{ margin: '0.2rem 0 0', fontSize: '0.83rem', color: T.textMuted }}>
            Each square is one knowledge cue. Click to see the cue and your evidence.
          </p>
        </div>
        {/* Legend — five states for the disclosure-aware schema */}
        <div style={{ display: 'flex', gap: '0.9rem', alignItems: 'center', flexShrink: 0, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 4,
              background: T.greenAccent, border: `1.5px solid ${T.greenDeep}` }} />
            <span style={{ fontSize: '0.78rem', color: T.textMuted, fontWeight: 600 }}>Explicit</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 4,
              background: T.amberAccent, border: `1.5px solid ${T.amberDeep}` }} />
            <span style={{ fontSize: '0.78rem', color: T.textMuted, fontWeight: 600 }}>Indirect</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 4,
              background: '#94a3b8', border: `1.5px solid #475569` }} />
            <span style={{ fontSize: '0.78rem', color: T.textMuted, fontWeight: 600 }}>Reflective silence</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 4,
              background: '#f8fafc', border: `1.5px solid #94a3b8` }} />
            <span style={{ fontSize: '0.78rem', color: T.textMuted, fontWeight: 600 }}>Appropriate restraint</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 4,
              background: '#fafaf9', border: `1.5px dashed ${T.redAccent}` }} />
            <span style={{ fontSize: '0.78rem', color: T.textMuted, fontWeight: 600 }}>Framing gap</span>
          </div>
        </div>
      </div>

      {/* ── Grid ── */}
      <div style={{ padding: '1rem 1.5rem' }}>
        {sorted.map(tier => {
          const items = tier.items ?? [];
          if (items.length === 0) return null;
          return (
            <div key={tier.tier} style={{
              display: 'grid',
              gridTemplateColumns: '200px 1fr 90px',
              gap: '1rem',
              alignItems: 'center',
              padding: '0.5rem 0',
              borderTop: `1px solid ${T.cardBorder}`,
            }}>
              {/* Tier label */}
              <div style={{ fontSize: '0.85rem', color: T.textHeading, fontWeight: 600, lineHeight: 1.3 }}>
                Tier {tier.tier}
                <div style={{ fontSize: '0.74rem', color: T.textFaint, fontWeight: 500 }}>
                  {tier.title}
                </div>
              </div>

              {/* Cells */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {items.map((item, idx) => {
                  const isSel = selected?.tier === tier.tier && selected?.idx === idx;
                  const appearance = cellAppearance(item);
                  const isAppropriateRestraint = !item.elicited
                    && item.type === 'signal'
                    && item.omission_classification === 'appropriate_non_disclosure';
                  return (
                    <button
                      key={item.chunk_id}
                      type="button"
                      onClick={() => setSelected(isSel ? null : { tier: tier.tier, idx })}
                      aria-label={`Cue ${idx + 1}: ${appearance.label} — ${item.fact_summary}`}
                      aria-pressed={isSel}
                      style={{
                        width: 28, height: 28, borderRadius: 5,
                        background: appearance.bg,
                        border: item.elicited || isAppropriateRestraint
                          ? `1.5px solid ${appearance.border}`
                          : `1.5px dashed ${appearance.border}`,
                        boxShadow: isSel ? '0 0 0 2px #1c1917, 0 0 0 4px rgba(28,25,23,0.15)' : 'none',
                        cursor: 'pointer', padding: 0,
                        transition: 'box-shadow 0.15s ease, transform 0.1s ease',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.transform = 'scale(1.08)')}
                      onMouseLeave={e => (e.currentTarget.style.transform = 'scale(1.0)')}
                    />
                  );
                })}
              </div>

              {/* Tier count */}
              <div style={{ fontSize: '0.85rem', color: T.textMuted, fontWeight: 600, textAlign: 'right' }}>
                {tier.cues_found}/{tier.cues_total}
              </div>
            </div>
          );
        })}
      </div>

      {/* ── Detail panel ── */}
      {sel && selTier && (() => {
        const isSignal = sel.type === 'signal';
        const isAppropriateRestraint = !sel.elicited && isSignal && sel.omission_classification === 'appropriate_non_disclosure';
        const isFramingGap            = !sel.elicited && (!isSignal || sel.omission_classification === 'insufficient_framing' || sel.omission_classification == null);

        const panelBg = sel.elicited
          ? T.greenBg2
          : isAppropriateRestraint ? '#f8fafc' : T.redBg2;

        const stateLabel = sel.elicited
          ? (sel.credit_mode && CREDIT_MODE_LABEL[sel.credit_mode]) || 'Elicited'
          : (isAppropriateRestraint ? 'Appropriate restraint — in-character' : 'Framing gap');

        return (
        <div style={{
          padding: '1rem 1.5rem 1.25rem',
          borderTop: `1px solid ${T.cardBorder}`,
          background: panelBg,
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

          {/* When elicited, show the student quote that earned the credit. */}
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

          {/* What the student did right (any item with surfacing cues used). */}
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

          {/* For appropriate restraint: affirming copy, NOT a "try this next time" tip. */}
          {isAppropriateRestraint && (
            <div style={{
              borderLeft: `3px solid #94a3b8`,
              padding: '0.4rem 0.8rem',
              background: 'white', borderRadius: '0 8px 8px 0',
              fontSize: '0.88rem', color: T.textBody, lineHeight: 1.55,
            }}>
              <span style={{ fontWeight: 700, color: '#475569' }}>In-character restraint: </span>
              Alex maintained appropriate institutional restraint here. Your framing was respectful — this is not a missed quota.
            </div>
          )}

          {/* For framing gaps on signal items: show what would have surfaced this. */}
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

          {/* For framing gaps on plain Tier 1/2 facts: keep the suggested follow-up — these are extraction-shaped by design. */}
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
}

function InsightCoveragePanel({ tiers }: { tiers: TierCoverage[] }) {
  const sorted = [...tiers].sort((a, b) => a.tier - b.tier);

  // Build the legend dynamically from the actual statuses present in this session.
  const presentStatuses: TierCoverageStatus[] = [];
  for (const t of sorted) {
    if (!presentStatuses.includes(t.status)) presentStatuses.push(t.status);
  }
  const legendOrder: TierCoverageStatus[] = [
    'full',
    'partial',
    'not_accessed_appropriate_restraint',
    'not_accessed_insufficient_framing',
    'not_accessed',
  ];
  const legendStatuses = legendOrder.filter(s => presentStatuses.includes(s));

  return (
    <div style={{
      background: T.cardBg,
      border: `1px solid ${T.cardBorder}`,
      borderRadius: 14,
      overflow: 'hidden',
      marginBottom: '1.35rem',
      boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
    }}>

      {/* ── Header ── */}
      <div style={{
        padding: '1rem 1.5rem',
        borderBottom: `1px solid ${T.cardBorder}`,
        background: '#f5f5f4',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem',
      }}>
        <div>
          <h3 style={{ margin: 0, fontWeight: 700, fontSize: '1.05rem', color: T.textHeading }}>
            Insight Coverage Summary
          </h3>
          <p style={{ margin: '0.2rem 0 0', fontSize: '0.83rem', color: T.textMuted }}>
            Which tiers of stakeholder knowledge you accessed this session
          </p>
        </div>
        {/* Legend */}
        <div style={{ display: 'flex', gap: '1.25rem', alignItems: 'center', flexShrink: 0, flexWrap: 'wrap' }}>
          {legendStatuses.map(s => (
            <div key={s} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <LegendDot status={s} />
              <span style={{ fontSize: '0.82rem', color: T.textMuted, fontWeight: 600 }}>
                {STATUS_LABEL[s]}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Tier rows ── */}
      {sorted.map((tier, idx) => {
        const c = STATUS_COLOR[tier.status];
        return (
          <div key={tier.tier} style={{
            display: 'grid',
            gridTemplateColumns: '260px 1fr 300px',
            gap: '1.25rem',
            alignItems: 'center',
            padding: '1.25rem 1.5rem',
            borderTop: idx > 0 ? `1px solid ${T.cardBorder}` : 'none',
            background: c.bg,
          }}>

            {/* Col 1 — tier name + description */}
            <div>
              <div style={{ fontWeight: 700, fontSize: '0.97rem', color: T.textHeading }}>
                Tier {tier.tier}: {tier.title}{' '}
                <span style={{ fontWeight: 500, color: T.textFaint }}>({tier.category})</span>
              </div>
              <div style={{ fontSize: '0.8rem', color: T.textMuted, marginTop: '0.3rem' }}>
                • {tier.description}
              </div>
            </div>

            {/* Col 2 — donut + bar + tag */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '1.1rem' }}>
              <DonutChart pct={tier.percentage} status={tier.status} size={76} cuesTotal={tier.cues_total} tier={tier.tier} />
              <div style={{ flex: 1, minWidth: 0 }}>
                {/* Percentage + status label */}
                <div style={{ fontWeight: 700, fontSize: '1rem', color: c.deep, marginBottom: '0.35rem' }}>
                  {tier.tier !== 3 || tier.percentage > 0 ? `${tier.percentage}% ` : ''}
                  <span style={{ fontWeight: 600, fontSize: '0.88rem', color: T.textMuted }}>
                    {STATUS_LABEL[tier.status]}
                  </span>
                </div>
                {/* Progress bar */}
                <div style={{
                  height: 7, borderRadius: 99,
                  background: c.track, overflow: 'hidden',
                  marginBottom: '0.5rem',
                }}>
                  <div style={{
                    height: '100%', borderRadius: 99,
                    width: `${tier.percentage}%`,
                    background: c.fg,
                    transition: 'width 0.6s ease',
                  }} />
                </div>
                {/* Cue tag — quantitative for Tier 1/2 only; Tier 3 uses qualitative status */}
                {tier.skill_label && tier.tier !== 3 && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.76rem', color: c.deep, fontWeight: 600,
                    border: `1.5px dashed ${c.border}`,
                    borderRadius: 6, padding: '3px 8px',
                    background: 'white', cursor: 'default',
                  }}>
                    {tier.skill_label} ({tier.cues_found} of {tier.cues_total} cues found)
                  </div>
                )}
                {isNotAccessed(tier.status) && tier.tier !== 3 && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.76rem', color: c.deep, fontWeight: 600,
                    border: `1.5px dashed ${c.border}`,
                    borderRadius: 6, padding: '3px 8px',
                    background: 'white',
                  }}>
                    {tier.cues_found}/{tier.cues_total} cues found
                  </div>
                )}
                {/* Tier 3: qualitative tag — never "0/N" framing */}
                {tier.tier === 3 && tier.status === 'not_accessed_appropriate_restraint' && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.76rem', color: c.deep, fontWeight: 700,
                    border: `1.5px solid ${c.border}`,
                    borderRadius: 6, padding: '3px 8px',
                    background: 'white',
                  }}>
                    In-character professional restraint — not a missed quota
                  </div>
                )}
                {tier.tier === 3 && tier.status === 'not_accessed_insufficient_framing' && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.76rem', color: c.deep, fontWeight: 700,
                    border: `1.5px dashed ${c.border}`,
                    borderRadius: 6, padding: '3px 8px',
                    background: 'white',
                  }}>
                    Framing didn't yet make space for these signals
                  </div>
                )}
                {tier.tier === 3 && tier.status === 'partial' && tier.skill_label && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    fontSize: '0.76rem', color: c.deep, fontWeight: 600,
                    border: `1.5px dashed ${c.border}`,
                    borderRadius: 6, padding: '3px 8px',
                    background: 'white', cursor: 'default',
                  }}>
                    {tier.skill_label} — credit via explicit / indirect / reflective framing
                  </div>
                )}
              </div>
            </div>

            {/* Col 3 — why it matters + tip pill */}
            <div style={{
              background: 'white',
              border: `1px solid ${c.border}`,
              borderRadius: 10,
              padding: '0.75rem 0.9rem',
              fontSize: '0.8rem', color: T.textBody, lineHeight: 1.6,
            }}>
              {tier.why_it_matters && (
                <p style={{ margin: '0 0 0.55rem 0' }}>
                  <span style={{ fontWeight: 700, color: c.deep }}>Why it matters: </span>
                  {tier.why_it_matters}
                </p>
              )}
              {/* Quick win / actionable tip pill */}
              {tier.quick_win && (
                <span style={{
                  display: 'inline-block', fontSize: '0.75rem', fontWeight: 700,
                  background: T.greenBg, color: T.greenDeep,
                  border: `1px solid ${T.greenBorder}`, borderRadius: 99,
                  padding: '3px 10px',
                }}>
                  ✓ Quick Win: {tier.quick_win}
                </span>
              )}
              {tier.actionable_tip && (
                <div style={{
                  display: 'block', fontSize: '0.75rem', fontWeight: 700,
                  background: T.amberBg, color: T.amberDeep,
                  border: `1px solid ${T.amberBorder}`, borderRadius: 8,
                  padding: '6px 10px', wordBreak: 'break-word',
                }}>
                  ↗ Actionable Tip: {tier.actionable_tip}
                </div>
              )}
            </div>

          </div>
        );
      })}

    </div>
  );
}

// ---------------------------------------------------------------------------
// DimensionCard — updated structure: Assessment first, then evidence cols,
// then stakeholder cue / missed insight (matches latest dashboard.py)
// ---------------------------------------------------------------------------

function DimensionCard({ res }: { res: IQREvaluation }) {
  const score = res.score;
  const c = coachColors(score);
  const showMissedInsight = score < 9.0;
  const icon = DIMENSION_ICONS[res.dimension_name] ?? null;
  const emoji = DIMENSION_EMOJI[res.dimension_name] ?? '•';
  const missedText = res.line_of_inquiry_impact?.trim() || res.rationale?.trim() || '';

  return (
    <div style={{
      background: T.cardBg, border: `1px solid ${T.cardBorder}`,
      borderRadius: 12, marginBottom: '1.1rem',
      overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
    }}>

      {/* ── 0. Dimension header ── */}
      <div style={{
        padding: '0.85rem 1.1rem',
        borderBottom: `1px solid ${T.cardBorder}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: T.cardBg,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ color: c.accent }}>{icon}</span>
          <span style={{ fontWeight: 700, fontSize: '1.05rem', color: T.textPrimary }}>
            {emoji} {res.dimension_name}
          </span>
          <span style={{ color: c.deep, fontWeight: 700, marginLeft: 4 }}>
            — {score.toFixed(1)}/10
          </span>
          <span style={{ color: T.textMuted, fontWeight: 500, fontSize: '0.88rem' }}>
            ({res.label})
          </span>
          {res.skill_level_title && (
            <span style={{ fontSize: '0.82rem', fontWeight: 600, color: T.textFaint, marginLeft: 4 }}>
              · {res.skill_level_title}
            </span>
          )}
        </div>
      </div>

      {/* ── Score bar ── */}
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

      {/* ── 1. Assessment (rationale) — full width, shown first ── */}
      <div style={{
        background: T.cardBg,
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
          {res.rationale}
        </p>
      </div>

      {/* ── 2. What you said / Coach's suggestion — side by side ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
        {/* Left */}
        <div style={{
          padding: '1rem 1.15rem', minHeight: '8rem',
          background: c.bg,
          borderLeft: `4px solid ${c.quoteBorder}`,
          borderRight: `1px solid ${T.cardBorder}`,
          borderTop: `1px solid ${T.cardBorder}`,
        }}>
          <div style={{
            fontWeight: 800, fontSize: '0.68rem', letterSpacing: '0.07em',
            textTransform: 'uppercase', color: T.textMuted, marginBottom: '0.55rem',
          }}>
            What you said
          </div>
          <div style={{
            fontFamily: "Georgia, 'Times New Roman', serif",
            fontSize: '0.98rem', color: T.textPrimary, lineHeight: 1.6,
          }}>
            &ldquo;{res.evidence.student_quote}&rdquo;
          </div>
          <div style={{ marginTop: '0.75rem', fontSize: '0.72rem', color: T.textFaint }}>
            Turn {res.evidence.turn_id}
          </div>
        </div>

        {/* Right */}
        <div style={{
          padding: '1rem 1.15rem', minHeight: '8rem',
          background: res.evidence.alternative_phrasing ? c.bg2 : T.cardBg,
          borderLeft: `1px solid ${res.evidence.alternative_phrasing ? c.quoteBorder : T.cardBorder}`,
          borderTop: `1px solid ${T.cardBorder}`,
        }}>
          <div style={{
            fontWeight: 800, fontSize: '0.68rem', letterSpacing: '0.07em',
            textTransform: 'uppercase', color: T.textMuted, marginBottom: '0.55rem',
          }}>
            Coach&rsquo;s suggestion
          </div>
          {res.evidence.alternative_phrasing ? (
            <div style={{ color: T.textBody, lineHeight: 1.6, fontSize: '0.95rem' }}>
              {res.evidence.alternative_phrasing}
            </div>
          ) : (
            <div style={{ color: T.textGhost, fontStyle: 'italic', fontSize: '0.9rem' }}>
              No alternative phrasing for this dimension — often because the evidence highlights a strength, not a closed turn.
            </div>
          )}
        </div>
      </div>

      {/* ── 3. Stakeholder cue + Missed insight ── */}
      <div style={{
        background: T.pageBg,
        borderTop: `1px dashed ${T.divider}`,
        padding: '1rem 1.15rem 1.15rem',
      }}>
        <div style={{
          fontWeight: 700, color: T.textMuted, fontSize: '0.78rem', marginBottom: '0.4rem',
        }}>
          Stakeholder cue
        </div>
        <div style={{ color: T.textBody, lineHeight: 1.55, fontSize: '0.92rem', marginBottom: '0.85rem' }}>
          {res.evidence.stakeholder_cue}
        </div>

        {showMissedInsight && missedText && (
          <div style={{
            display: 'flex', gap: 8, alignItems: 'flex-start',
            background: T.amberBg, border: `1px solid ${T.amberBorder}`,
            borderRadius: 8, padding: '0.6rem 0.85rem',
          }}>
            <AlertTriangle size={14} style={{ color: T.amberDeep, marginTop: 2, flexShrink: 0 }} />
            <div>
              <div style={{ fontWeight: 700, color: T.amberDeep, fontSize: '0.78rem', marginBottom: 2 }}>
                Missed insight
              </div>
              <div style={{ color: T.textBody, fontSize: '0.88rem', lineHeight: 1.55 }}>
                {missedText}
              </div>
            </div>
          </div>
        )}
      </div>
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

  const results = evaluation.evaluation_results ?? [];
  const mean    = meanScore(results);
  const c       = coachColors(mean);
  const persona   = (evaluation.metadata?.persona as string) || '';
  const sessionId = (evaluation.metadata?.session_id as string) || '';
  const turns     = (evaluation.metadata as any)?.turns as any[] | undefined;
  const hasSicData = Array.isArray(evaluation.insight_coverage) && evaluation.insight_coverage.length > 0;

  // Mode skill title across dimensions
  const titles = results.map(r => r.skill_level_title?.trim()).filter(Boolean);
  const modeTitle = titles.length
    ? [...titles].sort((a, b) => titles.filter(x => x === b).length - titles.filter(x => x === a).length)[0]
    : skillTitleFromScore(mean);

  const skillDesc = SKILL_LEVEL_DESCRIPTIONS[modeTitle] || '';

  // Order dimensions
  const byName: Record<string, IQREvaluation> = {};
  for (const r of results) byName[r.dimension_name] = r;
  const ordered: IQREvaluation[] = [
    ...DIMENSION_ORDER.filter(d => d in byName).map(d => byName[d]),
    ...results.filter(r => !DIMENSION_ORDER.includes(r.dimension_name)),
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

        {/* ── Page title + toggle ── */}
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '1.25rem', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ color: T.textPrimary, fontWeight: 700, fontSize: '1.9rem', margin: '0 0 0.15rem 0' }}>
              Interview Diagnostic Coach
            </h1>
            <p style={{ color: T.textMuted, fontSize: '1rem', margin: 0 }}>
              {activeTab === 'iqr'
                ? 'Interview Quality Rubric — feedback for your next session'
                : 'Stakeholder Insight Coverage — which knowledge tiers you accessed'}
            </p>
          </div>

          {/* Toggle */}
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

        {/* ── Session skill badge ── */}
        {results.length > 0 && activeTab === 'iqr' && (
          <div style={{
            background: `linear-gradient(135deg, ${c.bg} 0%, ${c.bg2} 50%, #ffffff 100%)`,
            border: `1px solid ${T.cardBorder}`,
            borderLeft: `6px solid ${c.border}`,
            borderRadius: 14,
            padding: '1.5rem 1.75rem',
            marginBottom: '1.35rem',
            boxShadow: '0 8px 24px rgba(28, 25, 23, 0.08)',
          }}>
            <div style={{
              fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.08em',
              color: T.textFaint, textTransform: 'uppercase', marginBottom: '0.4rem',
            }}>
              Skill level
            </div>
            <div style={{ fontSize: '2rem', fontWeight: 800, color: T.textPrimary, lineHeight: 1.2, letterSpacing: '-0.02em' }}>
              <span style={{ color: c.deep }}>{mean.toFixed(1)}/10</span>
              <span style={{ color: T.textGhost, fontWeight: 600 }}> — </span>
              <span>{modeTitle}</span>
            </div>
            {skillDesc && (
              <p style={{ color: T.textMuted, fontSize: '0.93rem', lineHeight: 1.6, margin: '0.6rem 0 0 0' }}>
                {skillDesc}
              </p>
            )}
          </div>
        )}

        {/* ══════════════ SIC TAB ══════════════ */}
        {activeTab === 'sic' && hasSicData && (
          <>
            <CueHeatmap tiers={evaluation.insight_coverage!} />
            <InsightCoveragePanel tiers={evaluation.insight_coverage!} />
          </>
        )}

        {/* ══════════════ IQR TAB ══════════════ */}
        {activeTab === 'iqr' && (
          <>
            {/* ── Session info + Overall summary ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1.5rem' }}>
              <div style={{
                background: T.cardBg, border: `1px solid ${T.cardBorder}`,
                borderRadius: 12, padding: '1.25rem',
                boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
              }}>
                <h3 style={{ color: T.textHeading, fontWeight: 600, margin: '0 0 0.75rem 0', fontSize: '1rem' }}>Session</h3>
                {persona && (
                  <p style={{ color: T.textMuted, margin: '0 0 0.4rem 0', fontSize: '0.9rem' }}>
                    <strong>Persona:</strong> {persona}
                  </p>
                )}
                {sessionId && (
                  <p style={{ color: T.textMuted, margin: 0, fontSize: '0.9rem' }}>
                    <strong>Session:</strong>{' '}
                    <code style={{ fontSize: '0.78rem', background: '#f5f5f4', padding: '1px 6px', borderRadius: 4 }}>
                      {sessionId.slice(0, 8)}…
                    </code>
                  </p>
                )}
              </div>

              <div style={{
                background: T.cardBg, border: `1px solid ${T.cardBorder}`,
                borderRadius: 12, padding: '1.25rem',
                boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
              }}>
                <h3 style={{ color: T.textHeading, fontWeight: 600, margin: '0 0 0.75rem 0', fontSize: '1rem' }}>Overall summary</h3>
                <p style={{ color: T.textMuted, lineHeight: 1.65, fontSize: '0.9rem', margin: 0 }}>
                  {evaluation.overall_summary}
                </p>
              </div>
            </div>

            {/* ── Dimension diagnostics ── */}
            <h2 style={{ color: T.textHeading, fontWeight: 600, fontSize: '1.1rem', margin: '0 0 1rem 0' }}>
              Dimension diagnostics
            </h2>
            {ordered.map(res => <DimensionCard key={res.dimension_id} res={res} />)}

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

        {/* ── Glossary (collapsible) ── */}
        <Collapsible label="Glossary — interview technique terms" icon={<BookOpen size={15} />}>
          <p style={{ color: T.textFaint, fontSize: '0.87rem', margin: '0 0 0.75rem 0' }}>
            Definitions for common terms that may appear in coach feedback above.
          </p>
          {GLOSSARY.map((entry, i) => (
            <div key={entry.term} style={{
              borderTop: i > 0 ? `1px solid ${T.cardBorder}` : 'none',
              marginTop: i > 0 ? '0.85rem' : 0,
              paddingTop: i > 0 ? '0.85rem' : 0,
            }}>
              <span style={{ fontWeight: 700, color: T.textHeading, fontSize: '0.95rem' }}>
                {entry.term}
              </span>
              <p style={{ color: T.textBody, lineHeight: 1.65, margin: '0.3rem 0 0 0', fontSize: '0.91rem' }}>
                {entry.definition}
              </p>
            </div>
          ))}
        </Collapsible>
          </>
        )}
        {/* ══ end tabs ══ */}

      </div>
    </div>
  );
}
