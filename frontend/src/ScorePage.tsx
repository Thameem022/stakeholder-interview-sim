import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import ScoreReport, { type SessionEvaluation } from './ScoreReport'
import { getLatestEvaluation } from './api'

type Status = 'idle' | 'loading' | 'loaded' | 'not_found' | 'error'

export default function ScorePage() {
  const location = useLocation()
  const navigate = useNavigate()
  const { sessionId } = useParams<{ sessionId: string }>()
  const state = location.state as
    | { evaluation?: SessionEvaluation; conversation?: unknown[] }
    | null

  const [evaluation, setEvaluation] = useState<SessionEvaluation | null>(
    state?.evaluation ?? null
  )
  const conversation = state?.conversation
  const [status, setStatus] = useState<Status>(state?.evaluation ? 'loaded' : 'idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    if (evaluation || !sessionId) return
    let cancelled = false
    setStatus('loading')
    getLatestEvaluation(sessionId)
      .then((data) => {
        if (cancelled) return
        setEvaluation(data as SessionEvaluation)
        setStatus('loaded')
      })
      .catch((e: any) => {
        if (cancelled) return
        const code = e?.response?.status
        if (code === 404) {
          setStatus('not_found')
        } else {
          setErrorMsg(e?.response?.data?.detail ?? e?.message ?? 'failed to load score')
          setStatus('error')
        }
      })
    return () => {
      cancelled = true
    }
  }, [evaluation, sessionId])

  const goBack = () => navigate('/', { state: { conversation, evaluation } })

  if (status === 'loading') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 text-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-300 mb-4">Loading score report…</p>
        </div>
      </div>
    )
  }

  if (status === 'error') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 text-white flex items-center justify-center">
        <div className="text-center max-w-md px-6">
          <p className="text-slate-300 mb-2">Could not load the score report.</p>
          {errorMsg && <p className="text-slate-400 text-sm mb-4">{errorMsg}</p>}
          <button
            onClick={goBack}
            className="px-6 py-3 rounded-full bg-slate-700 hover:bg-slate-600 transition-colors text-white font-medium"
          >
            ← Back to Interview
          </button>
        </div>
      </div>
    )
  }

  if (!evaluation || status === 'not_found') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 text-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-300 mb-4">No score report available. Complete an interview first.</p>
          <button
            onClick={goBack}
            className="px-6 py-3 rounded-full bg-slate-700 hover:bg-slate-600 transition-colors text-white font-medium"
          >
            ← Back to Interview
          </button>
        </div>
      </div>
    )
  }

  return <ScoreReport evaluation={evaluation} onClose={goBack} />
}
