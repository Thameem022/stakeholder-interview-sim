// The live interview, streamed through the SES backend to Amazon Bedrock.
//
//   1. POST /api/realtime/token  → session id + a 60-second single-use stream
//                                  token (never an AI-provider credential)
//   2. WebSocket /api/realtime/stream, same origin, authenticated by the
//      session cookie; the first message presents the stream token
//   3. microphone → 16 kHz PCM frames → backend → Nova Sonic
//      Nova Sonic → backend → 24 kHz PCM frames → speakers (+ avatar lip-sync)
//      transcripts and turn events arrive as JSON
//
// The browser talks to the SES origin only. The backend writes the
// transcript; nothing here stores audio.

import { getRealtimeToken } from '../api'

export interface RealtimeCallbacks {
  onSessionReady?: (sessionId: string) => void
  onRemoteStream?: (stream: MediaStream) => void
  onUserTranscript?: (text: string) => void
  onAssistantTranscript?: (running: string) => void
  onAssistantDone?: (final: string) => void
  onAssistantSpeakingChange?: (speaking: boolean) => void
  onError?: (message: string) => void
  /** The server ended the interview (time limit, safety review, lost connection). */
  onEnded?: (reason: string) => void
  /**
   * The login session stopped working mid-interview. Raised instead of letting
   * the global interceptor redirect, so the owner can stop the microphone
   * before the route changes.
   */
  onAuthExpired?: () => void
}

export interface ConnectOptions {
  personaId: string
  voiceId?: string
  /** Version of the pre-session notice the student just acknowledged. */
  noticeVersion: string
  callbacks: RealtimeCallbacks
}

const PLAYBACK_RATE = 24000
// After the persona's last audio has played, wait this long before the
// microphone opens again, so the tail of its voice is not picked up.
const MIC_TAIL_MS = 300
const END_TIMEOUT_MS = 4000
const CLOSE_UNAUTHORISED = 4401

/** Messages the backend sends as JSON. */
type ServerMessage =
  | { type: 'ready'; session_id: string }
  | { type: 'transcript'; role: 'user' | 'assistant'; text: string; final: boolean }
  | { type: 'assistant_audio_start' }
  | { type: 'assistant_turn_end' }
  | { type: 'assistant_interrupted' }
  | { type: 'error'; message: string }
  | { type: 'ended'; reason: string }

/**
 * No-barge-in turn-taking, as before: the microphone sends nothing from the
 * moment the persona starts a reply until its audio has finished playing,
 * so the persona cannot be interrupted by playback bleed or by the student
 * starting to talk early.
 */
export class RealtimeStreamSession {
  private ws: WebSocket | null = null
  private micStream: MediaStream | null = null
  private captureCtx: AudioContext | null = null
  private captureNode: AudioWorkletNode | null = null
  private playCtx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private remoteStream: MediaStream | null = null
  private sessionId: string | null = null
  private nextPlayTime = 0
  private micOpen = true
  private turnEnded = false
  private unmuteTimer: number | null = null
  private endResolve: (() => void) | null = null
  private endedByClient = false
  private callbacks: RealtimeCallbacks = {}

  get analyserNode(): AnalyserNode | null {
    return this.analyser
  }

  get remoteAudioStream(): MediaStream | null {
    return this.remoteStream
  }

  get currentSessionId(): string | null {
    return this.sessionId
  }

