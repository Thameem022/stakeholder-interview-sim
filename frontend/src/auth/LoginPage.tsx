import { useLocation } from 'react-router-dom'

import AuthLayout from './AuthLayout'
import { startSignIn } from './sso'
import { Banner } from './ui'

/**
 * Not a login form — there are no SES passwords. Signed-out users are sent
 * straight to WPI single sign-on; they only land here when a sign-in failed,
 * after signing out, or when a session could not be established. The codes
 * come from /api/auth/callback.
 */
const MESSAGES: Record<string, string> = {
  not_assigned:
    "Your WPI account isn't enrolled for the simulator. If you're in ID2050, contact your instructor.",
  wrong_domain: 'Please sign in with your WPI account.',
  account_disabled: 'This account has been disabled. Contact your instructor if this is unexpected.',
  idp_access_denied: 'Sign-in was cancelled.',
  sso_unavailable: 'WPI sign-in is unavailable right now. Please try again in a few minutes.',
  sso_not_configured: "Sign-in isn't set up on this server yet.",
  session_not_established:
    "You signed in, but the session didn't stick. Check that cookies are allowed for this site, then try again.",
  session_expired: 'Your session ended. Sign in again to continue.',
}
const GENERIC = "Sign-in couldn't be completed. Please try again."

export default function LoginPage() {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  const error = params.get('error')
  const signedOut = params.has('signed_out')
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  return (
    <AuthLayout>
      <h2 className="text-[23px] font-semibold tracking-[-0.01em] lg:text-[31px]">Sign in</h2>
      <p className="mt-1.5 text-[13.5px] leading-[1.6] text-muted lg:mt-2 lg:text-[15px]">
        Use your WPI account. You'll be taken to the WPI sign-in page.
      </p>

      <div className="mt-6 space-y-4">
        {error && <Banner tone="error">{MESSAGES[error] ?? GENERIC}</Banner>}
        {signedOut && !error && <Banner tone="success">You've signed out of the simulator.</Banner>}

        <button
          type="button"
          onClick={() => startSignIn(from, { force: true })}
          className="flex h-[50px] w-full items-center justify-center rounded-lg bg-brand text-[13px] font-semibold uppercase tracking-[0.1em] text-white transition-colors hover:bg-brand-hover"
        >
          Sign in with your WPI account
        </button>
      </div>
    </AuthLayout>
  )
}
