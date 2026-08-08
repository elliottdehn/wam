/**
 * Stage the Pyodide runtime, the numpy wheel, and the WAM compiler itself into
 * public/pyodide, so everything the turntable needs is served from our own
 * origin under cache headers we control.
 *
 * Self-hosted rather than CDN-loaded for two reasons: the compiler is only
 * usable if numpy resolves, and a third-party outage should not take the
 * viewer down; and immutable caching is the whole answer to "don't make me
 * wait 15 seconds twice".
 *
 * Run via `npm run prepare:pyodide` (wired into prebuild).
 */
import { createRequire } from 'node:module'
import { createWriteStream } from 'node:fs'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const run = promisify(execFile)
const require = createRequire(import.meta.url)
const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')
const dest = path.join(root, 'public', 'pyodide')
const pyodideDir = path.dirname(require.resolve('pyodide/package.json'))
const lock = require('pyodide/pyodide-lock.json')
const version = require('pyodide/package.json').version

// The loader pulls these by name at runtime; everything else in the package is
// types, source maps and the REPL demo pages.
const RUNTIME_FILES = [
  'pyodide.mjs',
  'pyodide.asm.mjs',
  'pyodide.asm.wasm',
  'python_stdlib.zip',
  'pyodide-lock.json',
]

const PACKAGES = ['numpy']

async function copyRuntime() {
  await fs.mkdir(dest, { recursive: true })
  for (const f of RUNTIME_FILES) {
    await fs.copyFile(path.join(pyodideDir, f), path.join(dest, f))
  }
  return RUNTIME_FILES.length
}

async function fetchWheels() {
  const wanted = new Set()
  const walk = (name) => {
    const pkg = lock.packages[name]
    if (!pkg || wanted.has(pkg.file_name)) return
    wanted.add(pkg.file_name)
    for (const dep of pkg.depends ?? []) walk(dep)
  }
  PACKAGES.forEach(walk)

  for (const file of wanted) {
    const out = path.join(dest, file)
    // Wheels are content-addressed by version in their filename, so an
    // existing one is never stale.
    if (await fs.stat(out).then(() => true, () => false)) continue
    const local = path.join(pyodideDir, file)
    if (await fs.stat(local).then(() => true, () => false)) {
      await fs.copyFile(local, out)
      continue
    }
    const url = `https://cdn.jsdelivr.net/pyodide/v${version}/full/${file}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`${res.status} fetching ${url}`)
    await pipeline(Readable.fromWeb(res.body), createWriteStream(out))
  }
  return wanted
}

/**
 * The compiler ships as one zip rather than ~15 loose modules: Pyodide can
 * unpack an archive into the virtual filesystem in a single call, and it is
 * one request instead of fifteen.
 */
async function packCompiler() {
  const pkg = path.resolve(root, '..', '..', 'wam')
  if (!(await fs.stat(pkg).then(() => true, () => false))) {
    throw new Error(`no wam package at ${pkg}`)
  }
  const zip = path.join(dest, 'wam.zip')
  await fs.rm(zip, { force: true })
  // -x excludes caches; zip writes paths relative to -C's directory, so the
  // archive root contains `wam/`, which is importable as-is.
  await run('zip', ['-q', '-r', zip, 'wam', '-i', 'wam/*.py'], {
    cwd: path.dirname(pkg),
  })
  const { size } = await fs.stat(zip)
  return size
}

const files = await copyRuntime()
const wheels = await fetchWheels()
const zipped = await packCompiler()
console.log(
  `pyodide ${version}: ${files} runtime files, ${wheels.size} wheel(s), ` +
    `wam.zip ${(zipped / 1024).toFixed(0)} KB -> public/pyodide`,
)
