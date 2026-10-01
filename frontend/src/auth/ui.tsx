/**
 * Shared primitives for the sign-in landing page.
 *
 * Values that exist in the Tailwind theme are used as tokens; the rest are
 * arbitrary values matching the mockup exactly (13.5px body copy and so on)
 * rather than being rounded to the nearest step.
 */

import { ReactNode } from 'react'

export function Banner({
  tone,
  children,
}: {
  tone: 'error' | 'success'
  children: ReactNode
}) {
  const error = tone === 'error'
  return (
    <div
      role={error ? 'alert' : 'status'}
      aria-live={error ? 'assertive' : 'polite'}
      className={[
        'border-l-[3px] px-[14px] py-3 text-[13.5px] leading-[1.5]',
        error ? 'border-danger bg-danger-bg text-danger' : 'border-success bg-success-bg text-success',
      ].join(' ')}
    >
      {children}
    </div>
  )
}
