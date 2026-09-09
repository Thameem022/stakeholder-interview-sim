import { FormEvent, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { AuthError, login } from '../api'
import { useAuth } from './AuthContext'
import AuthLayout from './AuthLayout'
import { EMAIL_DOMAIN, isAllowedDomain } from './config'
import { Banner, FieldError, FieldLabel, PrimaryButton, inputClasses } from './ui'

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { refresh } = useAuth()

  // Set when the interceptor bounced us here off a 401 mid-session.
  const expired = (location.state as { reason?: string } | null)?.reason === 'session_expired'

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [remember, setRemember] = useState(true)
  const [touchedEmail, setTouchedEmail] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [succeeded, setSucceeded] = useState(false)

  // Only nag once the field has been touched, so the error doesn't appear
  // while someone is still typing the first character.
  const domainInvalid = touchedEmail && email.length > 0 && !isAllowedDomain(email)
  const disabled = loading || succeeded || !email || !password || !isAllowedDomain(email)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (disabled) return

    setLoading(true)
    setError('')
    try {
      await login(email, password, remember)
      // Pull the identity into context before leaving, so the header shows
      // the signed-in user on the very first render of the app.
      await refresh()
      setSucceeded(true)
      // Let the confirmation register before the route changes underneath it.
      setTimeout(() => navigate('/'), 700)
    } catch (err) {
      setError(err instanceof AuthError ? err.message : 'Something went wrong.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthLayout variant="login">
      <form onSubmit={submit} noValidate>
        <h2 className="text-[23px] font-semibold tracking-[-0.01em] lg:text-[31px]">Sign in</h2>
        <p className="mt-1.5 text-[13.5px] leading-[1.6] text-muted lg:mt-2 lg:text-[15px]">
          Use your WPI credentials to continue.
        </p>

        {expired && !error && !succeeded && (
          <div className="mt-[22px]">
            <Banner tone="error">Your session has ended. Please sign in again.</Banner>
          </div>
        )}
        {error && (
          <div className="mt-[22px]">
            <Banner tone="error">{error}</Banner>
          </div>
        )}
        {succeeded && (
          <div className="mt-[22px]">
            <Banner tone="success">Signed in. Taking you to the simulator…</Banner>
          </div>
        )}

        <div className="mt-[30px] flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <FieldLabel htmlFor="wpi-email">WPI Email</FieldLabel>
            <input
              id="wpi-email"
              type="email"
              autoComplete="username"
              placeholder={`yourname@${EMAIL_DOMAIN}`}
              value={email}
              aria-describedby="email-msg"
              aria-invalid={domainInvalid}
              onChange={(e) => {
                setEmail(e.target.value)
                setTouchedEmail(true)
                setError('')
              }}
              className={inputClasses(domainInvalid)}
            />
            {domainInvalid && (
              <FieldError id="email-msg">
                Use your @{EMAIL_DOMAIN} address to sign in.
              </FieldError>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <FieldLabel htmlFor="wpi-pw">Password</FieldLabel>
            <div className="relative flex items-center">
              <input
                id="wpi-pw"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                value={password}
                aria-describedby="pw-msg"
                onChange={(e) => {
                  setPassword(e.target.value)
                  setError('')
                }}
                className={inputClasses(!!error, 'pr-[66px]')}
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-pressed={showPassword}
                className="absolute right-2 px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-brand"
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
            {error && <FieldError id="pw-msg">Check your password and try again.</FieldError>}
          </div>

          <div className="flex items-center justify-between gap-4">
            <label className="flex cursor-pointer items-center gap-[9px] text-[13.5px] text-muted">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="h-4 w-4 cursor-pointer accent-brand"
              />
              Remember me
            </label>
            {/* Password reset needs email delivery, which does not exist yet.
                Shown but inert, rather than silently missing from the design. */}
            <span
              title="Available once email delivery is set up"
              aria-disabled="true"
              className="cursor-not-allowed text-[13.5px] text-muted-faint underline underline-offset-[3px]"
            >
              Forgot password?
            </span>
          </div>

          <PrimaryButton disabled={disabled} loading={loading} loadingLabel="Signing in">
            Sign in
          </PrimaryButton>

          <p className="mt-0.5 text-[13.5px] text-muted">
            No account yet?{' '}
            <button
              type="button"
              onClick={() => navigate('/register')}
              className="font-semibold text-brand underline underline-offset-[3px] hover:text-brand-hover"
            >
              Register
            </button>
          </p>
        </div>
      </form>
    </AuthLayout>
  )
}
