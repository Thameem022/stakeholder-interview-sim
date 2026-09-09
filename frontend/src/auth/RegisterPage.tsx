import { FormEvent, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { AuthError, registerAccount, setPassword } from '../api'
import { useAuth } from './AuthContext'
import AuthLayout from './AuthLayout'
import {
  EMAIL_DOMAIN,
  PASSWORD_RULES,
  RESEND_SECONDS,
  STRENGTH_LABELS,
  isAllowedDomain,
  passwordRulesMet,
} from './config'
import { Banner, FieldError, FieldLabel, PrimaryButton, inputClasses } from './ui'

const STEPS = ['Request', 'Verify', 'Password']

/** Indexed by rules met, so a stronger password moves rose → crimson. */
const SEGMENT_CLASSES = ['bg-line-soft', 'bg-brand-rose', 'bg-brand-rose', 'bg-brand', 'bg-brand']

type BannerState = { text: string; action: 'restart' | 'signin' | null } | null

function Stepper({ step }: { step: number }) {
  return (
    <div className="mb-[30px] flex items-center">
      {STEPS.map((label, index) => {
        const number = index + 1
        const reached = step >= number
        return (
          <div key={label} className="flex flex-1 items-center last:flex-none">
            <div className="flex items-center gap-[9px]">
              <div
                className={[
                  'flex h-[26px] w-[26px] items-center justify-center rounded-full border border-brand text-[12px] font-semibold',
                  reached ? 'bg-brand text-white' : 'bg-white text-brand',
                ].join(' ')}
              >
                {number}
              </div>
              <span
                className={[
                  'text-[11px] font-semibold uppercase tracking-[0.09em]',
                  reached ? 'text-ink' : 'text-muted-faint',
                ].join(' ')}
              >
                {label}
              </span>
            </div>
            {number < STEPS.length && (
              <div
                className={`mx-3 h-0.5 flex-1 ${step > number ? 'bg-brand' : 'bg-line-soft'}`}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

export default function RegisterPage() {
  const navigate = useNavigate()
  const { refresh } = useAuth()

  const [step, setStep] = useState(1)
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [email, setEmail] = useState('')
  const [sentTo, setSentTo] = useState('')
  const [resendLeft, setResendLeft] = useState(0)

  const [temp, setTemp] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')

  const [loading, setLoading] = useState(false)
  const [banner, setBanner] = useState<BannerState>(null)
  const [tempError, setTempError] = useState('')
  const [toast, setToast] = useState(false)

  useEffect(() => {
    if (resendLeft <= 0) return
    const timer = setInterval(() => setResendLeft((n) => Math.max(0, n - 1)), 1000)
    return () => clearInterval(timer)
  }, [resendLeft])

  const domainInvalid = email.length > 0 && !isAllowedDomain(email)
  const step1Disabled = loading || !firstName.trim() || !lastName.trim() || !email || domainInvalid

  const rulesMet = passwordRulesMet(newPassword)
  const metCount = rulesMet.filter(Boolean).length
  const mismatch = confirm.length > 0 && newPassword !== confirm
  const step3Disabled = loading || !temp || metCount < PASSWORD_RULES.length || !confirm || mismatch

  const restart = () => {
    setStep(1)
    setBanner(null)
    setTempError('')
    setTemp('')
    setNewPassword('')
    setConfirm('')
  }

  const submitRequest = async (event: FormEvent) => {
    event.preventDefault()
    if (step1Disabled) return
    setLoading(true)
    setBanner(null)
    try {
      await registerAccount(firstName, lastName, email)
      setSentTo(email)
      setResendLeft(RESEND_SECONDS)
      setStep(2)
    } catch (err) {
      setBanner({ text: err instanceof AuthError ? err.message : 'Something went wrong.', action: null })
    } finally {
      setLoading(false)
    }
  }

  const resend = async () => {
    if (resendLeft > 0 || loading) return
    setLoading(true)
    try {
      await registerAccount(firstName, lastName, sentTo)
      setResendLeft(RESEND_SECONDS)
    } catch {
      // Deliberately quiet: the endpoint reports success regardless, so a
      // failure here is a transport problem, not something the user can act on.
    } finally {
      setLoading(false)
    }
  }

  const submitPassword = async (event: FormEvent) => {
    event.preventDefault()
    if (step3Disabled) return
    setLoading(true)
    setBanner(null)
    setTempError('')
    try {
      await setPassword(sentTo, temp, newPassword)
      // set-password signs the user in, so pull that identity into context
      // before redirecting into the app.
      await refresh()
      setToast(true)
      setTimeout(() => navigate('/'), 1200)
    } catch (err) {
      const error = err instanceof AuthError ? err : null
      if (error?.code === 'invalid_temp_password') {
        setTempError('That temporary password is not correct.')
      } else if (error?.code === 'account_exists') {
        setBanner({ text: error.message, action: 'signin' })
      } else if (
        error?.code === 'expired' ||
        error?.code === 'no_pending_registration' ||
        error?.code === 'too_many_attempts'
      ) {
        setBanner({ text: error.message, action: 'restart' })
      } else {
        setBanner({ text: error?.message ?? 'Something went wrong.', action: null })
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthLayout variant="register">
      <Stepper step={step} />

      {banner && (
        <div className="mb-6">
          <Banner tone="error">
            {banner.text}{' '}
            {banner.action === 'restart' && (
              <button type="button" onClick={restart} className="font-semibold underline">
                Request a new one
              </button>
            )}
            {banner.action === 'signin' && (
              <button
                type="button"
                onClick={() => navigate('/login')}
                className="font-semibold underline"
              >
                Sign in
              </button>
            )}
          </Banner>
        </div>
      )}

      {step === 1 && (
        <form onSubmit={submitRequest} noValidate>
          <h2 className="text-[23px] font-semibold tracking-[-0.01em] lg:text-[31px]">
            Request access
          </h2>
          <p className="mt-1.5 text-[13.5px] leading-[1.6] text-muted lg:mt-2 lg:text-[15px]">
            We'll email a temporary password to your WPI address.
          </p>

          <div className="mt-[30px] flex flex-col gap-5">
            <div className="flex flex-col gap-5 lg:flex-row lg:gap-4">
              <div className="flex flex-1 flex-col gap-2">
                <FieldLabel htmlFor="reg-first">First name</FieldLabel>
                <input
                  id="reg-first"
                  type="text"
                  placeholder="Alex"
                  autoComplete="given-name"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  className={inputClasses(false)}
                />
              </div>
              <div className="flex flex-1 flex-col gap-2">
                <FieldLabel htmlFor="reg-last">Last name</FieldLabel>
                <input
                  id="reg-last"
                  type="text"
                  placeholder="Rivera"
                  autoComplete="family-name"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  className={inputClasses(false)}
                />
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <FieldLabel htmlFor="reg-email">WPI Email</FieldLabel>
              <input
                id="reg-email"
                type="email"
                placeholder={`yourname@${EMAIL_DOMAIN}`}
                autoComplete="email"
                value={email}
                aria-describedby="reg-email-msg"
                aria-invalid={domainInvalid}
                onChange={(e) => setEmail(e.target.value)}
                className={inputClasses(domainInvalid)}
              />
              <div
                id="reg-email-msg"
                aria-live="polite"
                className={`text-[12.5px] leading-[1.5] ${domainInvalid ? 'text-danger' : 'text-muted-soft'}`}
              >
                {domainInvalid
                  ? `Registration is limited to @${EMAIL_DOMAIN} addresses.`
                  : `Only @${EMAIL_DOMAIN} addresses can register.`}
              </div>
            </div>

            <PrimaryButton disabled={step1Disabled} loading={loading} loadingLabel="Sending">
              Send verification email
            </PrimaryButton>

            <p className="text-[13.5px] text-muted">
              Already have access?{' '}
              <button
                type="button"
                onClick={() => navigate('/login')}
                className="font-semibold text-brand underline underline-offset-[3px] hover:text-brand-hover"
              >
                Sign in
              </button>
            </p>
          </div>
        </form>
      )}

      {step === 2 && (
        <div className="flex flex-col items-center text-center">
          <div className="flex h-[74px] w-[74px] items-center justify-center rounded-full bg-brand">
            <div className="relative h-[22px] w-8 overflow-hidden rounded-sm border-2 border-white">
              <div className="absolute -top-2 left-1/2 h-[22px] w-[22px] -translate-x-1/2 rotate-45 border-b-2 border-r-2 border-white" />
            </div>
          </div>
          <h2 className="mt-6 text-[28px] font-semibold">Check your inbox</h2>
          <p className="mt-2.5 max-w-[340px] text-[15px] leading-[1.7] text-muted">
            We sent a temporary password to{' '}
            <strong className="font-semibold text-ink">{sentTo}</strong>. It expires in 24 hours.
          </p>

          <div className="mt-[30px] flex w-full flex-col gap-3">
            <button
              type="button"
              onClick={resend}
              disabled={resendLeft > 0 || loading}
              className={[
                'h-12 w-full rounded-lg border bg-white text-[12.5px] font-semibold uppercase tracking-[0.08em]',
                resendLeft > 0 || loading
                  ? 'cursor-not-allowed border-line-soft text-muted-faint'
                  : 'border-line text-brand hover:border-brand',
              ].join(' ')}
            >
              {resendLeft > 0 ? `Resend email in ${resendLeft}s` : 'Resend email'}
            </button>
            <button
              type="button"
              onClick={restart}
              className="text-[13.5px] text-muted underline underline-offset-[3px]"
            >
              Use a different address
            </button>
            {/* No email is sent yet, so this is the route to step 3. It is
                also the mockup's own affordance, not an invention. */}
            <button
              type="button"
              onClick={() => setStep(3)}
              className="mt-1.5 font-plex text-[11px] text-muted-faint hover:text-brand"
            >
              [demo] open emailed link →
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <form onSubmit={submitPassword} noValidate>
          <h2 className="text-[23px] font-semibold tracking-[-0.01em] lg:text-[31px]">
            Set your password
          </h2>
          <p className="mt-1.5 text-[13.5px] leading-[1.6] text-muted lg:mt-2 lg:text-[15px]">
            First sign-in. Paste the temporary password we emailed you.
          </p>

          <div className="mt-7 flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <FieldLabel htmlFor="temp-pw">Temporary password</FieldLabel>
              <input
                id="temp-pw"
                type="text"
                placeholder="e.g. 7QF-42KD-XM"
                value={temp}
                aria-invalid={!!tempError}
                onChange={(e) => {
                  setTemp(e.target.value)
                  setTempError('')
                }}
                className={inputClasses(!!tempError, 'font-plex text-[14.5px]')}
              />
              {tempError && <FieldError id="temp-msg">{tempError}</FieldError>}
            </div>

            <div className="flex flex-col gap-2">
              <FieldLabel htmlFor="new-pw">New password</FieldLabel>
              <input
                id="new-pw"
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className={inputClasses(false)}
              />

              <div className="mt-1 flex gap-1.5" aria-hidden="true">
                {[0, 1, 2, 3].map((index) => (
                  <div
                    key={index}
                    className={`h-[5px] flex-1 rounded-[3px] ${
                      metCount > index ? SEGMENT_CLASSES[metCount] : 'bg-line-soft'
                    }`}
                  />
                ))}
              </div>
              <div aria-live="polite" className="text-[12px] tracking-[0.04em] text-muted">
                {STRENGTH_LABELS[metCount]}
              </div>

              <ul className="mt-2 flex flex-col gap-1.5">
                {PASSWORD_RULES.map((rule, index) => (
                  <li
                    key={rule.label}
                    className={`flex items-center gap-2 text-[12.5px] ${
                      rulesMet[index] ? 'text-success' : 'text-muted-soft'
                    }`}
                  >
                    <span className="w-3 font-plex">{rulesMet[index] ? '✓' : '·'}</span>
                    {rule.label}
                  </li>
                ))}
              </ul>
            </div>

            <div className="flex flex-col gap-2">
              <FieldLabel htmlFor="cp-pw">Confirm new password</FieldLabel>
              <input
                id="cp-pw"
                type="password"
                autoComplete="new-password"
                value={confirm}
                aria-describedby="cp-msg"
                aria-invalid={mismatch}
                onChange={(e) => setConfirm(e.target.value)}
                className={inputClasses(mismatch)}
              />
              {mismatch && <FieldError id="cp-msg">Passwords don't match yet.</FieldError>}
            </div>

            <PrimaryButton disabled={step3Disabled} loading={loading} loadingLabel="Saving">
              Set password &amp; continue
            </PrimaryButton>
          </div>
        </form>
      )}

      {toast && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg bg-ink px-[22px] py-3.5 text-[14px] text-white shadow-[0_10px_26px_rgba(0,0,0,0.28)]"
        >
          <span className="inline-block h-2 w-2 rounded-full bg-success-dot" />
          Password set. Redirecting to your dashboard…
        </div>
      )}
    </AuthLayout>
  )
}
