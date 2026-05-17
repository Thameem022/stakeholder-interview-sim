// AudioWorklet that downsamples mic input to PCM16 24kHz mono.
// Posts {type:'pcm', buffer:ArrayBuffer} messages every ~20ms (480 samples @ 24kHz).

class PCMDownsampleProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.targetRate = 24000
    this.sourceRate = sampleRate // global from AudioWorkletProcessor scope
    this.ratio = this.sourceRate / this.targetRate
    this.acc = []
    this.frameSize = 480 // 20ms @ 24kHz
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const channel = input[0]
    if (!channel) return true

    // Downsample (simple linear-decimation)
    for (let i = 0; i < channel.length; i += this.ratio) {
      const idx = Math.floor(i)
      this.acc.push(channel[idx])
    }

    // Emit fixed-size PCM16 frames
    while (this.acc.length >= this.frameSize) {
      const slice = this.acc.splice(0, this.frameSize)
      const pcm16 = new Int16Array(slice.length)
      for (let j = 0; j < slice.length; j++) {
        const s = Math.max(-1, Math.min(1, slice[j]))
        pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      this.port.postMessage({ type: 'pcm', buffer: pcm16.buffer }, [pcm16.buffer])
    }
    return true
  }
}

registerProcessor('pcm-downsample-processor', PCMDownsampleProcessor)
