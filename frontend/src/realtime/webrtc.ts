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
  private audioEl: HTMLAudioElement | null = null
  private audioCtx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private sessionId: string | null = null
  private personaId = ''
  private assistantBuf = ''
  private toolArgsBuf: Record<string, string> = {}

  get analyserNode(): AnalyserNode | null {
    return this.analyser
  }

  get currentSessionId(): string | null {
    return this.sessionId
  }

  async connect(opts: ConnectOptions): Promise<void> {
    this.personaId = opts.personaId

    const { ephemeral_key, session_id, model } = await getRealtimeToken(
      opts.personaId,
      opts.voiceId
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
    stream.getTracks().forEach((t) => pc.addTrack(t, stream))

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
          cb.onUserTranscript?.(text)
          if (this.sessionId) {
            void postTranscript(this.sessionId, 'user', text).catch(() => {})
          }
        }
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
        cb.onAssistantSpeakingChange?.(true)
        break
      }

      case 'response.done': {
        const final = this.assistantBuf.trim()
        if (final) {
          cb.onAssistantDone?.(final)
          if (this.sessionId) {
            void postTranscript(this.sessionId, 'assistant', final).catch(() => {})
          }
        }
        this.assistantBuf = ''
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
