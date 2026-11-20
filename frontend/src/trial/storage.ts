import type { TrialStart } from '../api'

/**
 * The briefing outlives a page refresh within the tab, so a guest who reloads
 * the interview page still sees the goal and the right time limit. Per-tab
 * convenience only: the server owns the real limits.
 */
const KEY = 'ses-trial'

export const saveTrial = (trial: TrialStart) => {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(trial))
  } catch {
    // Storage blocked (private mode etc.): the pages fall back to defaults.
  }
}

export const loadTrial = (): TrialStart | null => {
  try {
    const raw = sessionStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as TrialStart) : null
  } catch {
    return null
  }
}

export const clearTrial = () => {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    // nothing to clear
  }
}

/**
 * Only hides the login link. The server's own cookie is what refuses a second
 * trial; this just avoids offering one it will refuse.
 */
const USED_KEY = 'ses-trial-used'

export const markTrialUsed = () => {
  try {
    localStorage.setItem(USED_KEY, '1')
  } catch {
    // Storage blocked: the link stays, and the server answers instead.
  }
}

export const trialUsed = (): boolean => {
  try {
    return localStorage.getItem(USED_KEY) === '1'
  } catch {
    return false
  }
}

export const formatClock = (seconds: number) => {
  const s = Math.max(0, Math.ceil(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
