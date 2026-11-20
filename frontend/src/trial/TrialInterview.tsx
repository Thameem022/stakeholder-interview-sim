import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { evalIqr } from '../api'
import { useAuth } from '../auth/AuthContext'
import { Avatar, Header } from '../components'
import { useRealtimeSession } from '../hooks/useRealtimeSession'
import { formatClock, loadTrial, markTrialUsed } from './storage'

const PERSONA_ID = 'alex_martinez'
const PERSONA_NAME = 'Alex Martinez'
const DEFAULT_MINUTES = 5

/**
 * "Try SES", step 2: one interview with Alex, capped in time.
 *
 * The countdown runs from the moment the call goes live. At zero the interview
 * ends exactly as the End button does. The server enforces the same cap on its
 * own endpoints, so this timer is the friendly edge of the limit, not the
 * limit itself.
 *
 * Kept separate from InterviewView on purpose: the trial must not change the
 * student flow, so the end-and-score steps are repeated here rather than
 * refactored out of it.
 */
export default function TrialInterview() {
  const navigate = useNavigate()
  const { refresh } = useAuth()
  const trial = loadTrial()
  const limitSeconds = (trial?.interview_minutes ?? DEFAULT_MINUTES) * 60

  const [scoring, setScoring] = useState(false)
  const [scoringError, setScoringError] = useState('')
  const [remaining, setRemaining] = useState(limitSeconds)
  const ended = useRef(false)

  const session = useRealtimeSession({
    personaId: PERSONA_ID,
    onAuthExpired: () => {
      void refresh()
      navigate('/login', { replace: true, state: { reason: 'session_expired' } })
    },
  })

  const handleEnd = useCallback(async () => {
    if (ended.current) return
    ended.current = true
    const sid = session.sessionId
    // Spoke to Alex, so this browser's trial is spent (the server decides the
    // same way); this only hides the login link from now on.
    if (session.userTranscript) markTrialUsed()
    session.end()
    if (!sid) return
    setScoring(true)
    setScoringError('')
    try {
      const result = await evalIqr(sid)
      navigate(`/score/${sid}`, { state: { evaluation: result } })
    } catch (e: any) {
      const status = e?.response?.status
      if (status === 401) return
      if (status === 429) {
        setScoringError('Too many scoring requests. Please wait a few minutes and try again.')
        return
      }
      navigate(`/score/${sid}`, {
        state: { evaluation: null, error: e?.message ?? String(e) },
      })
    } finally {
      setScoring(false)
    }
  }, [session, navigate])

  // Latest handleEnd for the timer, without restarting the interval each render.
  const endRef = useRef(handleEnd)
  endRef.current = handleEnd

  const live = session.status === 'live'
  useEffect(() => {
    if (!live) return
    const startedAt = Date.now()
    const id = window.setInterval(() => {
      const left = limitSeconds - (Date.now() - startedAt) / 1000
      setRemaining(left)
      if (left <= 0) {
        window.clearInterval(id)
        void endRef.current()
      }
    }, 250)
    return () => window.clearInterval(id)
  }, [live, limitSeconds])

  const lowTime = live && remaining <= 60

  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main className="mx-auto max-w-[1200px] px-[18px] py-8 lg:px-11 lg:py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-brand">
              Try SES · Interview
            </div>
            <h2 className="mt-1.5 text-[26px] font-semibold tracking-[-0.01em] text-ink lg:text-[32px]">
              Interview with {PERSONA_NAME}
            </h2>
          </div>
          <div
            role="timer"
            aria-label="Time remaining"
            className={`font-plex text-[22px] font-semibold tabular-nums ${
              lowTime ? 'text-danger' : 'text-ink'
            }`}
          >
            {formatClock(remaining)}
          </div>
        </div>

        {trial && (
          <p className="mt-3 max-w-[760px] text-[13.5px] leading-[1.6] text-muted">
            <span className="font-semibold text-brand">Goal · {trial.briefing.target_label}:</span>{' '}
            {trial.briefing.goal}
          </p>
        )}

        <Avatar
          audioStream={session.remoteStream}
          active={live}
          personaId={PERSONA_ID}
          personaName={PERSONA_NAME}
        />

        <div className="my-4 flex justify-center gap-3">
          {session.status === 'idle' && !ended.current && (
            <button
              onClick={session.start}
              className="h-[46px] rounded-lg bg-brand px-8 text-[12.5px] font-semibold uppercase tracking-[0.1em] text-white transition-colors hover:bg-brand-hover"
            >
              Start interview
            </button>
          )}
          {session.status === 'connecting' && (
            <div className="flex h-[46px] items-center text-[14px] text-muted">Connecting…</div>
          )}
          {live && (
            <button
              onClick={handleEnd}
              disabled={scoring}
              className="h-[46px] rounded-lg bg-brand px-8 text-[12.5px] font-semibold uppercase tracking-[0.1em] text-white transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-brand-disabled"
            >
              {scoring ? 'Scoring…' : 'End interview'}
            </button>
          )}
          {!live && scoring && (
            <div className="flex h-[46px] items-center text-[14px] text-muted">
              {remaining <= 0 ? "Time's up. " : ''}Scoring your interview…
            </div>
          )}
        </div>

        <div className="mt-6 grid grid-cols-1 gap-4">
          <div className="rounded-lg border border-line bg-white p-4">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft">
              You said
            </h3>
            <p className="text-[14.5px] leading-relaxed text-ink">{session.userTranscript || '—'}</p>
          </div>
          <div className="rounded-lg border border-line bg-white p-4">
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft">
              {PERSONA_NAME} said
            </h3>
            <p className="text-[14.5px] leading-relaxed text-ink">
              {session.assistantTranscript || '—'}
            </p>
          </div>
        </div>

        {(session.error || scoringError) && (
          <div className="mt-4 border-l-[3px] border-danger bg-danger-bg px-3.5 py-3 text-[13.5px] leading-[1.5] text-danger">
            {scoringError || session.error}
          </div>
        )}
        {/* A call that never got going does not spend the trial, so offer a
            clean restart rather than leaving the visitor stuck. */}
        {session.error && session.status === 'idle' && !scoring && (
          <button
            onClick={() => navigate('/try')}
            className="mt-3 text-[13.5px] font-semibold text-brand underline underline-offset-[3px] hover:text-brand-hover"
          >
            Start over →
          </button>
        )}
      </main>
    </div>
  )
}
