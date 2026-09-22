import { MONO, SERIF, T } from './theme';
import { DIMENSION_LABELS, MOMENTS_HEADING, itemReferenceVerb, momentRowLabels } from './copy';
import type { Moment } from './types';

/**
 * One short stretch of transcript — two to four turns — where something
 * consequential happened.
 *
 * This is the evidence layer of the report. It replaces the per-dimension
 * evidence quotes and "what was missed" lines, which said the same thing the
 * assessments already said. Nothing here should restate a dimension assessment.
 */

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    // Wraps rather than squeezing: below roughly 400px the fixed label column
    // would leave the quote about 120px wide, which is unreadable. The content's
    // flex-basis is what makes it drop under the label instead.
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '0.25rem 1rem',
      padding: '0.7rem 0', alignItems: 'flex-start',
    }}>
      <div style={{
        flex: '0 0 7.5rem',
        fontFamily: MONO,
        fontSize: '0.67rem',
        letterSpacing: '0.06em',
        color: T.textFaint,
        paddingTop: '0.15rem',
      }}>
        {label}
      </div>
      <div style={{
        flex: '1 1 16rem', minWidth: 0,
        color: T.textBody, fontSize: '0.92rem', lineHeight: 1.6,
      }}>
        {children}
      </div>
    </div>
  );
}

function MomentCard({ moment, index, firstName }: { moment: Moment; index: number; firstName: string }) {
  const labels = momentRowLabels(firstName);

  return (
    <div style={{
      border: `1px solid ${T.cardBorder}`,
      borderRadius: 10,
      overflow: 'hidden',
      marginBottom: '1rem',
      background: T.cardBg,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'baseline',
        gap: '0.7rem',
        padding: '0.8rem 1.1rem',
        background: '#f5f5f4',
        borderBottom: `1px solid ${T.cardBorder}`,
      }}>
        <span style={{ fontFamily: MONO, fontSize: '0.78rem', color: T.teal }}>
          {String(index + 1).padStart(2, '0')}
        </span>
        <span style={{ fontWeight: 700, fontSize: '0.97rem', color: T.textPrimary, flex: 1 }}>
          {moment.headline}
        </span>
        <span style={{
          fontFamily: MONO,
          fontSize: '0.64rem',
          letterSpacing: '0.06em',
          color: T.textFaint,
          textTransform: 'uppercase',
          flexShrink: 0,
        }}>
          {DIMENSION_LABELS[moment.dimension] ?? moment.dimension}
        </span>
      </div>

      <div style={{ padding: '0.4rem 1.1rem 1rem' }}>
        {moment.persona_offered && (
          <Row label={labels.personaOffered}>{moment.persona_offered}</Row>
        )}

        <Row label={labels.youSaid}>
          <span style={{ fontFamily: SERIF, fontStyle: 'italic', color: T.textPrimary }}>
            &ldquo;{moment.student_quote}&rdquo;
          </span>
        </Row>

        <Row label={labels.produced[moment.produced_label] ?? labels.produced.you_learned}>
          {moment.what_it_produced}
        </Row>

        <Row label={labels.outcome[moment.outcome_kind] ?? labels.outcome.out_of_reach}>
          {/* The item name and its verb come from the coverage grade resolved
              server-side, so this line can never contradict the second tab. */}
          {moment.out_of_reach_item && (
            <>
              <strong style={{ color: T.textPrimary }}>{moment.out_of_reach_item}</strong>
              {' '}{itemReferenceVerb(moment.out_of_reach_state)}.{' '}
            </>
          )}
          {moment.outcome}
        </Row>

        <Row label={labels.tryInstead}>
          <div style={{
            background: T.tealBg,
            borderLeft: `3px solid ${T.teal}`,
            borderRadius: '0 8px 8px 0',
            padding: '0.6rem 0.9rem',
          }}>
            <div style={{ fontWeight: 700, color: T.textPrimary, marginBottom: 3 }}>
              {moment.technique_name}
            </div>
            <div style={{ fontFamily: SERIF, fontStyle: 'italic', color: T.textBody }}>
              &ldquo;{moment.technique_stem}&rdquo;
            </div>
          </div>
        </Row>
      </div>
    </div>
  );
}

export default function Moments({ moments, firstName }: { moments: Moment[]; firstName: string }) {
  // Three when the transcript supports three. A short interview legitimately
  // yields fewer, and padding it would mean inventing an exchange.
  if (!moments.length) return null;

  return (
    <section style={{ marginBottom: '1.5rem' }}>
      <h2 style={{
        color: T.textHeading, fontWeight: 700, fontSize: '1.05rem', margin: '0 0 0.9rem 0',
      }}>
        {MOMENTS_HEADING}
      </h2>
      {moments.map((moment, i) => (
        <MomentCard key={i} moment={moment} index={i} firstName={firstName} />
      ))}
    </section>
  );
}
