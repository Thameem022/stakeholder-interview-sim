import axios from 'axios'

const apiClient = axios.create({
  baseURL: '',
  timeout: 60000,
  // The session lives in an httpOnly cookie, so every call has to carry
  // credentials — including the same-origin ones, once this is served from a
  // different host than the API.
  withCredentials: true,
})

// Double-submit CSRF: the server issues a readable cookie and rejects any write
// that does not echo it in this header. In production the cookie carries the
// __Host- prefix, so accept either name.
const CSRF_HEADER = 'X-CSRF-Token'
const CSRF_COOKIES = ['__Host-sis_csrf', 'sis_csrf']
const SAFE_METHODS = new Set(['get', 'head', 'options'])

const readCsrfCookie = (): string | null => {
  for (const part of document.cookie.split('; ')) {
    const eq = part.indexOf('=')
    if (eq > 0 && CSRF_COOKIES.includes(part.slice(0, eq))) {
      return decodeURIComponent(part.slice(eq + 1))
    }
  }
  return null
}

apiClient.interceptors.request.use(async (config) => {
  if (SAFE_METHODS.has((config.method ?? 'get').toLowerCase())) return config
  let token = readCsrfCookie()
  if (!token) {
    // Any response issues the cookie. /api/auth/me on load normally has, but a
    // write can still come first (e.g. the cookie was cleared mid-session).
    await apiClient.get('/api/health')
    token = readCsrfCookie()
  }
  if (token) config.headers.set(CSRF_HEADER, token)
  return config
})

export interface Persona {
  key: string
  display_name: string
}

export interface Voice {
  persona_id: string
  voice_id: string
}

export const getPersonas = async (): Promise<Persona[]> => {
  const { data } = await apiClient.get('/api/personas')
  return data
}

export const getVoices = async (): Promise<Voice[]> => {
  const { data } = await apiClient.get('/api/voices')
  return data
}

export const evalIqr = async (sessionId: string) => {
  const { data } = await apiClient.post('/api/eval/iqr', null, { params: { session_id: sessionId } })
  return data
}

export const evalSic = async (sessionId: string) => {
  const { data } = await apiClient.post('/api/eval/sic', null, { params: { session_id: sessionId } })
  return data
}

export const getLatestEvaluation = async (sessionId: string) => {
  const { data } = await apiClient.get(`/api/eval/sessions/${sessionId}/latest`)
  return data
}

export const healthCheck = async () => {
  const { data } = await apiClient.get('/api/health')
  return data
}

/** Starts an interview. Never an AI-provider credential — only a short-lived,
 * single-use token for the backend's own stream. */
export interface RealtimeToken {
  session_id: string
  stream_token: string
  expires_in: number
  voice_id: string
}

export interface PreSessionNotice {
  version: string
  title: string
  points: string[]
  acknowledgement: string
}

/** The canonical notice text; the server rejects a start without its version. */
export const getNotice = async (): Promise<PreSessionNotice> => {
  const { data } = await apiClient.get('/api/realtime/notice')
  return data
}

export interface ResearchConsent {
  enabled: boolean
  version: string | null
  title: string | null
  points: string[] | null
  yes_label: string | null
  no_label: string | null
  /** The student's own choice; null until they have made one. */
  consented: boolean | null
}

export const getResearchConsent = async (): Promise<ResearchConsent> => {
  const { data } = await apiClient.get('/api/research/consent')
  return data
}

export const setResearchConsent = async (
  consented: boolean,
  version: string
): Promise<ResearchConsent> => {
  const { data } = await apiClient.put('/api/research/consent', { consented, version })
  return data
}

/** The server always allocates the session id; there is no way to ask for one. */
export const getRealtimeToken = async (
  personaId: string,
  noticeVersion: string,
  voiceId?: string
): Promise<RealtimeToken> => {
  const { data } = await apiClient.post('/api/realtime/token', {
    persona_id: personaId,
    voice_id: voiceId,
    notice_version: noticeVersion,
  })
  return data
}

