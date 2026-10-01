import { ReactNode, useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from './AuthContext'
import { clearSignInAttempt, startSignIn } from './sso'

/**
 * Gates a route on being signed in.
 *
 * The `loading` branch is the whole point. `user` is null both before
 * /api/auth/me has answered and after it says "signed out" — redirecting on
 * the first would bounce a perfectly valid session to the login page on every
 * hard refresh.
 *
 * Signed out means straight to WPI single sign-on — there is no SES login
 * form. If a sign-in was attempted moments ago and still no session exists,
 * stop and explain on /login rather than loop through Entra forever.
 *
 * This is convenience, not security: the API enforces the same thing, so a
 * user who bypassed this would see an empty shell and a string of 401s.
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  const [looped, setLooped] = useState(false)

  useEffect(() => {
    if (loading) return
    if (user) {
      clearSignInAttempt()
      return
    }
    if (!startSignIn(location.pathname + location.search)) setLooped(true)
  }, [loading, user, location.pathname, location.search])

  if (looped) {
    return <Navigate to="/login?error=session_not_established" replace />
  }

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <span
          role="status"
          aria-label={loading ? 'Loading' : 'Redirecting to WPI sign-in'}
          className="h-6 w-6 animate-spin rounded-full border-2 border-line-soft border-t-brand"
        />
      </div>
    )
  }

  return <>{children}</>
}
