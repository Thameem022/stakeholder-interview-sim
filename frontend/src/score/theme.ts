import type { ItemState } from './types';

/** Diagnostic Coach palette. */
export const T = {
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

  teal:        '#0f766e',
  tealDeep:    '#115e59',
  tealSoft:    '#ccfbf1',
  tealBg:      '#f0fdfa',

  amberAccent: '#d97706',
  amberDeep:   '#b45309',
  amberBg:     '#fffbeb',
  amberBorder: '#fbbf24',

  greenAccent: '#059669',
  greenDeep:   '#047857',
  greenBg:     '#f0fdf4',
  greenBorder: '#34d399',

  redAccent:   '#dc2626',
  redDeep:     '#b91c1c',
  redBg:       '#fef2f2',
  redBorder:   '#fca5a5',
};

/**
 * One ramp for every score bar in the report.
 *
 * The old pair of functions ran red -> orange -> blue -> orange as the score
 * climbed, so a 7.0 read as a different KIND of result than a 6.9 rather than a
 * slightly better one. A single monotonic ramp is the whole point.
 */
export function scoreRamp(score: number) {
  if (score < 5.0) return { accent: T.redAccent, deep: T.redDeep, bg: T.redBg, border: T.redBorder };
  if (score < 7.0) return { accent: T.amberAccent, deep: T.amberDeep, bg: T.amberBg, border: T.amberBorder };
  return { accent: T.greenAccent, deep: T.greenDeep, bg: T.greenBg, border: T.greenBorder };
}

/** Box colours for the three coverage states. */
export const STATE_STYLE: Record<ItemState, { bg: string; border: string; text: string; chip: string }> = {
  earned:     { bg: '#e8f3d6', border: '#7fa33e', text: '#1a2e05', chip: '#3f6212' },
  opened:     { bg: '#fef6d9', border: '#ca8a04', text: '#713f12', chip: '#854d0e' },
  not_opened: { bg: '#ffffff', border: '#d1d5db', text: '#57534e', chip: '#78716c' },
};

/**
 * Widest the report column gets.
 *
 * The column is the reading measure — text inside a bordered card fills that
 * card. Capping paragraphs individually instead leaves each card ending well
 * past its own content, which reads as broken rather than as a margin.
 */
export const PAGE_MAX = '80rem';

/**
 * Cap for prose that is NOT inside a card — tier descriptions and the "why it
 * matters" line sit directly on the page, where a shorter measure reads as
 * deliberate instead of ragged.
 */
export const PROSE_MAX = '62rem';

export const MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, monospace";
export const SERIF = "Georgia, 'Times New Roman', serif";
