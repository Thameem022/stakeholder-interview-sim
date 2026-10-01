// Microphone -> 16 kHz, 16-bit mono PCM frames for the interview stream.
//
// Runs on the audio thread. The input arrives at the AudioContext's own rate
// (usually 48 kHz); it is resampled by linear interpolation, converted to
// Int16, and posted to the main thread in ~40 ms frames, which forwards each
// one over the WebSocket. Nothing is buffered beyond the current frame and
// nothing is stored: audio exists here only on its way to the server.

const TARGET_RATE = 16000
const FRAME_SAMPLES = 640 // 40 ms at 16 kHz

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.step = sampleRate / TARGET_RATE // input samples per output sample
    this.pos = 0 // fractional read position into the carried-over input
    this.carry = new Float32Array(0)
    this.frame = new Int16Array(FRAME_SAMPLES)
    this.filled = 0
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true

    const data = new Float32Array(this.carry.length + channel.length)
    data.set(this.carry, 0)
    data.set(channel, this.carry.length)

    let pos = this.pos
    while (pos + 1 < data.length) {
      const i = Math.floor(pos)
      const frac = pos - i
      const sample = data[i] + (data[i + 1] - data[i]) * frac
      const clamped = Math.max(-1, Math.min(1, sample))
      this.frame[this.filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff
      if (this.filled === FRAME_SAMPLES) {
        const out = this.frame.slice().buffer
        this.port.postMessage(out, [out])
        this.filled = 0
      }
      pos += this.step
    }

    const keep = Math.floor(pos)
    this.carry = data.slice(keep)
    this.pos = pos - keep
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
