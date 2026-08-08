/**
 * Compile WAM text to a viewer blob, with the three caches that make the
 * second view instant.
 *
 * 1. IndexedDB, keyed by a hash of the source. A model you have already seen
 *    never compiles again — this is the layer that actually removes the wait,
 *    because it skips Pyodide entirely.
 * 2. The browser's HTTP cache for the ~18 MB runtime under /pyodide/, which is
 *    immutable and served with a one-year max-age.
 * 3. One worker per page, booted once and reused for every later compile.
 */
import type { WamModel } from './types'

export type CompileStage = 'cached' | 'runtime' | 'numpy' | 'compiler' | 'ready' | 'compiling'

const DB_NAME = 'wamshare'
const STORE = 'compiled'
const DB_VERSION = 1

async function openDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return null
  return new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE)
      }
    }
    req.onsuccess = () => resolve(req.result)
    // A private-mode browser can refuse to open a database. That is a reason to
    // compile every time, not to fail.
    req.onerror = () => resolve(null)
  })
}

async function hashKey(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text)
  if (!crypto?.subtle) return `len:${bytes.length}`
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest).slice(0, 16))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

async function readCache(key: string): Promise<WamModel | null> {
  const db = await openDb()
  if (!db) return null
  return new Promise((resolve) => {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(key)
    req.onsuccess = () => resolve((req.result as WamModel) ?? null)
    req.onerror = () => resolve(null)
  })
}

async function writeCache(key: string, model: WamModel) {
  const db = await openDb()
  if (!db) return
  try {
    db.transaction(STORE, 'readwrite').objectStore(STORE).put(model, key)
  } catch {
    // Quota, mostly. Losing the cache costs time, never correctness.
  }
}

let worker: Worker | null = null
let nextId = 1
const pending = new Map<
  number,
  { resolve: (json: string) => void; reject: (err: Error) => void }
>()
let onStage: ((stage: CompileStage) => void) | null = null

function ensureWorker(): Worker {
  if (worker) return worker
  worker = new Worker(new URL('./compiler.worker.ts', import.meta.url), {
    type: 'module',
  })
  worker.onmessage = (ev) => {
    const msg = ev.data
    if (msg.type === 'status') {
      onStage?.(msg.stage as CompileStage)
      return
    }
    const entry = pending.get(msg.id)
    if (!entry) return
    pending.delete(msg.id)
    if (msg.type === 'ok') entry.resolve(msg.json)
    else entry.reject(new Error(msg.message))
  }
  worker.onerror = (ev) => {
    const err = new Error(ev.message || 'the compiler worker failed to start')
    pending.forEach((p) => p.reject(err))
    pending.clear()
    worker?.terminate()
    worker = null
  }
  return worker
}

export interface CompileOptions {
  onStage?: (stage: CompileStage) => void
  signal?: AbortSignal
}

export async function compileWam(
  text: string,
  opts: CompileOptions = {},
): Promise<WamModel> {
  const key = await hashKey(text)
  const hit = await readCache(key)
  if (hit) {
    opts.onStage?.('cached')
    return hit
  }
  if (opts.signal?.aborted) throw new DOMException('aborted', 'AbortError')

  onStage = opts.onStage ?? null
  const w = ensureWorker()
  const id = nextId++
  const json = await new Promise<string>((resolve, reject) => {
    pending.set(id, { resolve, reject })
    opts.signal?.addEventListener('abort', () => {
      if (pending.delete(id)) reject(new DOMException('aborted', 'AbortError'))
    })
    opts.onStage?.('compiling')
    w.postMessage({ id, text })
  })

  const model = JSON.parse(json) as WamModel
  void writeCache(key, model)
  return model
}

/** Warm the runtime before anyone drops a file on the page. */
export function preloadCompiler() {
  ensureWorker()
}