// --- written interview (SR-2026-052 item 2.1) --------------------------------

export interface TextTurnReply {
  /** null when the server withheld the reply — `ended` says why. */
  reply: string | null
  ended: boolean
  reason: 'guardrail' | 'time_limit' | null
}

export const startTextInterview = async (
  personaId: string,
  noticeVersion: string
): Promise<{ session_id: string; persona_id: string }> => {
  const { data } = await apiClient.post('/api/realtime/text/start', {
    persona_id: personaId,
    notice_version: noticeVersion,
  })
  return data
}

export const sendTextTurn = async (sessionId: string, text: string): Promise<TextTurnReply> => {
  const { data } = await apiClient.post(
    `/api/realtime/text/${encodeURIComponent(sessionId)}/turns`,
    { text }
  )
  return data
}

export const endTextInterview = async (sessionId: string) => {
  const { data } = await apiClient.post(`/api/realtime/text/${encodeURIComponent(sessionId)}/end`, {})
  return data
}

// --- auth -------------------------------------------------------------------

declare module 'axios' {
  export interface AxiosRequestConfig {
    /**
     * Opt this request out of the global "redirect to /login" reaction.
     *
     * For fire-and-forget calls made while an interview is live: they should
     * report an expired session through the session's own error path, which
     * can stop the microphone first, rather than yanking the route out from
     * under a user mid-sentence.
     */
    skipAuthRedirect?: boolean
  }
}

type UnauthorizedHandler = () => void

let onUnauthorized: UnauthorizedHandler | null = null

/** Registered by AuthProvider so the interceptor can route without a Router. */
export const setUnauthorizedHandler = (handler: UnauthorizedHandler | null) => {
  onUnauthorized = handler
}

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url ?? ''
    // The auth endpoints own their 401s: a failed sign-in and a signed-out
    // /me are normal answers, not expired sessions. Reacting to them here
    // would bounce the user off the login page they are already on.
    const isAuthEndpoint = url.startsWith('/api/auth/')
    const optedOut = error.config?.skipAuthRedirect === true
    if (error.response?.status === 401 && !isAuthEndpoint && !optedOut) {
      onUnauthorized?.()
    }
    return Promise.reject(error)
  }
)

export interface AuthUser {
  id: string
  email: string
  first_name: string
  last_name: string
}

/**
 * A failure from the auth endpoints.
 *
 * The backend answers with `{detail: {code, message, ...}}`, so `code` is what
 * the screens branch on and `message` is safe to show as-is — the server
 * deliberately keeps those strings vague where being specific would leak
 * whether an account exists.
 */
export class AuthError extends Error {
  code: string
  status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'AuthError'
    this.code = code
    this.status = status
  }
}

const asAuthError = (error: unknown): AuthError => {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status ?? 0
    const detail = error.response?.data?.detail
    if (detail && typeof detail === 'object') {
      return new AuthError(detail.message, detail.code, status)
    }
    if (status === 0) {
      return new AuthError("Can't reach the server. Check your connection.", 'network', 0)
    }
  }
  return new AuthError('Something went wrong. Please try again.', 'unknown', 0)
}

const authPost = async <T>(path: string, body: unknown): Promise<T> => {
  try {
    const { data } = await apiClient.post(path, body)
    return data
  } catch (error) {
    throw asAuthError(error)
  }
}

/**
 * Where the browser goes to sign in. Sign-in is WPI single sign-on (Entra ID)
 * only: this is a full-page navigation to the backend, which redirects on to
 * Microsoft and back. `returnTo` must be a path on this site.
 */
export const signInUrl = (returnTo = '/') =>
  `/api/auth/login?return_to=${encodeURIComponent(returnTo)}`

export const logout = () => authPost<{ status: string }>('/api/auth/logout', {})

/** The signed-in user, or null. A 401 here is the expected "signed out" case. */
export const getMe = async (): Promise<AuthUser | null> => {
  try {
    const { data } = await apiClient.get('/api/auth/me')
    return data
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 401) return null
    throw asAuthError(error)
  }
}

export default apiClient
