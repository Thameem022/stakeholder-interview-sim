import { useCallback, useEffect, useRef, useState } from 'react'
import { RealtimeWebRTCSession } from '../realtime/webrtc'

export type SessionStatus = 'idle' | 'connecting' | 'live' | 'ending'

export interface UseRealtimeSessionOptions {
  personaId: string
  voiceId?: string
  /** Called after the connection is torn down because the login session died. */
  onAuthExpired?: () => void
}

export interface RealtimeSession {
  status: SessionStatus
  sessionId: string | null
  userTranscript: string
  assistantTranscript: string
  isUserSpeaking: boolean
  isAssistantSpeaking: boolean
  analyser: AnalyserNode | null
  remoteStream: MediaStream | null
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
  const [remoteStream, setRemoteStream] = useState<MediaStream | null>(null)

  const sessionRef = useRef<RealtimeWebRTCSession | null>(null)

  // Held in a ref so an inline callback from the caller doesn't re-create
  // `start` on every render.
  const onAuthExpiredRef = useRef(opts.onAuthExpired)
  onAuthExpiredRef.current = opts.onAuthExpired

  const cleanup = useCallback(() => {
    sessionRef.current?.disconnect()
    sessionRef.current = null
    setAnalyser(null)
    setRemoteStream(null)
    setIsAssistantSpeaking(false)
  }, [])

  // Tear the connection down if this component ever goes away with a session
  // still open — which route guards and the 401 redirect can now cause. Without
  // it the RTCPeerConnection outlives the route and the microphone stays live
  // after the user has visibly left the interview.
  useEffect(() => {
    return () => {
      sessionRef.current?.disconnect()
      sessionRef.current = null
    }
  }, [])

  const end = useCallback(() => {
    setStatus('ending')
    const sess = sessionRef.current
    if (sess) {
      void sess.endAndPersist().finally(() => {
        sessionRef.current = null
        setAnalyser(null)
        setRemoteStream(null)
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
          onRemoteStream: (stream) => setRemoteStream(stream),
          onUserTranscript: (text) => setUserTranscript(text),
          onAssistantTranscript: (running) => setAssistantTranscript(running),
          onAssistantDone: () => {
            // running text is already up to date via onAssistantTranscript
          },
          onAssistantSpeakingChange: (speaking) => setIsAssistantSpeaking(speaking),
          onError: (msg) => setError(msg),
          onAuthExpired: () => {
            // Stop the connection (and the microphone) first, then let the
            // app redirect. The other order leaves a live mic on a route the
            // user can no longer see.
            cleanup()
            setStatus('idle')
            setError('Your session ended. Please sign in again.')
            onAuthExpiredRef.current?.()
          },
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
    remoteStream,
    error,
    start,
    end,
  }
}
