/**
 * The release gate for the compiler.
 *
 * Nothing is pinned and nothing is cached: every stored model compiles with
 * whatever compiler is deployed. That is only safe if a language change cannot
 * quietly kill old links, so the stored corpus is the compatibility suite —
 * every uploaded source is a test case, it grows for free, and it covers
 * exactly what people actually write rather than what we thought to test.
 *
 *   node scripts/corpus-check.mjs <dir-of-.wam-files>
 *
 * Exits non-zero if anything that used to compile no longer does. Run it
 * against a corpus dump before shipping a compiler change.
 *
 * Getting the dump: sources live in R2 under `src/<id>`. Until there is an
 * export path, `wrangler r2 object get wamshare-sources/src/<id>` one at a
 * time, or point this at any directory of .wam files — the repo's own
 * `models/` and `online/frontend/samples/` both work.
 */
import { execFile } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'

const run = promisify(execFile)
const dir = process.argv[2]
if (!dir) {
  console.error('usage: node scripts/corpus-check.mjs <dir-of-.wam-files>')
  process.exit(2)
}

// The compiler lives at the repo root, three levels up from backend/scripts.
const repo = path.resolve(import.meta.dirname, '..', '..', '..')

const files = (await fs.readdir(dir, { recursive: true }))
  .filter((f) => f.endsWith('.wam'))
  .map((f) => path.join(dir, f))

if (!files.length) {
  console.error(`no .wam files under ${dir}`)
  process.exit(2)
}

let broken = 0
for (const file of files) {
  try {
    await run('python3', ['-m', 'wam.cli', path.resolve(file), '--no-gltf', '--no-viewer', '-o', '/tmp/corpus-check/x'], {
      cwd: repo,
      timeout: 120_000,
    })
    console.log(`ok    ${path.basename(file)}`)
  } catch (err) {
    broken++
    const msg = String(err.stdout || err.stderr || err.message)
      .split('\n')
      .filter((l) => /ERROR|Error|Traceback/.test(l))
      .slice(0, 3)
      .join('\n        ')
    console.log(`BROKE ${path.basename(file)}\n        ${msg}`)
  }
}

console.log(`\n${files.length - broken}/${files.length} compile with the current compiler`)
if (broken) {
  console.log(
    `\n${broken} model(s) would break if this compiler shipped. Every one of them\n` +
      `is a permanent link that stops rendering. Fix the compiler, or decide\n` +
      `deliberately that the break is worth it.`,
  )
}
process.exit(broken ? 1 : 0)
