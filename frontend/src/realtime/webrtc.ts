// Browser-direct WebRTC session against OpenAI Realtime.
//
// Handshake:
//   1. POST /api/realtime/token        → ephemeral key + session id
//   2. POST <openai>/v1/realtime?model → SDP offer/answer exchange
//
// After connect, audio flows directly browser↔OpenAI. The data channel carries
// events (transcripts, tool calls). The backend is only contacted for:
//   - the initial token mint
//   - per-turn transcript persistence
//   - retrieve_context tool fulfillment (RAG)

import { getRealtimeToken, postRetrieve, postTranscript } from '../api'

export interface RealtimeCallbacks {
  onSessionReady?: (sessionId: string) => void
  onUserTranscript?: (text: string) => void
  onAssistantTranscript?: (running: string) => void
  onAssistantDone?: (final: string) => void
  onAssistantSpeakingChange?: (speaking: boolean) => void
  onError?: (message: string) => void
}

export interface ConnectOptions {
  personaId: string
  voiceId?: string
  /**
   * If true, the local mic is fully silenced for the duration of every
   * assistant response — from `response.created` (before any audio arrives)
   * through `response.done`. We both:
   *   - set `track.enabled = false`, AND
   *   - call `sender.replaceTrack(null)` so the WebRTC sender transmits
   *     literally nothing (not even silence packets) to the server.
   * This is what prevents OpenAI's server VAD from ever seeing user audio
   * during a response, so the persona can't be interrupted by playback
   * bleed, background noise, or the user starting to talk too early.
   * Server VAD is still on for user turns, so replies auto-commit — no buttons.
   */
  turnBased?: boolean
  callbacks: RealtimeCallbacks
}

// GA Realtime WebRTC SDP exchange endpoint. The /v1/realtime URL (no /calls
// suffix) is the deprecated Beta shape and returns 400 with
// "The Realtime Beta API is no longer supported."
const OPENAI_REALTIME_SDP_URL = 'https://api.openai.com/v1/realtime/calls'

export class RealtimeWebRTCSession {
  private pc: RTCPeerConnection | null = null
  private dc: RTCDataChannel | null = null
  private localStream: MediaStream | null = null
  private audioSender: RTCRtpSender | null = null
  private audioTrack: MediaStreamTrack | null = null
  private audioEl: HTMLAudioElement | null = null
  private audioCtx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private sessionId: string | null = null
  private personaId = ''
  private assistantBuf = ''
  private toolArgsBuf: Record<string, string> = {}
  private turnBased = false
  private micMuted = false
  private lastResponseDoneAt = 0

  get analyserNode(): AnalyserNode | null {
    return this.analyser
  }

  get currentSessionId(): string | null {
    return this.sessionId
  }

  async connect(opts: ConnectOptions): Promise<void> {
    this.personaId = opts.personaId
    this.turnBased = !!opts.turnBased

    const { ephemeral_key, session_id, model } = await getRealtimeToken(
      opts.personaId,
      opts.voiceId,
      undefined,
      this.turnBased
    )
    this.sessionId = session_id
    opts.callbacks.onSessionReady?.(session_id)

    const pc = new RTCPeerConnection()
    this.pc = pc

    const audioEl = document.createElement('audio')
    audioEl.autoplay = true
    this.audioEl = audioEl
    pc.ontrack = (e) => {
      audioEl.srcObject = e.streams[0]
      void audioEl.play().catch(() => {})
      this.setupAnalyser(e.streams[0], opts.callbacks)
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    })
    this.localStream = stream
    for (const track of stream.getTracks()) {
      const sender = pc.addTrack(track, stream)
      if (track.kind === 'audio') {
        this.audioSender = sender
        this.audioTrack = track
      }
    }

    const dc = pc.createDataChannel('oai-events')
    this.dc = dc
    dc.onmessage = (e) => {
      void this.handleEvent(e.data, opts.callbacks)
    }
    dc.onerror = (e) => opts.callbacks.onError?.(`data channel error: ${String(e)}`)

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await this.waitForIceGatheringComplete(pc)

