import { useEffect, useRef } from 'react'
import { TalkingHead } from '@met4citizen/talkinghead'
import './Avatar.css'

// Persona → avatar assets (GLB served from public/avatars/, optional backdrop
// image from public/background/). TalkingHead renders with a transparent
// canvas, so `bg` composites behind the 3D model.
// Keyed by persona id (same keys as the SIC keys and the persona dropdown).
// Convention: /avatars/<persona_id>.glb and /background/<persona_id>_bg.<ext>.
const AVATARS: Record<string, { url: string; body: 'M' | 'F'; bg?: string }> = {
  alex_martinez: {
    url: '/avatars/alex_martinez.glb',
    body: 'M',
    bg: '/background/alex_martinez_bg.jpg',
  },
  michael_mike_alvarez: {
    url: '/avatars/michael_mike_alvarez.glb',
    body: 'M',
    bg: '/background/michael_mike_alvarez_bg.avif',
  },
  sarah_donnelly: {
    url: '/avatars/sarah_donnelly.glb',
    body: 'F',
    bg: '/background/sarah_donnelly_bg.jpg',
  },
  thomas_tom_caldwell: {
    url: '/avatars/thomas_tom_caldwell.glb',
    body: 'M',
    bg: '/background/thomas_tom_caldwell_bg.jpg',
  },
}

// HeadAudio assets, served from public/headaudio/. The dist bundles are
// self-contained (no relative imports), so they can be loaded at runtime
// without going through the Vite build.
const HEADAUDIO_MODULE_URL = '/headaudio/dist/headaudio.min.mjs'
const HEADAUDIO_WORKLET_URL = '/headaudio/dist/headworklet.min.mjs'
const HEADAUDIO_MODEL_URL = '/headaudio/dist/model-en-mixed.bin'

// Runtime shape of the HeadAudio AudioWorkletNode (public/headaudio/dist/headaudio.min.mjs).
interface HeadAudioNode extends AudioWorkletNode {
  onvalue: ((key: string, value: number) => void) | null
  loadModel(url: string): Promise<void>
  update(dt: number): void
}

type HeadAudioModule = {
  HeadAudio: new (
    audioCtx: AudioContext,
    options?: { parameterData?: Record<string, number> }
  ) => HeadAudioNode
}

// Everything a single mount owns, so teardown disposes exactly what this
// mount created — never a later mount's instances (refs are shared across
// StrictMode's double-mount, so we must not read them in cleanup).
interface HeadInstance {
  head: TalkingHead
  headaudio: HeadAudioNode
}

export interface AvatarProps {
  audioStream: MediaStream | null
  active: boolean
  personaId: string
  personaName: string
}

