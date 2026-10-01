import { signInUrl } from '../api'

/**
 * Leave for WPI single sign-on (Entra ID), coming back to `returnTo`.
 *
 * Returns false instead of navigating when a sign-in was already attempted a
 * moment ago: if the session cookie is not sticking (blocked cookies, a
 * misconfigured proxy), bouncing to Entra again would loop forever. The
 * caller then shows /login with an explanation instead.
 */
const ATTEMPT_KEY = 'ses:sso-attempt'
const LOOP_WINDOW_MS = 30_000

export function startSignIn(returnTo: string, { force = false } = {}): boolean {
  try {
    const last = Number(sessionStorage.getItem(ATTEMPT_KEY) ?? 0)
    if (!force && Date.now() - last < LOOP_WINDOW_MS) return false
    sessionStorage.setItem(ATTEMPT_KEY, String(Date.now()))
  } catch {
    // Storage unavailable (private mode, blocked): no loop guard, but sign-in
    // still has to work.
  }
  window.location.assign(signInUrl(returnTo))
  return true
}

/** Called once the app sees a live session, so the next expiry may redirect. */
export function clearSignInAttempt(): void {
  try {
    sessionStorage.removeItem(ATTEMPT_KEY)
  } catch {
    // nothing to clear
  }
}
