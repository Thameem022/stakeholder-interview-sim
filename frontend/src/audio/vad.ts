// Client-side voice activity detection on the mic stream.
// Uses RMS amplitude with hysteresis. Fires speechStart/speechEnd events
// with a sustained-threshold delay to avoid background-noise false positives.

export interface VADCallbacks {
  onSpeechStart?: () => void
  onSpeechEnd?: () => void
}

export class SimpleVAD {
  private analyser: AnalyserNode
  private buf: Uint8Array<ArrayBuffer>
  private isSpeaking = false
  private speechStartedAt = 0
  private silenceStartedAt = 0
  private raf = 0

  // Tunables
  private threshold = 0.03 // RMS in [0,1]
  private speechHoldMs = 250 // sustained speech before firing onSpeechStart
  private silenceHoldMs = 500 // sustained silence before firing onSpeechEnd

  constructor(
    ctx: AudioContext,
    stream: MediaStream,
    private cb: VADCallbacks = {}
  ) {
    const src = ctx.createMediaStreamSource(stream)
    this.analyser = ctx.createAnalyser()
    this.analyser.fftSize = 512
    src.connect(this.analyser)
    this.buf = new Uint8Array(new ArrayBuffer(this.analyser.fftSize))
    this.loop = this.loop.bind(this)
    this.raf = requestAnimationFrame(this.loop)
  }

  private loop() {
    this.analyser.getByteTimeDomainData(this.buf)
    let sum = 0
    for (let i = 0; i < this.buf.length; i++) {
      const v = (this.buf[i] - 128) / 128
      sum += v * v
    }
    const rms = Math.sqrt(sum / this.buf.length)
    const now = performance.now()

    if (rms > this.threshold) {
      this.silenceStartedAt = 0
      if (!this.isSpeaking) {
        if (this.speechStartedAt === 0) this.speechStartedAt = now
        if (now - this.speechStartedAt >= this.speechHoldMs) {
          this.isSpeaking = true
          this.cb.onSpeechStart?.()
        }
      }
    } else {
      this.speechStartedAt = 0
      if (this.isSpeaking) {
        if (this.silenceStartedAt === 0) this.silenceStartedAt = now
        if (now - this.silenceStartedAt >= this.silenceHoldMs) {
          this.isSpeaking = false
          this.cb.onSpeechEnd?.()
        }
      }
    }

    this.raf = requestAnimationFrame(this.loop)
  }

  destroy() {
    cancelAnimationFrame(this.raf)
  }
}
