/**
 * Shared form primitives for the auth screens.
 *
 * Values that exist in the Tailwind theme are used as tokens; the rest are
 * arbitrary values matching the mockup exactly (13.5px body copy, 0.11em
 * label tracking, and so on) rather than being rounded to the nearest step.
 */

import { ReactNode } from 'react'

export function Spinner({ label }: { label: string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className="inline-block h-[17px] w-[17px] animate-spin rounded-full border-2 border-white/40 border-t-white"
    />
  )
}

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

export function FieldLabel({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label
      htmlFor={htmlFor}
      className="text-[11px] font-semibold uppercase tracking-[0.11em] text-ink"
    >
      {children}
    </label>
  )
}

export function FieldError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div id={id} aria-live="polite" className="text-[12.5px] leading-[1.5] text-danger">
      {children}
    </div>
  )
}

export const inputClasses = (invalid: boolean, extra = '') =>
  [
    'w-full rounded-md border bg-white px-[14px] py-[13px] text-[15px] text-ink',
    'outline-none focus:border-brand focus-visible:ring-2 focus-visible:ring-brand/40',
    invalid ? 'border-danger' : 'border-line',
    extra,
  ].join(' ')

/** Submits its enclosing <form>, so Enter works without extra wiring. */
export function PrimaryButton({
  disabled,
  loading,
  loadingLabel,
  children,
}: {
  disabled: boolean
  loading: boolean
  loadingLabel: string
  children: ReactNode
}) {
  return (
    <button
      type="submit"
      disabled={disabled}
      className={[
        'mt-1 flex h-[50px] w-full items-center justify-center gap-[10px] rounded-lg',
        'text-[13px] font-semibold uppercase tracking-[0.1em] text-white transition-colors',
        disabled ? 'cursor-not-allowed bg-brand-disabled' : 'bg-brand hover:bg-brand-hover active:translate-y-px',
      ].join(' ')}
    >
      {loading ? <Spinner label={loadingLabel} /> : <span>{children}</span>}
    </button>
  )
}

export function TextLink({
  onClick,
  children,
  className = '',
}: {
  onClick: () => void
  children: ReactNode
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`font-semibold underline underline-offset-[3px] text-brand hover:text-brand-hover ${className}`}
    >
      {children}
    </button>
  )
}
