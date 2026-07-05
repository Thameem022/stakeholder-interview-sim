import { useCallback, useRef, useState } from 'react'
import { RealtimeWebRTCSession } from '../realtime/webrtc'

export type SessionStatus = 'idle' | 'connecting' | 'live' | 'ending'

export interface UseRealtimeSessionOptions {
  personaId: string
  voiceId?: string
}

export interface RealtimeSession {
  status: SessionStatus
  sessionId: string | null
  userTranscript: string
  assistantTranscript: string
  isUserSpeaking: boolean
  isAssistantSpeaking: boolean
  analyser: AnalyserNode | null
  error: string | null
  start: () => Promise<void>
  end: () => void
}

export function useRealtimeSession(opts: UseRealtimeSessionOptions): RealtimeSession {
  const [status, setStatus] = useState<SessionStatus>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [userTranscript, setUserTranscript] = useState('')
  const [assistantTranscript, setAssistantTranscript] = useState('')
  const [isAssistantSpeaking, setIsAssistantSpeaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null)

  const sessionRef = useRef<RealtimeWebRTCSession | null>(null)

  const cleanup = useCallback(() => {
    sessionRef.current?.disconnect()
    sessionRef.current = null
    setAnalyser(null)
    setIsAssistantSpeaking(false)
  }, [])

  const end = useCallback(() => {
    setStatus('ending')
    const sess = sessionRef.current
    if (sess) {
      void sess.endAndPersist().finally(() => {
        sessionRef.current = null
        setAnalyser(null)
        setIsAssistantSpeaking(false)
        setStatus('idle')
      })
    } else {
      setStatus('idle')
    }
  }, [])

  const start = useCallback(async () => {
    if (status !== 'idle') return
    setStatus('connecting')
    setError(null)
    setUserTranscript('')
    setAssistantTranscript('')

    const sess = new RealtimeWebRTCSession()
    sessionRef.current = sess

    try {
      await sess.connect({
        personaId: opts.personaId,
        voiceId: opts.voiceId,
        callbacks: {
          onSessionReady: (sid) => setSessionId(sid),
          onUserTranscript: (text) => setUserTranscript(text),
          onAssistantTranscript: (running) => setAssistantTranscript(running),
          onAssistantDone: () => {
            // running text is already up to date via onAssistantTranscript
          },
          onAssistantSpeakingChange: (speaking) => setIsAssistantSpeaking(speaking),
          onError: (msg) => setError(msg),
        },
      })
      setAnalyser(sess.analyserNode)
      setStatus('live')
    } catch (e: any) {
      setError(e?.message || String(e))
      cleanup()
      setStatus('idle')
    }
  }, [status, opts.personaId, opts.voiceId, cleanup])

  return {
    status,
    sessionId,
    userTranscript,
    assistantTranscript,
    isUserSpeaking: false,
    isAssistantSpeaking,
    analyser,
    error,
    start,
    end,
  }
}
