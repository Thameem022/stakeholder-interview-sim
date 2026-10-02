import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { AiNotice, Avatar, Header, PreSessionNotice, ResearchConsentCard, TextInterview } from './components'
import { useRealtimeSession } from './hooks/useRealtimeSession'
import {
  Persona,
  ResearchConsent,
  endTextInterview,
  evalIqr,
  getPersonas,
  getResearchConsent,
  startTextInterview,
} from './api'
import { personaMeta } from './personas'
import ScorePage from './ScorePage'
import { AuthProvider, useAuth } from './auth/AuthContext'
import LoginPage from './auth/LoginPage'
import RequireAuth from './auth/RequireAuth'

function StepKicker({ children }: { children: string }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-brand">
      {children}
    </div>
  )
}

function PersonaCard({ persona, onPick }: { persona: Persona; onPick: () => void }) {
  const meta = personaMeta(persona.key)
  // A portrait file that 404s must not leave a broken-image icon sitting in the
  // card. Dropping the layer falls back to the backdrop alone, which is what
  // the card showed before any renders existed.
  const [portraitFailed, setPortraitFailed] = useState(false)
  const showPortrait = Boolean(meta.portrait) && !portraitFailed
  return (
    <button
      type="button"
      onClick={onPick}
      className="group flex w-full items-center gap-4 overflow-hidden rounded-lg border border-line bg-white p-3 text-left transition-colors hover:border-brand lg:flex-col lg:items-stretch lg:gap-0 lg:p-0"
    >
      {/* Previews the interview itself: the same avatar over the same backdrop
          the student meets when the call starts. The backdrop alone showed the
          set with nobody in it. */}
      <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-md bg-brand-rose lg:h-44 lg:w-full lg:rounded-none">
        {meta.image && (
          <img
            src={meta.image}
            alt=""
            className="absolute inset-0 h-full w-full object-cover"
            loading="lazy"
          />
        )}
        {showPortrait && (
          <img
            src={meta.portrait}
            alt={persona.display_name}
            onError={() => setPortraitFailed(true)}
            // Anchored to the bottom so the head-and-shoulders render sits in
            // the scene rather than floating in the middle of it.
            className="absolute inset-x-0 bottom-0 mx-auto h-[115%] w-auto max-w-none object-contain object-bottom drop-shadow-[0_2px_12px_rgba(12,10,9,0.28)]"
            loading="lazy"
          />
        )}
        {meta.role && (
          <span className="absolute bottom-3 left-3 hidden bg-ink/70 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-white backdrop-blur-[2px] lg:inline-block">
            {meta.role}
          </span>
        )}
      </div>

      <div className="min-w-0 flex-1 lg:p-5">
        <div className="text-[17px] font-semibold text-ink lg:text-[19px]">
          {persona.display_name}
        </div>
        {meta.role && (
          <div className="mt-0.5 text-[11px] uppercase tracking-[0.12em] text-muted-soft lg:hidden">
            {meta.role}
          </div>
        )}
        {meta.description && (
          <p className="mt-2 hidden text-[13.5px] leading-[1.65] text-muted lg:block">
            {meta.description}
          </p>
        )}
        <div className="mt-4 hidden items-center justify-between border-t border-line-soft pt-3 lg:flex">
          <span className="font-plex text-[11px] text-muted-faint">{persona.key}</span>
          <span className="text-[11.5px] font-semibold uppercase tracking-[0.1em] text-brand group-hover:text-brand-hover">
            Interview →
          </span>
        </div>
      </div>
    </button>
  )
}

