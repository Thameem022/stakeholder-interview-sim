/**
 * Auth behaviour constants, mirroring the mockup's configurable props.
 *
 * These drive presentation only. Every rule here is also enforced by the
 * backend — the domain check in `_clean_email`, the password policy in
 * `password_policy_failures`. Changing a value here changes what the user
 * sees, not what the server accepts.
 */

export const REQUIRE_WPI_DOMAIN = true
export const EMAIL_DOMAIN = 'wpi.edu'
export const RESEND_SECONDS = 60
export const MIN_PASSWORD_LENGTH = 12

/**
 * The registration password checklist.
 *
 * Labels are byte-identical to the backend's, which returns unmet rules as a
 * `failed` array on a 400. Keeping them in sync lets a server rejection light
 * up the same rows the live checklist uses, instead of needing a second
 * mapping. Change one side and you must change the other.
 */
export const PASSWORD_RULES: { label: string; test: (value: string) => boolean }[] = [
  { label: `${MIN_PASSWORD_LENGTH}+ characters`, test: (v) => v.length >= MIN_PASSWORD_LENGTH },
  { label: 'One uppercase letter', test: (v) => /[A-Z]/.test(v) },
  { label: 'One number', test: (v) => /[0-9]/.test(v) },
  { label: 'One symbol', test: (v) => /[^A-Za-z0-9]/.test(v) },
]

/** Indexed by how many rules are met, so index 0 is the empty-field case. */
export const STRENGTH_LABELS = ['Enter a password', 'Weak', 'Fair', 'Good', 'Strong']

export const isAllowedDomain = (email: string): boolean =>
  !REQUIRE_WPI_DOMAIN || new RegExp(`@${EMAIL_DOMAIN}$`, 'i').test(email.trim())

export const passwordRulesMet = (value: string): boolean[] =>
  PASSWORD_RULES.map((rule) => rule.test(value))
