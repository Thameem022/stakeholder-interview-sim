import { useEffect, useRef } from 'react'
import './Avatar.css'

export interface AvatarProps {
  analyser: AnalyserNode | null
  isSpeaking: boolean
  personaImageUrl?: string
  personaName: string
}

export function Avatar({ analyser, isSpeaking, personaImageUrl, personaName }: AvatarProps) {
  const mouthRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number>(0)

  useEffect(() => {
    if (!analyser || !mouthRef.current) return
    const buf = new Uint8Array(new ArrayBuffer(analyser.fftSize))

    const loop = () => {
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / buf.length)
      const scale = isSpeaking ? Math.min(1, rms * 6) : 0
      if (mouthRef.current) {
        mouthRef.current.style.transform = `scaleY(${0.2 + scale * 0.8})`
      }
      rafRef.current = requestAnimationFrame(loop)
    }
    rafRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(rafRef.current)
  }, [analyser, isSpeaking])

  return (
    <div className="avatar-container">
      {personaImageUrl ? (
        <img src={personaImageUrl} alt={personaName} className="avatar-image" />
      ) : (
        <div className="avatar-placeholder">{personaName.charAt(0)}</div>
      )}
      <div ref={mouthRef} className={`avatar-mouth ${isSpeaking ? 'speaking' : ''}`} />
      <div className="avatar-name">{personaName}</div>
    </div>
  )
}
