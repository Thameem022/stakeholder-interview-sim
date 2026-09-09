import { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from './AuthContext'

/**
 * Gates a route on being signed in.
 *
 * The `loading` branch is the whole point. `user` is null both before
 * /api/auth/me has answered and after it says "signed out" — redirecting on
 * the first would bounce a perfectly valid session to the login page on every
 * hard refresh.
 *
 * This is convenience, not security: the API enforces the same thing, so a
 * user who bypassed this would see an empty shell and a string of 401s.
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <span
          role="status"
          aria-label="Loading"
          className="h-6 w-6 animate-spin rounded-full border-2 border-line-soft border-t-brand"
        />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <>{children}</>
}
