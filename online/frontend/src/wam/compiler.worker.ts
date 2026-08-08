/**
 * Runs the real WAM compiler under Pyodide, off the main thread.
 *
 * This is viable only because the compile path is pure Python + numpy — no
 * PIL, no scipy; even PNG encoding is hand-rolled with zlib and struct. So the
 * geometry a browser produces is the geometry the CLI produces, rather than a
 * reimplementation that drifts.
 *
 * Everything is served from our own origin under /pyodide/ so the runtime is
 * not hostage to a CDN and we control its cache headers.
 */
/// <reference lib="webworker" />

const BASE = '/pyodide/'

// The glue is deliberately thin: parse, solve, build, export. `export_built`
// writes JSON to the virtual filesystem and we hand the text back rather than
// converting a large dict across the JS/Python boundary, which is far slower.
const GLUE = `
import json, sys, traceback

def _compile(text):
    with open('/tmp/model.wam', 'w') as f:
        f.write(text)
    from wam import parser as wparser, skeleton as wskel, mesh as wmesh
    from wam import viewer_export as wve
    model = wparser.parse_file('/tmp/model.wam')
    bones, order = wskel.solve(model)
    built = wmesh.build(model, bones)
    wve.export_built(model, bones, order, built, '/tmp/viewer.json')
    with open('/tmp/viewer.json') as f:
        return f.read()
`

type PyodideApi = {
  loadPackage: (names: string | string[]) => Promise<unknown>
  unpackArchive: (buf: ArrayBuffer, format: string, opts?: object) => void
  runPython: (code: string) => unknown
  globals: { get: (name: string) => (arg: string) => string }
  FS: { mkdirTree: (p: string) => void }
}

let ready: Promise<PyodideApi> | null = null

async function boot(): Promise<PyodideApi> {
  post({ type: 'status', stage: 'runtime' })
  // A runtime URL, not a bundled module — Vite must not try to resolve it.
  const mod = await import(/* @vite-ignore */ `${BASE}pyodide.mjs`)
  const py: PyodideApi = await mod.loadPyodide({ indexURL: BASE })

  post({ type: 'status', stage: 'numpy' })
  await py.loadPackage('numpy')

  post({ type: 'status', stage: 'compiler' })
  const zip = await fetch(`${BASE}wam.zip`)
  if (!zip.ok) throw new Error(`could not fetch the compiler (${zip.status})`)
  // Unpacks to the cwd, so the archive's top-level `wam/` becomes importable.
  py.unpackArchive(await zip.arrayBuffer(), 'zip')
  py.FS.mkdirTree('/tmp')
  py.runPython(GLUE)

  post({ type: 'status', stage: 'ready' })
  return py
}

interface Request {
  id: number
  text: string
}

type Outbound =
  | { type: 'status'; stage: string }
  | { type: 'ok'; id: number; json: string; ms: number }
  | { type: 'error'; id: number; message: string }

function post(msg: Outbound | { type: 'status'; stage: string }) {
  ;(self as unknown as DedicatedWorkerGlobalScope).postMessage(msg)
}

self.onmessage = async (ev: MessageEvent<Request>) => {
  const { id, text } = ev.data
  try {
    ready ??= boot()
    const py = await ready
    const started = performance.now()
    const json = py.globals.get('_compile')(text)
    post({ type: 'ok', id, json, ms: Math.round(performance.now() - started) })
  } catch (err) {
    // A WamError carries the line and the fix; surfacing the raw message is
    // more useful than any wrapper we could put around it.
    const message = err instanceof Error ? err.message : String(err)
    post({ type: 'error', id, message: message.trim().split('\n').slice(-12).join('\n') })
    // Boot failures must not poison every later compile.
    if (!/^\s*$/.test(message) && ready && message.includes('pyodide')) ready = null
  }
}