  async connect(opts: ConnectOptions): Promise<void> {
    this.callbacks = opts.callbacks
    const { session_id, stream_token } = await getRealtimeToken(
      opts.personaId,
      opts.noticeVersion,
      opts.voiceId
    )
    this.sessionId = session_id
    opts.callbacks.onSessionReady?.(session_id)

    // Playback graph: decoded PCM → speakers, analyser, and a MediaStream the
    // avatar reads for lip-sync (it does not play it — no double audio).
    const playCtx = new AudioContext({ sampleRate: PLAYBACK_RATE })
    const analyser = playCtx.createAnalyser()
    analyser.fftSize = 512
    const toAvatar = playCtx.createMediaStreamDestination()
    analyser.connect(playCtx.destination)
    analyser.connect(toAvatar)
    this.playCtx = playCtx
    this.analyser = analyser
    this.remoteStream = toAvatar.stream
    opts.callbacks.onRemoteStream?.(toAvatar.stream)

    // Microphone capture graph (16 kHz PCM via the worklet).
    const micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    this.micStream = micStream
    const captureCtx = new AudioContext()
    await captureCtx.audioWorklet.addModule('/audio/pcm-capture-worklet.js')
    const source = captureCtx.createMediaStreamSource(micStream)
    const node = new AudioWorkletNode(captureCtx, 'pcm-capture')
    source.connect(node)
    this.captureCtx = captureCtx
    this.captureNode = node

    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${window.location.host}/api/realtime/stream`)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => ws.send(JSON.stringify({ type: 'start', token: stream_token }))
      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          const message = JSON.parse(event.data) as ServerMessage
          if (message.type === 'ready') {
            ws.onmessage = (e) => this.onMessage(e)
            resolve()
          }
        }
      }
      ws.onclose = (event) => {
        if (event.code === CLOSE_UNAUTHORISED) opts.callbacks.onAuthExpired?.()
        reject(new Error('Could not start the interview. Please try again.'))
      }
    })

    ws.onclose = (event) => this.onClose(event)
    node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (this.micOpen && ws.readyState === WebSocket.OPEN) ws.send(event.data)
    }
  }

  private onMessage(event: MessageEvent): void {
    if (event.data instanceof ArrayBuffer) {
      this.play(event.data)
      return
    }
    const message = JSON.parse(event.data as string) as ServerMessage
    switch (message.type) {
      case 'transcript':
        if (message.role === 'user') {
          this.callbacks.onUserTranscript?.(message.text)
        } else {
          this.callbacks.onAssistantTranscript?.(message.text)
          if (message.final) this.callbacks.onAssistantDone?.(message.text)
        }
        break
      case 'assistant_audio_start':
        this.setSpeaking(true)
        break
      case 'assistant_turn_end':
      case 'assistant_interrupted':
        this.turnEnded = true
        this.scheduleUnmute()
        break
      case 'error':
        this.callbacks.onError?.(message.message)
        break
      case 'ended':
        this.endResolve?.()
        if (!this.endedByClient) {
          this.callbacks.onEnded?.(message.reason)
        }
        break
    }
  }

  private onClose(event: CloseEvent): void {
    this.endResolve?.()
    if (event.code === CLOSE_UNAUTHORISED) this.callbacks.onAuthExpired?.()
    else if (!this.endedByClient) this.callbacks.onEnded?.('connection_closed')
  }

  /** Queue one chunk of 24 kHz 16-bit PCM for gapless playback. */
  private play(buffer: ArrayBuffer): void {
    const ctx = this.playCtx
    if (!ctx || !this.analyser || buffer.byteLength < 2) return
    if (this.micOpen) this.setSpeaking(true)
    const pcm = new Int16Array(buffer, 0, Math.floor(buffer.byteLength / 2))
    const audio = ctx.createBuffer(1, pcm.length, PLAYBACK_RATE)
    const channel = audio.getChannelData(0)
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000
    const source = ctx.createBufferSource()
    source.buffer = audio
    source.connect(this.analyser)
    const at = Math.max(this.nextPlayTime, ctx.currentTime + 0.02)
    source.start(at)
    this.nextPlayTime = at + audio.duration
    source.onended = () => this.scheduleUnmute()
  }

  private setSpeaking(speaking: boolean): void {
    if (speaking) {
      if (this.unmuteTimer !== null) {
        window.clearTimeout(this.unmuteTimer)
        this.unmuteTimer = null
      }
      if (this.micOpen) {
        this.micOpen = false
        this.turnEnded = false
        this.callbacks.onAssistantSpeakingChange?.(true)
      }
      return
    }
    if (!this.micOpen) {
      this.micOpen = true
      this.callbacks.onAssistantSpeakingChange?.(false)
    }
  }

  /** Reopen the mic once the turn has ended AND its audio has finished playing. */
  private scheduleUnmute(): void {
    const ctx = this.playCtx
    if (!this.turnEnded || !ctx) return
    const remainingMs = Math.max(0, (this.nextPlayTime - ctx.currentTime) * 1000)
    if (this.unmuteTimer !== null) window.clearTimeout(this.unmuteTimer)
    this.unmuteTimer = window.setTimeout(() => {
      this.unmuteTimer = null
      this.setSpeaking(false)
    }, remainingMs + MIC_TAIL_MS)
  }

  /** Ask the server to end the interview (it saves the transcript), then tear down. */
  async endAndPersist(): Promise<void> {
    const ws = this.ws
    if (ws && ws.readyState === WebSocket.OPEN) {
      this.endedByClient = true
      await new Promise<void>((resolve) => {
        this.endResolve = resolve
        ws.send(JSON.stringify({ type: 'end' }))
        window.setTimeout(resolve, END_TIMEOUT_MS)
      })
    }
    this.disconnect()
  }

  disconnect(): void {
    this.endedByClient = true
    if (this.unmuteTimer !== null) window.clearTimeout(this.unmuteTimer)
    this.unmuteTimer = null
    try {
      this.ws?.close()
    } catch {}
    this.captureNode?.port.close()
    this.micStream?.getTracks().forEach((t) => t.stop())
    for (const ctx of [this.captureCtx, this.playCtx]) {
      if (ctx && ctx.state !== 'closed') void ctx.close()
    }
    this.ws = null
    this.captureNode = null
    this.captureCtx = null
    this.playCtx = null
    this.micStream = null
    this.analyser = null
    this.remoteStream = null
    this.micOpen = true
    this.turnEnded = false
    this.nextPlayTime = 0
    // sessionId kept so the caller can still navigate to /score/:id
  }
}
