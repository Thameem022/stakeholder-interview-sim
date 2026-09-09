import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { Avatar, Header } from './components'
import { useRealtimeSession } from './hooks/useRealtimeSession'
import { Persona, evalIqr, getPersonas } from './api'
import { personaMeta } from './personas'
import ScorePage from './ScorePage'
import { AuthProvider, useAuth } from './auth/AuthContext'
import LoginPage from './auth/LoginPage'
import RegisterPage from './auth/RegisterPage'
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
  return (
    <button
      type="button"
      onClick={onPick}
      className="group flex w-full items-center gap-4 overflow-hidden rounded-lg border border-line bg-white p-3 text-left transition-colors hover:border-brand lg:flex-col lg:items-stretch lg:gap-0 lg:p-0"
    >
      <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-md lg:h-44 lg:w-full lg:rounded-none">
        {meta.image ? (
          <img
            src={meta.image}
            alt=""
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="h-full w-full bg-brand-rose" />
        )}
        {meta.role && (
          <span className="absolute bottom-3 left-3 hidden bg-ink/70 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-white lg:inline-block">
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
  }, [])

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

  const handleEnd = async () => {
    const sid = session.sessionId
    session.end()
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
  }

  const personaName = personas.find((p) => p.key === selected)?.display_name || selected
  const showViewScore = !!session.sessionId && session.status === 'idle' && !scoring

  return (
    <div className="min-h-screen bg-white">
      <Header />
      <main className="mx-auto max-w-[1200px] px-[18px] py-8 lg:px-11 lg:py-10">
        {!selected ? (
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
                <h2 className="mt-1.5 text-[26px] font-semibold tracking-[-0.01em] text-ink lg:text-[32px]">
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

            <Avatar
              audioStream={session.remoteStream}
              active={session.status === 'live'}
              personaId={selected}
              personaName={personaName}
            />

            <div className="my-4 flex justify-center gap-3">
              {session.status === 'idle' && (
                <button
                  onClick={session.start}
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
              {session.status !== 'live' && scoring && (
                <div className="flex h-[46px] items-center text-[14px] text-muted">
                  Scoring interview…
                </div>
              )}
            </div>

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

            {(session.error || scoringError) && (
              <div className="mt-4 border-l-[3px] border-danger bg-danger-bg px-3.5 py-3 text-[13.5px] leading-[1.5] text-danger">
                {scoringError || session.error}
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
          <Route path="/register" element={<RegisterPage />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
