/**
 * The secret lives in localStorage and nowhere else.
 *
 * It is a bucket handle, not an account: it authorises delete, and it is what
 * makes a later upload a *next version* rather than a branch off your own
 * work. There is no recovery, so the UI's job is to make sure the user sees it
 * at least once.
 */
const KEY = 'wamshare.secret'
const ACK = 'wamshare.secret.acknowledged'

export function getSecret(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    // Private browsing can refuse storage entirely. Uploading still works; the
    // secret just will not survive the tab.
    return null
  }
}

export function setSecret(secret: string) {
  try {
    localStorage.setItem(KEY, secret)
    localStorage.removeItem(ACK)
  } catch {
    /* nothing we can do; the caller still shows it on screen */
  }
}

export function forgetSecret() {
  try {
    localStorage.removeItem(KEY)
    localStorage.removeItem(ACK)
  } catch { /* empty */ }
}

/** Whether the user has confirmed they have written the secret down. */
export function secretAcknowledged(): boolean {
  try {
    return localStorage.getItem(ACK) === '1'
  } catch {
    return true
  }
}

export function acknowledgeSecret() {
  try {
    localStorage.setItem(ACK, '1')
  } catch { /* empty */ }
}