function InterviewView() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [personasError, setPersonasError] = useState('')
  const [selected, setSelected] = useState<string>('')
  const [scoring, setScoring] = useState(false)
  const [scoringError, setScoringError] = useState('')
  // The notice stands between "Start interview" and the interview itself,
  // every time.
  const [showNotice, setShowNotice] = useState(false)
  // Research participation: asked once (per consent-text version), before the
  // first interview, only while research is open. Never shown to anyone else.
  const [consent, setConsent] = useState<ResearchConsent | null>(null)
  const [changingConsent, setChangingConsent] = useState(false)
  // Voice or a written (typed) interview — same stakeholder, same scoring.
  const [mode, setMode] = useState<'voice' | 'text'>('voice')
  const [textSessionId, setTextSessionId] = useState<string | null>(null)
  const [textStarting, setTextStarting] = useState(false)
  const [textError, setTextError] = useState('')
  const interviewHeadingRef = useRef<HTMLHeadingElement>(null)
  const navigate = useNavigate()
  const { refresh } = useAuth()

  useEffect(() => {
    getPersonas()
      .then(setPersonas)
      .catch((e) => {
        // A 401 is already being handled — the interceptor is navigating to
        // /login. Showing an error here would flash it on the way out.
        if (e?.response?.status === 401) return
        setPersonasError('Could not load the stakeholder list. Please reload.')
      })
    getResearchConsent()
      .then(setConsent)
      // Without an answer the step is simply not shown; nothing is captured
      // for research without a recorded yes, so failing closed is safe.
      .catch(() => setConsent(null))
  }, [])

  const consentPending = !!consent?.enabled && consent.consented === null

  // The persona card that had focus is gone once one is chosen; move focus to
  // the new step rather than dropping keyboard users back at the top.
  useEffect(() => {
    if (selected) interviewHeadingRef.current?.focus()
  }, [selected])

  const session = useRealtimeSession({
    personaId: selected,
    // The interview's background calls opt out of the global redirect so the
    // microphone can be stopped first. By the time this runs the connection is
    // already down, so it is safe to leave the route.
    onAuthExpired: () => {
      void refresh()
      navigate('/login', { replace: true, state: { reason: 'session_expired' } })
    },
  })

  const startText = async (noticeVersion: string) => {
    setTextStarting(true)
    setTextError('')
    try {
      const { session_id } = await startTextInterview(selected, noticeVersion)
      setTextSessionId(session_id)
    } catch (e: any) {
      if (e?.response?.status === 401) return
      setTextError('Could not start the interview. Please try again.')
    } finally {
      setTextStarting(false)
    }
  }

  const handleEnd = async () => {
    const sid = textSessionId ?? session.sessionId
    if (textSessionId) {
      setScoring(true)
      try {
        await endTextInterview(textSessionId)
      } catch (e: any) {
        if (e?.response?.status === 401) return
        // Ending only stamps the time; scoring reads the saved transcript
        // either way, so carry on.
      }
    } else {
      session.end()
    }
    if (!sid) return
    setScoring(true)
    setScoringError('')
    try {
      const result = await evalIqr(sid)
      navigate(`/score/${sid}`, { state: { evaluation: result } })
    } catch (e: any) {
      const status = e?.response?.status
      // Navigating here would race the interceptor's redirect to /login, and
      // whichever landed second would win.
      if (status === 401) return
      if (status === 429) {
        setScoringError(
          'Too many scoring requests. Please wait a few minutes and try again.'
        )
        return
      }
      navigate(`/score/${sid}`, {
        state: { evaluation: null, error: e?.message ?? String(e) },
      })
    } finally {
      setScoring(false)
    }
  }

  const handleReset = () => {
    if (session.status !== 'idle') session.end()
    setSelected('')
    setScoring(false)
    setTextSessionId(null)
    setTextError('')
  }

  const personaName = personas.find((p) => p.key === selected)?.display_name || selected
  const showViewScore =
    !textSessionId && !!session.sessionId && session.status === 'idle' && !scoring
  const interviewActive = !!textSessionId || session.status !== 'idle' || textStarting

  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main className="mx-auto max-w-[1200px] px-[18px] py-8 lg:px-11 lg:py-10">
        {!selected && consent && (consentPending || changingConsent) ? (
          <ResearchConsentCard
            consent={consent}
            onDecided={(updated) => {
              setConsent(updated)
              setChangingConsent(false)
            }}
            onCancel={changingConsent && !consentPending ? () => setChangingConsent(false) : undefined}
          />
        ) : !selected ? (
          <>
            <StepKicker>Step 1 of 2</StepKicker>
            <h2 className="mt-1.5 text-[26px] font-semibold tracking-[-0.01em] text-ink lg:text-[32px]">
              Choose a stakeholder
            </h2>
            <div className="mt-1 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
              <p className="max-w-[640px] text-[14.5px] leading-[1.6] text-muted">
                Four Harbortown personas. Each interview is scored on interview quality
                and stakeholder insight.
              </p>
              <div className="flex shrink-0 flex-col items-end gap-1">
                <span className="font-plex text-[11.5px] text-muted-soft">
                  {personas.length} personas
                </span>
                {consent?.enabled && consent.consented !== null && (
                  <button
                    onClick={() => setChangingConsent(true)}
                    className="text-[12px] text-muted underline underline-offset-[3px] hover:text-brand"
                  >
                    Research participation: {consent.consented ? 'taking part' : 'not taking part'} · change
                  </button>
                )}
                {showViewScore && (
                  <button
                    onClick={() => navigate(`/score/${session.sessionId}`)}
                    className="text-[13px] font-semibold text-brand underline underline-offset-[3px] hover:text-brand-hover"
                  >
                    View last score report →
                  </button>
                )}
              </div>
            </div>
            <AiNotice className="mt-4" />
            <div className="mt-4 h-px bg-line-soft" />

            {personasError ? (
              <div className="mt-8 border-l-[3px] border-danger bg-danger-bg px-3.5 py-3 text-[13.5px] leading-[1.5] text-danger">
                {personasError}
              </div>
            ) : personas.length === 0 ? (
              <p className="mt-8 text-[14.5px] text-muted-soft">Loading personas…</p>
            ) : (
              <div className="mt-7 grid grid-cols-1 gap-4 lg:grid-cols-2 lg:gap-7">
                {personas.map((p) => (
                  <PersonaCard key={p.key} persona={p} onPick={() => setSelected(p.key)} />
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div className="min-w-0">
                <StepKicker>Step 2 of 2</StepKicker>
                <h2
                  ref={interviewHeadingRef}
                  tabIndex={-1}
                  className="mt-1.5 text-[26px] font-semibold tracking-[-0.01em] text-ink outline-none lg:text-[32px]"
                >
                  Interview with {personaName}
                </h2>
              </div>
              <div className="flex shrink-0 items-center gap-4 pb-1">
                {showViewScore && (
                  <button
                    onClick={() => navigate(`/score/${session.sessionId}`)}
                    className="h-10 rounded-md bg-brand px-4 text-[11.5px] font-semibold uppercase tracking-[0.1em] text-white hover:bg-brand-hover"
                  >
                    View score report
                  </button>
                )}
                <button
                  onClick={handleReset}
                  disabled={scoring}
                  className="text-[13px] text-muted underline underline-offset-[3px] hover:text-brand disabled:cursor-not-allowed disabled:text-muted-faint"
                >
                  ← Choose a different stakeholder
                </button>
              </div>
            </div>

            <AiNotice className="mt-4" />

            <Avatar
              audioStream={session.remoteStream}
              active={session.status === 'live'}
              personaId={selected}
              personaName={personaName}
            />

            {!interviewActive && (
              <fieldset className="mx-auto mt-4 max-w-[560px]">
                <legend className="text-[13.5px] font-semibold text-ink">
                  How would you like to hold this interview?
                </legend>
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {(
                    [
                      ['voice', 'Speak', 'Talk with a microphone.'],
                      ['text', 'Write', 'Type your questions — no microphone needed.'],
                    ] as const
                  ).map(([value, label, hint]) => (
                    <label
                      key={value}
                      className={`flex cursor-pointer items-start gap-2.5 rounded-md border px-3 py-2.5 focus-within:ring-2 focus-within:ring-brand ${
                        mode === value ? 'border-brand bg-primary-50' : 'border-line'
                      }`}
                    >
                      <input
                        type="radio"
                        name="interview-mode"
                        value={value}
                        checked={mode === value}
                        onChange={() => setMode(value)}
                        className="mt-1 accent-brand"
                      />
                      <span>
                        <span className="block text-[14px] font-semibold text-ink">{label}</span>
                        <span className="block text-[12.5px] text-muted">{hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
                <p className="mt-2 text-[12px] text-muted-soft">
                  Both are scored the same way.
                </p>
              </fieldset>
            )}

            <div className="my-4 flex justify-center gap-3">
              {!interviewActive && (
                <button
                  onClick={() => setShowNotice(true)}
                  className="h-[46px] rounded-lg bg-brand px-8 text-[12.5px] font-semibold uppercase tracking-[0.1em] text-white transition-colors hover:bg-brand-hover"
                >
                  Start interview
                </button>
              )}
              {session.status === 'connecting' && (
                <div className="flex h-[46px] items-center text-[14px] text-muted">
                  Connecting…
                </div>
              )}
              {session.status === 'live' && (
                <button
                  onClick={handleEnd}
                  disabled={scoring}
                  className="h-[46px] rounded-lg bg-brand px-8 text-[12.5px] font-semibold uppercase tracking-[0.1em] text-white transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-brand-disabled"
                >
                  {scoring ? 'Scoring…' : 'End interview'}
                </button>
              )}
              {textStarting && (
                <div className="flex h-[46px] items-center text-[14px] text-muted">
                  Starting…
                </div>
              )}
              {session.status !== 'live' && !textSessionId && scoring && (
                <div className="flex h-[46px] items-center text-[14px] text-muted">
                  Scoring interview…
                </div>
              )}
            </div>

            {textSessionId ? (
              <TextInterview
                sessionId={textSessionId}
                personaName={personaName}
                onEnd={handleEnd}
                ending={scoring}
              />
            ) : mode === 'voice' ? (
              <div className="mt-6 grid grid-cols-1 gap-4">
                <div className="rounded-lg border border-line bg-white p-4">
                  <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft">
                    You said
                  </h3>
                  <p className="text-[14.5px] leading-relaxed text-ink">
                    {session.userTranscript || '—'}
                  </p>
                </div>
                <div className="rounded-lg border border-line bg-white p-4">
                  <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.11em] text-muted-soft">
                    {personaName} said
                  </h3>
                  <p className="text-[14.5px] leading-relaxed text-ink">
                    {session.assistantTranscript || '—'}
                  </p>
                </div>
              </div>
            ) : null}

            {showNotice && (
              <PreSessionNotice
                onCancel={() => setShowNotice(false)}
                onAcknowledge={(version) => {
                  setShowNotice(false)
                  void (mode === 'text' ? startText(version) : session.start(version))
                }}
              />
            )}

            {(session.error || scoringError || textError) && (
              <div
                role="alert"
                className="mt-4 border-l-[3px] border-danger bg-danger-bg px-3.5 py-3 text-[13.5px] leading-[1.5] text-danger"
              >
                {scoringError || textError || session.error}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      {/* Inside the router: AuthProvider navigates on sign-out and on a 401. */}
      <AuthProvider>
        <Routes>
          <Route
            path="/"
            element={
              <RequireAuth>
                <InterviewView />
              </RequireAuth>
            }
          />
          <Route
            path="/score/:sessionId"
            element={
              <RequireAuth>
                <ScorePage />
              </RequireAuth>
            }
          />
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