export function Avatar({ audioStream, active, personaId, personaName }: AvatarProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Resolves to the live instance once setup completes (or null if it was torn
  // down / failed). The stream + active effects await this to wire audio.
  const instanceRef = useRef<Promise<HeadInstance | null> | null>(null)

  const avatarConfig = AVATARS[personaId]
  const hasModel = !!avatarConfig

  // Build the 3D head + audio-driven viseme pipeline for the selected persona.
  useEffect(() => {
    const container = containerRef.current
    const avatar = AVATARS[personaId]
    if (!container || !avatar) return

    let cancelled = false
    let local: HeadInstance | null = null

    const setup = async (): Promise<HeadInstance | null> => {
      const head = new TalkingHead(container, {
        cameraView: 'upper',
        lipsyncModules: [], // visemes come from HeadAudio, not TTS text
      })

      // mtAvatar (morph targets) exists only after the avatar has loaded.
      await head.showAvatar({ url: avatar.url, body: avatar.body, avatarMood: 'neutral' })
      if (cancelled) {
        disposeHead(head)
        return null
      }

      await head.audioCtx.audioWorklet.addModule(HEADAUDIO_WORKLET_URL)
      // Resolve to an absolute runtime URL so Vite can't constant-fold the
      // literal and try to bundle this /public ESM through its transforms.
      const moduleUrl = new URL(HEADAUDIO_MODULE_URL, window.location.origin).href
      const { HeadAudio } = (await import(/* @vite-ignore */ moduleUrl)) as HeadAudioModule
      if (cancelled) {
        disposeHead(head)
        return null
      }

      const headaudio = new HeadAudio(head.audioCtx, {
        parameterData: { vadGateActiveDb: -40, vadGateInactiveDb: -60 },
      })
      await headaudio.loadModel(HEADAUDIO_MODEL_URL)
      if (cancelled) {
        try {
          headaudio.disconnect()
        } catch {}
        disposeHead(head)
        return null
      }

      headaudio.onvalue = (key, value) => {
        const mt = head.mtAvatar[key]
        if (mt) {
          mt.newvalue = value
          mt.needsUpdate = true
        }
      }
      // Run HeadAudio's viseme easing inside TalkingHead's render loop.
      head.opt.update = headaudio.update.bind(headaudio)

      local = { head, headaudio }
      return local
    }

    const instance = setup().catch((e) => {
      console.error('Avatar setup failed:', e)
      return null
    })
    instanceRef.current = instance

    return () => {
      cancelled = true
      if (instanceRef.current === instance) instanceRef.current = null
      // Wait for setup to settle, then dispose exactly what THIS mount made.
      void instance.then(() => {
        if (!local) return
        try {
          local.headaudio.disconnect()
        } catch {}
        disposeHead(local.head)
      })
    }
  }, [personaId])

  // Feed the OpenAI Realtime remote track into HeadAudio for viseme detection.
  // Playback stays on the session's own <audio> element, so we must NOT route
  // this into TalkingHead's speaker path (that would double the audio).
  //
  // A HeadAudio node has 0 outputs, so `source → headaudio` is a dead-end
  // branch with no path to the AudioContext destination — the graph would
  // never pull audio through it and process() would never run. We add a
  // muted (gain 0) keep-alive path to destination so the source is actively
  // scheduled and HeadAudio receives samples, without producing any sound.
  useEffect(() => {
    if (!audioStream) return
    let cancelled = false
    let source: MediaStreamAudioSourceNode | null = null
    let keepAlive: GainNode | null = null

    const pending = instanceRef.current
    void (async () => {
      const inst = await pending
      if (cancelled || !inst) return
      const { head, headaudio } = inst
      if (head.audioCtx.state === 'closed') return

      source = head.audioCtx.createMediaStreamSource(audioStream)
      source.connect(headaudio)

      keepAlive = head.audioCtx.createGain()
      keepAlive.gain.value = 0
      source.connect(keepAlive)
      keepAlive.connect(head.audioCtx.destination)

      if (head.audioCtx.state === 'suspended') void head.audioCtx.resume()
    })()

    return () => {
      cancelled = true
      try {
        source?.disconnect()
      } catch {}
      try {
        keepAlive?.disconnect()
      } catch {}
    }
  }, [audioStream])

  // Autoplay policy: the context may have been created suspended (the avatar
  // mounts on persona selection, before any click). The "Start interview"
  // click grants user activation, so resume once the session goes live.
  useEffect(() => {
    if (!active) return
    const pending = instanceRef.current
    void pending?.then((inst) => {
      const ctx = inst?.head.audioCtx
      if (ctx && ctx.state === 'suspended') void ctx.resume()
    })
  }, [active])

  return (
    <div className="avatar-container">
      {hasModel ? (
        <div
          ref={containerRef}
          className="avatar-canvas"
          style={
            avatarConfig?.bg
              ? {
                  backgroundImage: `url(${avatarConfig.bg})`,
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                }
              : undefined
          }
        />
      ) : (
        <div className="avatar-placeholder">{personaName.charAt(0)}</div>
      )}
      <div className="avatar-name">{personaName}</div>
    </div>
  )
}

// Dispose a TalkingHead and close its AudioContext, tolerating an already
// closed context (StrictMode / rapid remounts).
function disposeHead(head: TalkingHead) {
  const ctx = head.audioCtx
  try {
    head.dispose()
  } catch {}
  if (ctx && ctx.state !== 'closed') void ctx.close().catch(() => {})
}
