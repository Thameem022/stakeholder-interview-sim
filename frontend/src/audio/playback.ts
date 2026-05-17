// PCM16 audio playback queue. Schedules base64-encoded 24kHz PCM16 chunks
// gaplessly into a Web Audio AudioContext. Exposes an AnalyserNode for the
// avatar amplitude-mouth animation.

export class PCMPlayback {
  private ctx: AudioContext
  private analyser: AnalyserNode
  private nextStart = 0
  private gain: GainNode

  constructor() {
    this.ctx = new AudioContext({ sampleRate: 24000 })
    this.gain = this.ctx.createGain()
    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 256
    this.gain.connect(this.analyser)
    this.analyser.connect(this.ctx.destination)
  }

  get analyserNode(): AnalyserNode {
    return this.analyser
  }

  async resume() {
    if (this.ctx.state === 'suspended') await this.ctx.resume()
  }

  enqueue(base64Pcm: string) {
    const bytes = base64ToUint8Array(base64Pcm)
    const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2)
    const float = new Float32Array(samples.length)
    for (let i = 0; i < samples.length; i++) {
      float[i] = samples[i] / 0x8000
    }
    const buffer = this.ctx.createBuffer(1, float.length, 24000)
    buffer.copyToChannel(float, 0)
    const src = this.ctx.createBufferSource()
    src.buffer = buffer
    src.connect(this.gain)
    const now = this.ctx.currentTime
    const startAt = Math.max(now, this.nextStart)
    src.start(startAt)
    this.nextStart = startAt + buffer.duration
  }

  flush() {
    this.nextStart = this.ctx.currentTime
    this.gain.disconnect()
    this.gain = this.ctx.createGain()
    this.gain.connect(this.analyser)
  }

  close() {
    void this.ctx.close()
  }
}

function base64ToUint8Array(b64: string): Uint8Array {
  const binary = atob(b64)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i)
  return out
}