    const sdpUrl = `${OPENAI_REALTIME_SDP_URL}?model=${encodeURIComponent(model)}`
    const sdpResp = await fetch(sdpUrl, {
      method: 'POST',
      body: pc.localDescription?.sdp ?? offer.sdp,
      headers: {
        Authorization: `Bearer ${ephemeral_key}`,
        'Content-Type': 'application/sdp',
      },
    })
    if (!sdpResp.ok) {
      const detail = await sdpResp.text().catch(() => '')
      throw new Error(`SDP exchange failed: ${sdpResp.status} ${detail.slice(0, 200)}`)
    }
    const answerSdp = await sdpResp.text()
    await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp })
  }

  disconnect(): void {
    try {
      this.dc?.close()
    } catch {}
    try {
      this.pc?.close()
    } catch {}
    this.localStream?.getTracks().forEach((t) => t.stop())
    if (this.audioEl) {
      this.audioEl.srcObject = null
      this.audioEl = null
    }
    if (this.audioCtx && this.audioCtx.state !== 'closed') {
      void this.audioCtx.close()
    }
    this.audioCtx = null
    this.analyser = null
    this.pc = null
    this.dc = null
    this.localStream = null
    this.audioSender = null
    this.audioTrack = null
    this.micMuted = false
    this.assistantBuf = ''
    this.toolArgsBuf = {}
    // sessionId left as-is so the caller can still navigate to /score/:id
  }

  async endAndPersist(): Promise<void> {
    if (this.sessionId) {
      try {
        await postTranscript(this.sessionId, 'assistant', '', true)
      } catch {}
    }
    this.disconnect()
  }

  /**
   * Fully gate the mic: flip `track.enabled` AND swap the sender's track to
   * null so the WebRTC transport sends nothing at all. Idempotent — safe to
   * call repeatedly from overlapping events (`response.created`, audio.delta).
   */
  private async muteMic(): Promise<void> {
    if (this.micMuted) return
    this.micMuted = true
    if (this.audioTrack) this.audioTrack.enabled = false
    if (this.audioSender) {
      try {
        await this.audioSender.replaceTrack(null)
      } catch {
        // If replaceTrack fails (older browser, renegotiation needed),
        // track.enabled = false above is the fallback.
      }
    }
  }

  private async unmuteMic(): Promise<void> {
    if (!this.micMuted) return
    this.micMuted = false
    if (this.audioTrack) this.audioTrack.enabled = true
    if (this.audioSender && this.audioTrack) {
      try {
        await this.audioSender.replaceTrack(this.audioTrack)
      } catch {
        // Same fallback — track.enabled = true is already set.
      }
    }
  }

  /**
   * Polls the AnalyserNode until the remote audio track goes quiet, then waits
   * a 300 ms tail buffer before resolving. Falls back to a fixed 600 ms delay
   * if the analyser is unavailable. This prevents unmuting the mic before the
   * final audio syllable has finished playing.
   */
  private waitForAudioDrain(): Promise<void> {
    return new Promise<void>((resolve) => {
      if (!this.analyser) {
        setTimeout(resolve, 600)
        return
      }
      const buf = new Uint8Array(this.analyser.frequencyBinCount)
      const poll = () => {
        this.analyser!.getByteTimeDomainData(buf)
        // Silence = all samples at the 128 midpoint (±4 counts of noise floor).
        const isQuiet = buf.every((v) => Math.abs(v - 128) < 5)
        if (isQuiet) {
          setTimeout(resolve, 300)
        } else {
          setTimeout(poll, 50)
        }
      }
      setTimeout(poll, 50)
    })
  }

  private setupAnalyser(stream: MediaStream, cb: RealtimeCallbacks): void {
    try {
      const ctx = new AudioContext()
      const src = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      src.connect(analyser)
      this.audioCtx = ctx
      this.analyser = analyser
    } catch (e) {
      cb.onError?.(`analyser setup failed: ${String(e)}`)
    }
  }

  private waitForIceGatheringComplete(pc: RTCPeerConnection): Promise<void> {
    if (pc.iceGatheringState === 'complete') return Promise.resolve()
    return new Promise((resolve) => {
      const check = () => {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', check)
          resolve()
        }
      }
      pc.addEventListener('icegatheringstatechange', check)
      // Don't block forever — trickle ICE works without complete gathering on most STUN setups.
      setTimeout(() => {
        pc.removeEventListener('icegatheringstatechange', check)
        resolve()
      }, 2500)
    })
  }

  private async handleEvent(raw: string, cb: RealtimeCallbacks): Promise<void> {
    let evt: any
    try {
      evt = JSON.parse(raw)
    } catch {
      return
    }
    const t: string = evt.type ?? ''

    switch (t) {
      case 'conversation.item.input_audio_transcription.completed': {
        const text = String(evt.transcript ?? '').trim()
        if (text) {
          // Drop Whisper hallucinations: short artifacts ("bye", "uh", etc.)
          // that arrive within 2 s of the last assistant response ending.
          // These are typically caused by throat-clearing or breath noise
          // being mis-transcribed immediately after playback stops.
          const isHallucination =
            text.length <= 6 &&
            /^(bye-bye|bye|thanks?|thank you|you|uh|um)\.?$/i.test(text) &&
            Date.now() - this.lastResponseDoneAt < 2000
          if (isHallucination) break
          cb.onUserTranscript?.(text)
          if (this.sessionId) {
            void postTranscript(this.sessionId, 'user', text).catch(() => {})
          }
        }
        break
      }

      case 'response.created': {
        // Earliest possible mute point — fires before audio_transcript.delta
        // and audio.delta, so the user has no window to interrupt mid-response.
        if (this.turnBased) void this.muteMic()
        break
      }

      case 'response.output_audio_transcript.delta':
      case 'response.audio_transcript.delta': {
        const delta = String(evt.delta ?? '')
        this.assistantBuf += delta
        cb.onAssistantTranscript?.(this.assistantBuf)
        break
      }

      case 'response.output_audio.delta':
      case 'response.audio.delta': {
        // Redundant with response.created above, but kept as a safety net in
        // case response.created is skipped or muteMic() races.
        if (this.turnBased) void this.muteMic()
        cb.onAssistantSpeakingChange?.(true)
        break
      }

      case 'response.done': {
        const final = this.assistantBuf.trim()
        this.assistantBuf = ''
        this.lastResponseDoneAt = Date.now()
        if (final) {
          cb.onAssistantDone?.(final)
          if (this.sessionId) {
            void postTranscript(this.sessionId, 'assistant', final).catch(() => {})
          }
        }
        if (this.turnBased) {
          // response.done fires when the model finishes generating, not when the
          // user finishes hearing. Wait for the audio playback queue to drain
          // (detected via the AnalyserNode) + a 300 ms tail buffer before
          // unmuting so the final syllable isn't clipped by the next VAD window.
          void this.waitForAudioDrain().then(() => {
            cb.onAssistantSpeakingChange?.(false)
            void this.unmuteMic()
          })
        } else {
          cb.onAssistantSpeakingChange?.(false)
        }
        break
      }

      case 'response.cancelled': {
        // Barge-in path — unmute immediately so the user can speak.
        this.assistantBuf = ''
        if (this.turnBased) void this.unmuteMic()
        cb.onAssistantSpeakingChange?.(false)
        break
      }

      case 'response.function_call_arguments.delta': {
        const callId = String(evt.call_id ?? '')
        if (callId) {
          this.toolArgsBuf[callId] = (this.toolArgsBuf[callId] ?? '') + String(evt.delta ?? '')
        }
        break
      }

      case 'response.function_call_arguments.done': {
        const callId = String(evt.call_id ?? '')
        const name = String(evt.name ?? '')
        const argsStr = String(evt.arguments ?? this.toolArgsBuf[callId] ?? '{}')
        delete this.toolArgsBuf[callId]
        await this.fulfillToolCall(callId, name, argsStr, cb)
        break
      }

      case 'error': {
        const msg = evt.error?.message ?? 'realtime error'
        cb.onError?.(msg)
        break
      }
    }
  }

  private async fulfillToolCall(
    callId: string,
    name: string,
    argsStr: string,
    cb: RealtimeCallbacks
  ): Promise<void> {
    if (name !== 'retrieve_context') {
      this.sendToolOutput(callId, '(unknown tool)')
      return
    }
    let query = ''
    try {
      query = String(JSON.parse(argsStr).query ?? '').trim()
    } catch {}
    if (!query) {
      this.sendToolOutput(callId, '(empty query)')
      return
    }
    try {
      const { text } = await postRetrieve(this.personaId, query)
      this.sendToolOutput(callId, text)
    } catch (e: any) {
      cb.onError?.(`retrieve failed: ${e?.message ?? String(e)}`)
      this.sendToolOutput(callId, '(retrieval failed)')
    }
  }

  private sendToolOutput(callId: string, output: string): void {
    if (!this.dc || this.dc.readyState !== 'open') return
    this.dc.send(
      JSON.stringify({
        type: 'conversation.item.create',
        item: {
          type: 'function_call_output',
          call_id: callId,
          output,
        },
      })
    )
    this.dc.send(JSON.stringify({ type: 'response.create' }))
  }
}
