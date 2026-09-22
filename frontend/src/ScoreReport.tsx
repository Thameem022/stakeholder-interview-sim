import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ChevronDown, ChevronUp } from 'lucide-react';

import HowYouInterviewed from './score/HowYouInterviewed';
import WhatYouLearned from './score/WhatYouLearned';
import { MONO, PAGE_MAX, T } from './score/theme';
import { TAB_LABELS, TRANSCRIPT_LABEL, personaCopy } from './score/copy';
import { itemState } from './score/types';
import type { SessionEvaluation, TierCoverage } from './score/types';

export type { SessionEvaluation } from './score/types';

/**
 * Post-interview report shell: two tabs, a count badge, and the transcript.
 *
 * The tabs are named in plain English. IQR and SIC are internal names — they
 * stay in the database, the prompts and the instructor materials, and never
 * reach a student.
 */

/** Boxes the student opened, across every tier. Drives the second tab's badge. */
function openedCount(tiers: TierCoverage[] | undefined): { opened: number; total: number } {
  let opened = 0;
  let total = 0;
  for (const tier of tiers ?? []) {
    for (const item of tier.items ?? []) {
      total += 1;
      if (itemState(item) !== 'not_opened') opened += 1;
    }
  }
  return { opened, total };
}

function Collapsible({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{
      background: T.cardBg, border: `1px solid ${T.cardBorder}`,
      borderRadius: 10, overflow: 'hidden', marginTop: '1.5rem',
    }}>
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '0.75rem 1.15rem', background: '#f5f5f4', border: 'none', cursor: 'pointer',
          color: T.textHeading, fontSize: '0.9rem', fontWeight: 600, gap: 8,
        }}
      >
        <span>{label}</span>
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
      </button>
      {open && (
        <div style={{ padding: '1.15rem', borderTop: `1px solid ${T.cardBorder}` }}>{children}</div>
      )}
    </div>
  );
}

interface ScoreReportProps {
  evaluation: SessionEvaluation;
  onClose?: () => void;
}

type Tab = 'interview' | 'learned';

export default function ScoreReport({ evaluation, onClose }: ScoreReportProps) {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<Tab>('interview');

  const turns = (evaluation.metadata as { turns?: Array<{ turn_id: number; speaker: string; text: string }> })?.turns;
  const coverage = evaluation.insight_coverage;
  const hasCoverage = Array.isArray(coverage) && coverage.length > 0;
  const { opened, total } = openedCount(coverage);

  const personaKey = evaluation.metadata?.persona_key;
  const persona = personaCopy(typeof personaKey === 'string' ? personaKey : undefined);

  const tabs: Tab[] = hasCoverage ? ['interview', 'learned'] : ['interview'];

  return (
    <div style={{
      minHeight: '100vh',
      background: T.pageBg,
      fontFamily: "'Segoe UI', system-ui, -apple-system, sans-serif",
    }}>
      {/* The column is the reading measure; cards fill it edge to edge. */}
      <div style={{
        maxWidth: PAGE_MAX,
        margin: '0 auto',
        padding: '2rem clamp(1rem, 3vw, 2.5rem) 4rem',
      }}>

        <button
          onClick={() => (onClose ? onClose() : navigate('/'))}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: 'none', border: 'none', cursor: 'pointer',
            color: T.textMuted, fontSize: '0.86rem', fontWeight: 600,
            marginBottom: '1.5rem', padding: 0,
          }}
        >
          <ArrowLeft size={15} />
          Back to interview
        </button>

        {/* Session header — the persona's actual job title, not their archetype. */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
          gap: '1rem', flexWrap: 'wrap',
          paddingBottom: '0.7rem', borderBottom: `1px solid ${T.divider}`, marginBottom: '1.25rem',
        }}>
          <span style={{
            fontFamily: MONO, fontSize: '0.7rem', letterSpacing: '0.09em',
            textTransform: 'uppercase', color: T.textFaint,
          }}>
            {persona.fullName}
            {persona.roleTitle ? ` · ${persona.roleTitle}` : ''}
          </span>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: 4, marginBottom: '1.5rem', flexWrap: 'wrap' }}>
          {tabs.map(tab => {
            const active = activeTab === tab;
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 8,
                  padding: '0.55rem 1.1rem',
                  borderRadius: 8,
                  border: `1px solid ${active ? T.cardBorder : 'transparent'}`,
                  borderBottomColor: active ? T.cardBg : 'transparent',
                  background: active ? T.cardBg : 'transparent',
                  color: active ? T.textPrimary : T.textFaint,
                  fontWeight: active ? 700 : 600,
                  fontSize: '0.9rem',
                  cursor: 'pointer',
                }}
              >
                {TAB_LABELS[tab]}
                {/* A reason to click the second tab. */}
                {tab === 'learned' && total > 0 && (
                  <span style={{
                    fontFamily: MONO, fontSize: '0.7rem',
                    background: active ? '#f5f5f4' : T.cardBorder,
                    color: T.textMuted, borderRadius: 5, padding: '0.1rem 0.4rem',
                  }}>
                    {opened} / {total}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {activeTab === 'interview' && (
          <HowYouInterviewed
            evaluation={evaluation}
            firstName={persona.firstName}
            their={persona.their}
            onGoToLearned={hasCoverage ? () => setActiveTab('learned') : undefined}
          />
        )}

        {activeTab === 'learned' && hasCoverage && (
          <WhatYouLearned
            tiers={coverage!}
            firstName={persona.firstName}
            their={persona.their}
          />
        )}

        <Collapsible label={TRANSCRIPT_LABEL}>
          {turns && turns.length > 0 ? turns.map(t => (
            <div key={t.turn_id} style={{ marginBottom: '0.85rem' }}>
              <p style={{
                color: T.textHeading, fontWeight: 600, margin: '0 0 0.2rem 0', fontSize: '0.85rem',
              }}>
                {t.speaker}
              </p>
              <p style={{
                margin: 0, padding: '0.55rem 0.85rem',
                borderLeft: `3px solid ${T.divider}`,
                background: T.pageBg, borderRadius: '0 8px 8px 0',
                color: T.textBody, lineHeight: 1.6, fontSize: '0.89rem',
              }}>
                {t.text}
              </p>
            </div>
          )) : (
            <p style={{ color: T.textFaint, fontSize: '0.87rem', margin: 0 }}>
              Transcript not available.
            </p>
          )}
        </Collapsible>

      </div>
    </div>
  );
}
