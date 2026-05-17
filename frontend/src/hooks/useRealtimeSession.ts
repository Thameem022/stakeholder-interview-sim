import { useCallback, useRef, useState } from 'react'
import { PCMPlayback } from '../audio/playback'
import { SimpleVAD } from '../audio/vad'

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
  const [isUserSpeaking, setIsUserSpeaking] = useState(false)
  const [isAssistantSpeaking, setIsAssistantSpeaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const workletNodeRef = useRef<AudioWorkletNode | null>(null)
  const playbackRef = useRef<PCMPlayback | null>(null)
  const vadRef = useRef<SimpleVAD | null>(null)
  const assistantBufRef = useRef<string>('')

  const cleanup = useCallback(() => {
    vadRef.current?.destroy()
    vadRef.current = null
    workletNodeRef.current?.disconnect()
    workletNodeRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (ctxRef.current && ctxRef.current.state !== 'closed') void ctxRef.current.close()
    ctxRef.current = null
    playbackRef.current?.close()
    playbackRef.current = null
    wsRef.current?.close()
    wsRef.current = null
    setAnalyser(null)
    setIsUserSpeaking(false)
    setIsAssistantSpeaking(false)
  }, [])

  const end = useCallback(() => {
    setStatus('ending')
    try {
      wsRef.current?.send(JSON.stringify({ type: 'end' }))
    } catch {
      // ignore
    }
    cleanup()
    setStatus('idle')
  }, [cleanup])

  const start = useCallback(async () => {
    if (status !== 'idle') return
    setStatus('connecting')
    setError(null)
    setUserTranscript('')
    setAssistantTranscript('')
    assistantBufRef.current = ''

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: 24000, channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream

      const ctx = new AudioContext({ sampleRate: 24000 })
      ctxRef.current = ctx
      await ctx.audioWorklet.addModule(new URL('../audio/pcm-worklet.js', import.meta.url))

      const playback = new PCMPlayback()
      playbackRef.current = playback
      await playback.resume()
      setAnalyser(playback.analyserNode)

      const newSid = crypto.randomUUID()
      const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${wsProto}//${window.location.host}/ws/interview/${newSid}`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve()
        ws.onerror = () => reject(new Error('WebSocket connection failed'))
      })

      ws.send(JSON.stringify({ type: 'start', persona_id: opts.personaId, voice_id: opts.voiceId || '' }))

      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        switch (msg.type) {
          case 'session_started':
            setSessionId(msg.session_id)
            break
          case 'session_ready':
            setStatus('live')
            break
          case 'audio_delta':
            setIsAssistantSpeaking(true)
            playback.enqueue(msg.audio)
            break
          case 'assistant_transcript_delta':
            assistantBufRef.current += msg.delta
            setAssistantTranscript(assistantBufRef.current)
            break
          case 'response_done':
            setIsAssistantSpeaking(false)
            break
          case 'user_transcript':
            setUserTranscript(msg.text)
            break
          case 'error':
            setError(msg.error?.message || 'realtime error')
            break
        }
      }
      ws.onclose = () => {
        if (status !== 'idle') {
          setStatus('idle')
          cleanup()
        }
      }

      const src = ctx.createMediaStreamSource(stream)
      const worklet = new AudioWorkletNode(ctx, 'pcm-downsample-processor')
      src.connect(worklet)
      workletNodeRef.current = worklet

      worklet.port.onmessage = (e) => {
        if (e.data?.type !== 'pcm') return
        if (ws.readyState !== WebSocket.OPEN) return
        const b64 = arrayBufferToBase64(e.data.buffer)
        ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: b64 }))
      }

      vadRef.current = new SimpleVAD(ctx, stream, {
        onSpeechStart: () => {
          setIsUserSpeaking(true)
          if (isAssistantSpeaking && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'cancel' }))
            playback.flush()
            setIsAssistantSpeaking(false)
          }
        },
        onSpeechEnd: () => {
          setIsUserSpeaking(false)
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'commit' }))
          }
        },
      })
    } catch (e: any) {
      setError(e?.message || String(e))
      cleanup()
      setStatus('idle')
    }
  }, [status, opts.personaId, opts.voiceId, cleanup, isAssistantSpeaking])

  return {
    status,
    sessionId,
    userTranscript,
    assistantTranscript,
    isUserSpeaking,
    isAssistantSpeaking,
    analyser,
    error,
    start,
    end,
  }
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i])
  return btoa(binary)
}
