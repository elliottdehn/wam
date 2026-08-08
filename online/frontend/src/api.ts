/**
 * Client for the wamshare API.
 *
 * Every response carries `hint` / `next` / `retryable`. Those are written for
 * an agent, but they are the clearest description of what happened that exists
 * anywhere, so the UI surfaces them rather than inventing its own wording.
 */
import { getSecret, setSecret } from './secret'

export interface Envelope {
  hint?: string
  next?: string[]
  retryable?: boolean
}

export interface ModelNode {
  id: string
  title: string | null
  description: string | null
  visibility: 'private' | 'public'
  bucketId: string
  tombstoned: boolean
  createdAt: number
  publishedAt: number | null
  meta: unknown
}

export interface UploadResult extends Envelope {
  id: string
  url: string
  bucketId: string
  existing: boolean
  owned: boolean
  secret?: string
  model: ModelNode
  parents?: string[]
}

export interface Lineage extends Envelope {
  focus: string
  canonical: ModelNode[]
  tips: string[]
  branches: Array<{
    fromId: string
    id: string
    title: string | null
    bucketId: string
    similarity: number | null
  }>
}

export class ApiError extends Error {
  // Declared rather than constructor parameter properties: this project builds
  // with `erasableSyntaxOnly`, which rejects the shorthand.
  readonly status: number
  readonly hint: string
  readonly next: string[]
  readonly retryable: boolean
  readonly body: Record<string, unknown>

  constructor(
    status: number,
    hint: string,
    next: string[] = [],
    retryable = false,
    body: Record<string, unknown> = {},
  ) {
    super(hint)
    this.status = status
    this.hint = hint
    this.next = next
    this.retryable = retryable
    this.body = body
  }
}

async function call<T>(
  path: string,
  init: RequestInit & { auth?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  const secret = getSecret()
  if (secret) headers.set('x-wam-secret', secret)
  if (init.body) headers.set('content-type', 'application/json')

  const res = await fetch(`/api${path}`, { ...init, headers })
  const text = await res.text()
  let body: Record<string, unknown>
  try {
    body = JSON.parse(text)
  } catch {
    throw new ApiError(res.status, `The server returned something that is not JSON (${res.status}).`)
  }
  if (!res.ok) {
    throw new ApiError(
      res.status,
      (body.hint as string) ?? `Request failed (${res.status}).`,
      (body.next as string[]) ?? [],
      !!body.retryable,
      body,
    )
  }
  return body as T
}

export const api = {
  async upload(input: {
    source: string
    title?: string
    description?: string
    visibility: 'private' | 'public'
    parents?: string[]
    meta?: unknown
  }): Promise<UploadResult> {
    const r = await call<UploadResult>('/models', {
      method: 'POST',
      body: JSON.stringify(input),
    })
    // A minted secret arrives exactly once and can never be recovered. Persist
    // it before anything else can throw.
    if (r.secret) setSecret(r.secret)
    return r
  },

  model: (id: string) =>
    call<Envelope & { model: ModelNode; url: string; parents: Array<{ parentId: string; similarity: number | null }> }>(
      `/models/${id}`,
    ),

  async source(id: string): Promise<string> {
    const res = await fetch(`/api/models/${id}/source`)
    if (res.status === 410) throw new ApiError(410, 'This model was deleted, so its source is gone.')
    if (!res.ok) throw new ApiError(res.status, `Could not fetch the source (${res.status}).`)
    return res.text()
  },

  lineage: (id: string) => call<Lineage>(`/lineage/${id}`),

  successors: (id: string) =>
    call<Envelope & { successors: Array<{ id: string; title: string | null; similarity: number | null }> }>(
      `/models/${id}/successors`,
    ),

  publish: (id: string) =>
    call<Envelope & { id: string; alreadyPublic?: boolean }>(`/models/${id}/publish`, { method: 'POST' }),

  remove: (id: string) =>
    call<Envelope & { id: string; tombstoned: boolean }>(`/models/${id}`, { method: 'DELETE' }),

  gallery: (limit = 60) => call<Envelope & { models: ModelNode[] }>(`/gallery?limit=${limit}`),

  mine: () => call<Envelope & { bucketId: string; models: ModelNode[] }>('/secrets/self'),

  /** Without confirm this is a preview and destroys nothing. */
  removeBucket: (confirm: boolean) =>
    call<Envelope & { bucketId: string; deleted: number; wouldDelete?: number; models?: ModelNode[] }>(
      `/secrets/self${confirm ? '?confirm=true' : ''}`,
      { method: 'DELETE' },
    ),
}
