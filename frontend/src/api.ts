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

export interface RealtimeToken {
  ephemeral_key: string
  session_id: string
  model: string
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

export const postRetrieve = async (
  personaId: string,
  query: string,
  sessionId: string
): Promise<{ text: string }> => {
  const { data } = await apiClient.post(
    '/api/realtime/retrieve',
    { persona_id: personaId, query, session_id: sessionId },
    // Background call during a live interview — see skipAuthRedirect.
    { skipAuthRedirect: true }
  )
  return data
}

export const postTranscript = async (
  sessionId: string,
  role: 'user' | 'assistant',
  text: string,
  ended?: boolean
): Promise<{ ok: boolean; turns: number }> => {
  const { data } = await apiClient.post(
    '/api/realtime/transcript',
    { session_id: sessionId, role, text, ended },
    { skipAuthRedirect: true }
  )
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
  failed: string[]

  constructor(message: string, code: string, status: number, failed: string[] = []) {
    super(message)
    this.name = 'AuthError'
    this.code = code
    this.status = status
    this.failed = failed
  }
}

const asAuthError = (error: unknown): AuthError => {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status ?? 0
    const detail = error.response?.data?.detail
    if (detail && typeof detail === 'object') {
      return new AuthError(detail.message, detail.code, status, detail.failed ?? [])
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

export const registerAccount = (firstName: string, lastName: string, email: string) =>
  authPost<{ status: string }>('/api/auth/register', {
    first_name: firstName,
    last_name: lastName,
    email,
  })

export const setPassword = (email: string, tempPassword: string, newPassword: string) =>
  authPost<AuthUser>('/api/auth/set-password', {
    email,
    temp_password: tempPassword,
    new_password: newPassword,
  })

export const login = (email: string, password: string, remember: boolean) =>
  authPost<AuthUser>('/api/auth/login', { email, password, remember })

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
