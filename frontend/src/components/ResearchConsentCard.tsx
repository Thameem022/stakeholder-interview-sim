import { useState } from 'react'
import { ResearchConsent, setResearchConsent } from '../api'

interface ResearchConsentCardProps {
  consent: ResearchConsent
  onDecided: (updated: ResearchConsent) => void
  /** Present when the student is changing an earlier choice. */
  onCancel?: () => void
}

/**
 * The research-participation choice (IRB-27-0033). Text and version come from
 * the server. Both answers are styled identically on purpose: neither may be
 * the "default-looking" button, and saying no must cost nothing.
 */
export function ResearchConsentCard({ consent, onDecided, onCancel }: ResearchConsentCardProps) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const decide = async (yes: boolean) => {
    if (!consent.version) return
    setSaving(true)
    setError('')
    try {
      onDecided(await setResearchConsent(yes, consent.version))
    } catch (e: any) {
      if (e?.response?.status === 401) return
      setError(
        e?.response?.status === 409
          ? 'The consent text has changed. Please reload the page and read it again.'
          : 'Could not save your choice. Please try again.'
      )
    } finally {
      setSaving(false)
    }
  }

  const choice =
    'h-11 flex-1 rounded-md border-2 border-brand bg-white px-4 text-[13px] font-semibold text-brand ' +
    'hover:bg-brand hover:text-white disabled:cursor-not-allowed disabled:opacity-60'

  return (
    <section
      aria-labelledby="research-consent-title"
      className="mt-6 max-w-[720px] rounded-lg border border-line bg-white p-6"
    >
      <h2 id="research-consent-title" className="text-[20px] font-semibold text-ink">
        {consent.title}
      </h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 text-[14px] leading-[1.6] text-muted">
        {(consent.points ?? []).map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
      <div className="mt-5 flex flex-col gap-3 sm:flex-row">
        <button type="button" disabled={saving} onClick={() => decide(true)} className={choice}>
          {consent.yes_label}
        </button>
        <button type="button" disabled={saving} onClick={() => decide(false)} className={choice}>
          {consent.no_label}
        </button>
      </div>
      {onCancel && (
        <button
          type="button"
          onClick={onCancel}
          className="mt-3 text-[13px] text-muted underline underline-offset-[3px] hover:text-brand"
        >
          Keep my current choice
        </button>
      )}
      {error && <p className="mt-3 text-[13.5px] text-danger">{error}</p>}
    </section>
  )
}
