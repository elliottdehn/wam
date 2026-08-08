/**
 * Hashing, secrets, and the similarity measure.
 */

const enc = new TextEncoder()

async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', enc.encode(input))
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * A model id is a content hash of its source, so immutability stops being a
 * rule to enforce and becomes a property we get. Truncated to 128 bits: still
 * far past any collision we could reach, and short enough to live in a URL.
 */
export async function sourceId(source: string): Promise<string> {
  return (await sha256Hex(`wam-source:${source}`)).slice(0, 32)
}

/** 256 bits of entropy, URL-safe, never stored. */
export function mintSecret(): string {
  const raw = crypto.getRandomValues(new Uint8Array(32))
  return btoa(String.fromCharCode(...raw))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

/**
 * Two derivations from one secret, with domain separation so that neither
 * stored value can be used to obtain the other.
 *
 *  - `secretHash` is the auth lookup key.
 *  - `bucketId` is safe to show in public, and is what `canonical` is keyed on.
 *
 * Neither is reversible, so a leaked database hands over no delete rights.
 */
export async function deriveBucket(secret: string) {
  return {
    secretHash: await sha256Hex(`wam-auth:${secret}`),
    bucketId: (await sha256Hex(`wam-bucket:${secret}`)).slice(0, 24),
  }
}

/**
 * How much two sources share, 0..1.
 *
 * Declared parentage is a claim we cannot verify, so we measure it instead and
 * show what we measured. This is deliberately a *line* measure rather than a
 * character one: WAM is line-oriented, an edit is usually a changed ring or
 * bone, and a character diff would report a trivially-retuned model as barely
 * related because every number moved a digit.
 *
 * Sørensen-Dice over the multiset of significant lines — multiset so that a
 * file with twenty identical `ring` lines is not treated as having one.
 * Blank lines and whole-line comments are dropped; they are the part of a file
 * most likely to differ without the model differing at all.
 */
export function similarity(a: string, b: string): number {
  const A = lineCounts(a)
  const B = lineCounts(b)
  if (A.total === 0 && B.total === 0) return 1
  if (A.total === 0 || B.total === 0) return 0
  let shared = 0
  for (const [line, count] of A.counts) {
    shared += Math.min(count, B.counts.get(line) ?? 0)
  }
  return Math.round(((2 * shared) / (A.total + B.total)) * 1000) / 1000
}

function lineCounts(src: string) {
  const counts = new Map<string, number>()
  let total = 0
  for (const raw of src.split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    counts.set(line, (counts.get(line) ?? 0) + 1)
    total++
  }
  return { counts, total }
}
