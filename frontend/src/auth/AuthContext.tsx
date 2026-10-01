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
import { startSignIn } from './sso'

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
        // Back through SSO; usually silent while the WPI session is alive.
        if (!startSignIn(window.location.pathname + window.location.search)) {
          navigate('/login?error=session_expired', { replace: true })
        }
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
      // Leave even if the request failed: the cookie may already be gone.
      // A full page load to the landing page, not a route change: clearing the
      // user first would let a protected route see "signed out" and send the
      // browser straight back through single sign-on. The reload also drops
      // every bit of in-memory state from the session.
      window.location.replace('/login?signed_out=1')
    }
  }, [])

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
