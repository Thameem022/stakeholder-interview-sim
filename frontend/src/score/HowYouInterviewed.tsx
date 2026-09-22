import { MONO, T, scoreRamp } from './theme';
import {
  ASSESSMENT_HEADING,
  DIMENSION_DISPLAY_ORDER,
  DIMENSION_EMOJI,
  DIMENSION_LABELS,
  dimensionSubheads,
  handOff,
  scoreExplainer,
} from './copy';
import Moments from './Moments';
import type { DimensionAssessment, SessionEvaluation } from './types';

/**
 * The first tab: how the student ran the interview.
 *
 * Score, four dimension assessments, three moments, hand-off. No coaching strip,
 * no overall summary, no depth note — every one of those restated the
 * assessments below them, which is the duplication this layout removes.
 */

function DimensionCard({ res, subhead }: { res: DimensionAssessment; subhead: string }) {
  const ramp = scoreRamp(res.score);

  return (
    <div style={{
      background: T.cardBg,
      border: `1px solid ${T.cardBorder}`,
      borderRadius: 10,
      marginBottom: '0.9rem',
      overflow: 'hidden',
    }}>
      <div style={{ padding: '0.9rem 1.15rem 0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span aria-hidden="true">{DIMENSION_EMOJI[res.dimension] ?? '•'}</span>
          <span style={{ fontWeight: 700, fontSize: '1rem', color: T.textPrimary, flex: 1 }}>
            {DIMENSION_LABELS[res.dimension] ?? res.dimension}
          </span>
          <span style={{
            fontFamily: MONO, fontSize: '1rem', fontWeight: 600, color: ramp.deep, flexShrink: 0,
          }}>
            {res.score.toFixed(1)}
          </span>
        </div>
        <p style={{
          margin: '0.3rem 0 0', fontSize: '0.83rem', color: T.textFaint, lineHeight: 1.5,
        }}>
          {subhead}
        </p>
      </div>

      <div style={{ padding: '0 1.15rem' }}>
        <div style={{ height: 4, borderRadius: 999, background: T.cardBorder, overflow: 'hidden' }}>
          <div style={{
            height: '100%',
            borderRadius: 999,
            width: `${(res.score / 10) * 100}%`,
            background: ramp.accent,
            transition: 'width 0.6s ease',
          }} />
        </div>
      </div>

      {/* All four shown open — the assessment is two sentences, so there is
          nothing worth collapsing. */}
      <p style={{
        margin: 0,
        padding: '0.85rem 1.15rem 1rem',
        color: T.textBody,
        fontSize: '0.93rem',
        lineHeight: 1.65,
      }}>
        {res.assessment}
      </p>
    </div>
  );
}

interface Props {
  evaluation: SessionEvaluation;
  firstName: string;
  their: string;
  onGoToLearned?: () => void;
}

export default function HowYouInterviewed({ evaluation, firstName, their, onGoToLearned }: Props) {
  const overall = evaluation.overall_score ?? 0;
  const ramp = scoreRamp(overall);
  const explainer = scoreExplainer(firstName);
  const subheads = dimensionSubheads(firstName, their);
  const hand = handOff(firstName);

  const dimMap = new Map(evaluation.dimensions.map(d => [d.dimension, d]));
  const orderedDims: DimensionAssessment[] = [
    ...DIMENSION_DISPLAY_ORDER.filter(k => dimMap.has(k)).map(k => dimMap.get(k)!),
    ...evaluation.dimensions.filter(d => !DIMENSION_DISPLAY_ORDER.includes(d.dimension)),
  ];

  return (
    <>
      {orderedDims.length > 0 && (
        <div style={{
          background: T.cardBg,
          border: `1px solid ${T.cardBorder}`,
          borderLeft: `4px solid ${ramp.accent}`,
          borderRadius: 10,
          padding: '1.15rem 1.5rem',
          marginBottom: '1.5rem',
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', flexWrap: 'wrap' }}>
            <span style={{
              fontSize: '2rem', fontWeight: 300, color: ramp.deep, lineHeight: 1.1,
            }}>
              {overall.toFixed(1)}
            </span>
            {evaluation.skill_label && (
              <span style={{ fontSize: '1rem', fontWeight: 700, color: T.textHeading }}>
                {evaluation.skill_label}
              </span>
            )}
          </div>
          <p style={{ margin: '0.6rem 0 0', fontSize: '0.88rem', color: T.textBody, lineHeight: 1.6 }}>
            {explainer.line1}
            <br />
            {explainer.line2}
          </p>
        </div>
      )}

      {orderedDims.length > 0 && (
        <section style={{ marginBottom: '1.5rem' }}>
          <h2 style={{
            color: T.textHeading, fontWeight: 700, fontSize: '1.05rem', margin: '0 0 0.9rem 0',
          }}>
            {ASSESSMENT_HEADING}
          </h2>
          {orderedDims.map(d => (
            <DimensionCard key={d.dimension} res={d} subhead={subheads[d.dimension] ?? ''} />
          ))}
        </section>
      )}

      <Moments moments={evaluation.moments ?? []} firstName={firstName} />

      {onGoToLearned && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '1.25rem',
          flexWrap: 'wrap',
          background: T.tealBg,
          border: `1px solid ${T.tealSoft}`,
          borderRadius: 10,
          padding: '1.1rem 1.4rem',
          marginBottom: '1.25rem',
        }}>
          <p style={{
            margin: 0, flex: '1 1 20rem', color: T.textBody, fontSize: '0.92rem', lineHeight: 1.6,
          }}>
            {hand.body}
          </p>
          <button
            type="button"
            onClick={onGoToLearned}
            style={{
              flexShrink: 0,
              background: T.cardBg,
              border: `1px solid ${T.teal}`,
              borderRadius: 8,
              padding: '0.6rem 1.1rem',
              color: T.tealDeep,
              fontWeight: 700,
              fontSize: '0.88rem',
              cursor: 'pointer',
            }}
          >
            {hand.cta}
          </button>
        </div>
      )}
    </>
  );
}
