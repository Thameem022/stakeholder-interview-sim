// Minimal typings for @met4citizen/talkinghead (the package ships no types).
// Only the surface used by components/Avatar.tsx is declared.
declare module '@met4citizen/talkinghead' {
  export interface MorphTarget {
    newvalue: number | null
    needsUpdate: boolean
  }

  export interface AvatarSpec {
    url: string
    body?: 'M' | 'F'
    avatarMood?: string
    [key: string]: unknown
  }

  export class TalkingHead {
    constructor(node: HTMLElement, opt?: Record<string, unknown>)
    audioCtx: AudioContext
    mtAvatar: Record<string, MorphTarget>
    opt: { update?: ((dt: number) => void) | null } & Record<string, unknown>
    showAvatar(avatar: AvatarSpec, onprogress?: (ev: ProgressEvent) => void): Promise<void>
    start(): void
    stop(): void
    dispose(): void
  }
}
