import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { Avatar, Header } from './components'
import { useRealtimeSession } from './hooks/useRealtimeSession'
import { Persona, evalIqr, getPersonas } from './api'
import ScorePage from './ScorePage'

function InterviewView() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [selected, setSelected] = useState<string>('')
  const [scoring, setScoring] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    getPersonas().then(setPersonas).catch(() => setPersonas([]))
  }, [])

  const session = useRealtimeSession({ personaId: selected })

  const handleEnd = async () => {
    const sid = session.sessionId
    session.end()
    if (!sid) return
    setScoring(true)
    try {
      const result = await evalIqr(sid)
      navigate(`/score/${sid}`, { state: { evaluation: result } })
    } catch (e: any) {
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
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-3xl mx-auto p-6">
        <div className="flex justify-end gap-2 mb-4">
          {showViewScore && (
            <button
              onClick={() => navigate(`/score/${session.sessionId}`)}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded-md font-medium hover:bg-blue-700"
            >
              View score report
            </button>
          )}
          <button
            onClick={handleReset}
            disabled={scoring}
            className="px-4 py-2 text-sm border border-gray-300 text-gray-700 rounded-md font-medium hover:bg-gray-100 disabled:opacity-50"
          >
            Reset
          </button>
        </div>
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">Persona</label>
          <select
            className="w-full border border-gray-300 rounded-md p-2"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={session.status !== 'idle'}
          >
            <option value="">-- choose a persona --</option>
            {personas.map((p) => (
              <option key={p.key} value={p.key}>
                {p.display_name}
              </option>
            ))}
          </select>
        </div>

        {selected && (
          <>
            <Avatar
              analyser={session.analyser}
              isSpeaking={session.isAssistantSpeaking}
              personaName={personaName}
            />

            <div className="flex gap-3 justify-center my-4">
              {session.status === 'idle' && (
                <button
                  onClick={session.start}
                  className="px-6 py-3 bg-blue-600 text-white rounded-md font-medium hover:bg-blue-700"
                  disabled={!selected}
                >
                  Start interview
                </button>
              )}
              {session.status === 'connecting' && (
                <div className="px-6 py-3 text-gray-600">Connecting…</div>
              )}
              {session.status === 'live' && (
                <button
                  onClick={handleEnd}
                  disabled={scoring}
                  className="px-6 py-3 bg-red-600 text-white rounded-md font-medium hover:bg-red-700 disabled:bg-red-400"
                >
                  {scoring ? 'Scoring…' : 'End interview'}
                </button>
              )}
              {session.status !== 'live' && scoring && (
                <div className="px-6 py-3 text-gray-600">Scoring interview…</div>
              )}
            </div>

            <div className="grid grid-cols-1 gap-4 mt-6">
              <div className="bg-white p-4 rounded-md shadow-sm">
                <h3 className="font-medium text-sm text-gray-600 mb-2">You said</h3>
                <p className="text-gray-800">{session.userTranscript || '—'}</p>
              </div>
              <div className="bg-white p-4 rounded-md shadow-sm">
                <h3 className="font-medium text-sm text-gray-600 mb-2">{personaName} said</h3>
                <p className="text-gray-800">{session.assistantTranscript || '—'}</p>
              </div>
            </div>

            {session.error && (
              <div className="mt-4 p-3 bg-red-50 text-red-800 rounded-md">{session.error}</div>
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
      <Routes>
        <Route path="/" element={<InterviewView />} />
        <Route path="/score/:sessionId" element={<ScorePage />} />
      </Routes>
    </BrowserRouter>
  )
}
