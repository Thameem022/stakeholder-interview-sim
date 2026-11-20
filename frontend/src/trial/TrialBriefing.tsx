import { useEffect, useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'

import { AuthError, TrialStart, startTrial } from '../api'
import { useAuth } from '../auth/AuthContext'
import { Header } from '../components'
import { personaMeta } from '../personas'
import { formatClock, markTrialUsed, saveTrial } from './storage'

const READ_SECONDS = 120

/**
 * "Try SES", step 1: a two-minute read before the interview.
 *
 * Arriving here creates the guest session. The interview does not start on
 * its own when the countdown ends: the browser only grants the microphone and
 * audio playback after a click, so the countdown is a suggestion and the
 * button is what starts the call.
 */
export default function TrialBriefing() {
  const navigate = useNavigate()
  const { user, loading, refresh } = useAuth()
  const [trial, setTrial] = useState<TrialStart | null>(null)
  const [error, setError] = useState('')
  const [remaining, setRemaining] = useState(READ_SECONDS)
  const requested = useRef(false)

  // A signed-in student must not be swapped for a guest by visiting this URL:
  // starting a trial replaces the session cookie.
  const isStudent = !!user && !user.is_trial

  useEffect(() => {
    if (loading || isStudent || requested.current) return
    requested.current = true
    startTrial()
      .then(async (t) => {
        saveTrial(t)
        setTrial(t)
        await refresh()
      })
      .catch((e) => {
        if (e instanceof AuthError && e.code === 'trial_used') {
          markTrialUsed()
          setError(
            "You've already used your free trial on this browser. Register with your WPI email to keep interviewing."
          )
        } else if (e instanceof AuthError && e.status === 429) {
          setError(
            'The trial is busy right now. Please try again later, or sign in with your WPI account.'
          )
        } else if (e instanceof AuthError && e.status === 404) {
          setError('The trial is not available at the moment.')
        } else {
          setError(e instanceof AuthError ? e.message : 'Could not start the trial.')
        }
      })
  }, [loading, isStudent, refresh])

  useEffect(() => {
    if (!trial) return
    const startedAt = Date.now()
    const id = window.setInterval(() => {
      const left = READ_SECONDS - (Date.now() - startedAt) / 1000
      setRemaining(left)
      if (left <= 0) window.clearInterval(id)
    }, 250)
    return () => window.clearInterval(id)
  }, [trial])

  if (isStudent) return <Navigate to="/" replace />

  const b = trial?.briefing
  const meta = personaMeta(b?.persona_id ?? 'alex_martinez')
  const done = remaining <= 0

  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main className="mx-auto max-w-[860px] px-[18px] py-8 lg:px-11 lg:py-10">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-brand">
          Try SES · Briefing
        </div>

        {error ? (
          <>
            <div className="mt-6 border-l-[3px] border-danger bg-danger-bg px-3.5 py-3 text-[13.5px] leading-[1.5] text-danger">
              {error}
            </div>
            <button
              onClick={() => navigate('/login')}
              className="mt-5 text-[13.5px] font-semibold text-brand underline underline-offset-[3px] hover:text-brand-hover"
            >
              ← Back to sign in
            </button>
          </>
        ) : !b ? (
          <p className="mt-8 text-[14.5px] text-muted-soft">Preparing your briefing…</p>
        ) : (
          <>
            <div className="mt-1.5 flex flex-wrap items-end justify-between gap-4">
              <h2 className="text-[26px] font-semibold tracking-[-0.01em] text-ink lg:text-[32px]">
                Your interview with {b.persona_name}
              </h2>
              <div
                className="font-plex text-[13px] text-muted"
                role="timer"
                aria-live="off"
              >
                {done ? 'Reading time is up' : `Reading time ${formatClock(remaining)}`}
              </div>
            </div>
            <div className="mt-4 h-px bg-line-soft" />

            <div className="mt-6 flex items-center gap-4">
              {meta.portrait && (
                <img
                  src={meta.portrait}
                  alt=""
                  className="h-16 w-16 shrink-0 rounded-md bg-brand-rose object-cover object-top"
                />
              )}
              <div>
                <div className="text-[17px] font-semibold text-ink">{b.persona_name}</div>
                <div className="text-[11px] uppercase tracking-[0.12em] text-muted-soft">
                  {b.persona_role}
                </div>
              </div>
            </div>

            <section className="mt-6">
              <h3 className="text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft">
                The situation
              </h3>
              <p className="mt-2 text-[15px] leading-[1.7] text-ink">{b.scenario}</p>
            </section>

            <section className="mt-6 rounded-lg border-l-[3px] border-brand bg-brand-rose/40 px-4 py-4">
              <h3 className="text-[11px] font-semibold uppercase tracking-[0.11em] text-brand">
                Your goal · {b.target_label}
              </h3>
              <p className="mt-2 text-[15px] leading-[1.7] text-ink">{b.goal}</p>
            </section>

            <section className="mt-6">
              <h3 className="text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft">
                How to approach it
              </h3>
              <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[14.5px] leading-[1.65] text-ink">
                {b.tips.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
              <p className="mt-3 text-[14px] leading-[1.6] text-muted">{b.avoid}</p>
            </section>

            <section className="mt-6 rounded-lg border border-line px-4 py-3 text-[13.5px] leading-[1.6] text-muted">
              It's a voice interview, so allow microphone access when asked. It ends
              automatically after <strong className="text-ink">{trial.interview_minutes} minutes</strong>,
              and you can end it early at any time. Either way you go straight to your
              score report.
            </section>

            <div className="mt-8 flex flex-wrap items-center gap-4">
              <button
                onClick={() => navigate('/try/interview')}
                className="h-[46px] rounded-lg bg-brand px-8 text-[12.5px] font-semibold uppercase tracking-[0.1em] text-white transition-colors hover:bg-brand-hover"
              >
                {done ? 'Continue to interview' : "I'm ready, continue"}
              </button>
              {done && (
                <span className="text-[13.5px] text-muted">Start when you're ready.</span>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  )
}
