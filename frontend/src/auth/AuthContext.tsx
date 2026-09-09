/**
 * Who is signed in, for the whole app.
 *
 * The session itself lives in an httpOnly cookie the browser sends on its own,
 * so this holds no token — only the identity the server reports, which is why
 * `loading` matters: until /api/auth/me answers, "signed out" and "not asked
 * yet" are different states and must not render the same.
 */

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { useNavigate } from 'react-router-dom'

import { AuthUser, getMe, logout as logoutRequest, setUnauthorizedHandler } from '../api'

interface AuthState {
  user: AuthUser | null
  loading: boolean
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const checking = useRef(false)

  const refresh = useCallback(async () => {
    try {
      setUser(await getMe())
    } catch {
      // A transport failure is not proof of being signed out; leave whatever
      // identity we already had rather than bouncing the user to /login.
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    // Fired when any non-auth endpoint answers 401 — the session lapsed or was
    // revoked server-side while the tab stayed open.
    setUnauthorizedHandler(async () => {
      // One endpoint returning 401 is a claim, not proof. /me is the
      // authoritative answer, and the interceptor skips /api/auth/* so asking
      // cannot recurse. The ref collapses a burst — several calls failing
      // together should ask once, not once each.
      if (checking.current) return
      checking.current = true
      try {
        const stillSignedIn = await getMe()
        if (stillSignedIn) {
          setUser(stillSignedIn)
          return
        }
        setUser(null)
        navigate('/login', { replace: true, state: { reason: 'session_expired' } })
      } finally {
        checking.current = false
      }
    })
    return () => setUnauthorizedHandler(null)
  }, [navigate])

  const signOut = useCallback(async () => {
    try {
      await logoutRequest()
    } finally {
      // Clear locally even if the request failed: the cookie may already be
      // gone, and leaving a stale name in the header would be worse.
      setUser(null)
      navigate('/login', { replace: true })
    }
  }, [navigate])

  return (
    <AuthContext.Provider value={{ user, loading, signOut, refresh }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
