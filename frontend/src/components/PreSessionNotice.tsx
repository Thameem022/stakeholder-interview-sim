import { useEffect, useRef, useState } from 'react'
import { PreSessionNotice as Notice, getNotice } from '../api'

interface PreSessionNoticeProps {
  /** Called with the acknowledged notice version; the only way to start. */
  onAcknowledge: (version: string) => void
  onCancel: () => void
}

/**
 * Shown before every interview. The text and its version come from the
 * server, which refuses to start an interview without that version — so this
 * dialog is the user-facing half of a check the backend enforces, not the
 * check itself.
 */
export function PreSessionNotice({ onAcknowledge, onCancel }: PreSessionNoticeProps) {
  const [notice, setNotice] = useState<Notice | null>(null)
  const [error, setError] = useState('')
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    getNotice()
      .then(setNotice)
      .catch((e) => {
        if (e?.response?.status === 401) return
        setError('Could not load the notice. Please try again.')
      })
  }, [])

  // Focus moves into the dialog so keyboard and screen-reader users land on it.
  useEffect(() => {
    if (notice) headingRef.current?.focus()
  }, [notice])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/50 px-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="pre-session-notice-title"
        className="w-full max-w-[560px] rounded-lg bg-white p-6 shadow-xl"
      >
        {error ? (
          <p className="text-[14px] text-danger">{error}</p>
        ) : !notice ? (
          <p className="text-[14px] text-muted">Loading…</p>
        ) : (
          <>
            <h2
              id="pre-session-notice-title"
              ref={headingRef}
              tabIndex={-1}
              className="text-[20px] font-semibold text-ink outline-none"
            >
              {notice.title}
            </h2>
            <ul className="mt-4 list-disc space-y-2 pl-5 text-[14px] leading-[1.6] text-muted">
              {notice.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </>
        )}
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="h-10 px-4 text-[13px] text-muted underline underline-offset-[3px] hover:text-brand"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!notice}
            onClick={() => notice && onAcknowledge(notice.version)}
            className="h-10 rounded-md bg-brand px-5 text-[12px] font-semibold uppercase tracking-[0.08em] text-white hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-brand-disabled"
          >
            {notice?.acknowledgement ?? 'Start the interview'}
          </button>
        </div>
      </div>
    </div>
  )
}
