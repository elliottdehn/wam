/**
 * Conformance suite for API_DESIGN.md.
 *
 * Every assertion here corresponds to a rule in that document — first to
 * upload wins, parents must be public, delete blanks the strings, a private
 * child never appears on its public parent, and so on. If a rule changes,
 * this file is where the change has to show up.
 *
 * Needs a running server:
 *   npx wrangler dev --port 8787   # in one shell
 *   npm test                       # in another
 */
const B = 'http://localhost:8787/api'
let pass = 0, failed = 0
const ok = (label, cond, detail = '') => {
  if (cond) { pass++; console.log(`PASS  ${label}`) }
  else { failed++; console.log(`FAIL  ${label}${detail ? '\n        ' + detail : ''}`) }
}
const call = async (method, path, { secret, body } = {}) => {
  const r = await fetch(B + path, {
    method,
    headers: { 'content-type': 'application/json', ...(secret ? { 'x-wam-secret': secret } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await r.text()
  let j; try { j = JSON.parse(text) } catch { j = { raw: text } }
  return { status: r.status, j, text, headers: r.headers }
}
// Ids are content hashes, so fixtures must be unique per run or the second run
// trips first-write-wins on its own leftovers from the first.
const RUN = crypto.randomUUID().slice(0, 8)
const src = (n) => `# run ${RUN}
model demo${n}\n  height 2.0\n  style chunky\n\nskeleton\n  root pelvis at 0.5\n  bone spine parent=pelvis dir=up len=0.2\n\nparts\n  loft body bones=spine..spine material=hide\n    ring 0.00 w=0.2 d=0.2\n    ring 1.00 w=0.1 d=0.1\n    cap start=dome end=dome\n`

// ---- 1. first upload mints a secret ----------------------------------------
const a = await call('POST', '/models', { body: { source: src(1), title: 'First' } })
ok('first upload creates a node', a.status === 201 && a.j.existing === false, `${a.status} ${a.text.slice(0,200)}`)
ok('a secret is minted when none is sent', typeof a.j.secret === 'string' && a.j.secret.length > 30)
ok('private is the default', a.j.model.visibility === 'private')
ok('hint mentions saving the secret', /save it/i.test(a.j.hint))
ok('every response carries hint', typeof a.j.hint === 'string' && a.j.hint.length > 10)
const secret = a.j.secret, id1 = a.j.id

// ---- 2. content addressing + first-write-wins ------------------------------
const again = await call('POST', '/models', { body: { source: src(1), title: 'Different title' }, secret })
ok('identical source returns the same id', again.j.id === id1)
ok('...flagged as existing and owned', again.j.existing === true && again.j.owned === true)
ok('...the title is NOT overwritten', again.j.model.title === 'First', JSON.stringify(again.j.model.title))

const stranger = await call('POST', '/models', { body: { source: src(1) } })
ok('a stranger uploading the same source gets it unowned', stranger.j.existing === true && stranger.j.owned === false)
ok('...and is told they cannot delete it', /cannot delete or publish/i.test(stranger.j.hint))
ok('...and no new secret is minted for them', stranger.j.secret === undefined)

// ---- 3. parents must be public ---------------------------------------------
const child = await call('POST', '/models', { body: { source: src(2), parents: [id1] }, secret })
ok('naming a PRIVATE parent is rejected', child.status === 422 && child.j.reason === 'private', `${child.status}`)
ok('...with a non-retryable hint naming the fix', child.j.retryable === false && /Publish the parent first/.test(child.j.next.join(' ')))

const ghost = await call('POST', '/models', { body: { source: src(3), parents: ['deadbeef'] }, secret })
ok('naming a nonexistent parent is rejected', ghost.status === 422 && ghost.j.reason === 'missing')

// ---- 4. publish is one-way --------------------------------------------------
const pub = await call('POST', `/models/${id1}/publish`, { secret })
ok('publish succeeds for the owner', pub.status === 200 && /CC0/.test(pub.j.hint))
ok('...and says it cannot be undone', /cannot be undone/i.test(pub.j.hint))
const pubAgain = await call('POST', `/models/${id1}/publish`, { secret })
ok('re-publishing is idempotent, not an error', pubAgain.status === 200 && pubAgain.j.alreadyPublic === true)
const notMine = await call('POST', `/models/${id1}/publish`, { secret: 'someone-elses-secret' })
ok('publishing with an unknown secret is refused', notMine.status === 403)

// ---- 5. lineage + similarity ------------------------------------------------
const v2src = src(1).replace('ring 1.00 w=0.1 d=0.1', 'ring 1.00 w=0.14 d=0.14')
const v2 = await call('POST', '/models', { body: { source: v2src, title: 'v2', parents: [id1], visibility: 'public' }, secret })
ok('a public parent is accepted', v2.status === 201, `${v2.status} ${v2.text.slice(0,160)}`)
const id2 = v2.j.id
const lin = await call('GET', `/lineage/${id2}`)
ok('lineage returns the canonical track in order', lin.j.canonical.map(n => n.id).join(',') === `${id1},${id2}`, JSON.stringify(lin.j.canonical.map(n=>n.id)))
ok('...with the newest as the tip', lin.j.tips.join() === id2)
const succ = await call('GET', `/models/${id1}/successors`)
ok('successors reports the child with a measured similarity', succ.j.successors.length === 1 && succ.j.successors[0].similarity > 0.5 && succ.j.successors[0].similarity < 1,
   JSON.stringify(succ.j.successors[0]?.similarity))

// a different secret forking the same parent is a BRANCH, not canonical
const forkRes = await call('POST', '/models', { body: { source: src(1).replace('height 2.0','height 2.6'), parents: [id1], visibility: 'public' } })
const lin2 = await call('GET', `/lineage/${id2}`)
ok('another secret\'s derivative is a branch, not canonical',
   !lin2.j.canonical.some(n => n.id === forkRes.j.id) && lin2.j.branches.some(b => b.id === forkRes.j.id),
   JSON.stringify({canon: lin2.j.canonical.map(n=>n.id), branches: lin2.j.branches.map(b=>b.id)}))

// ---- 6. private descendants must not leak -----------------------------------
const hidden = await call('POST', '/models', { body: { source: src(9), parents: [id1], visibility: 'private' }, secret })
const succ2 = await call('GET', `/models/${id1}/successors`)
ok('a PRIVATE child is not listed on its public parent', !succ2.j.successors.some(s => s.id === hidden.j.id),
   JSON.stringify(succ2.j.successors.map(s=>s.id)))

// ---- 7. source + caching ----------------------------------------------------
const raw = await call('GET', `/models/${id1}/source`)
ok('source round-trips byte-identically', raw.text === src(1))
ok('...served immutable', /immutable/.test(raw.headers.get('cache-control') ?? ''))

// ---- 8. gallery lists tips only ---------------------------------------------
const gal = await call('GET', '/gallery')
ok('gallery lists the tip, not the superseded parent',
   gal.j.models.some(m => m.id === id2) && !gal.j.models.some(m => m.id === id1),
   JSON.stringify(gal.j.models.map(m=>m.id)))

// ---- 9. delete ---------------------------------------------------------------
const delWrong = await call('DELETE', `/models/${id2}`, { secret: 'nope' })
ok('delete with an unknown secret is refused', delWrong.status === 403)
const del = await call('DELETE', `/models/${id2}`, { secret })
ok('delete succeeds for the owner', del.status === 200 && del.j.tombstoned === true)
const after = await call('GET', `/models/${id2}`)
ok('...the link still resolves', after.status === 200 && after.j.model.tombstoned === true)
ok('...the title is blanked too', after.j.model.title === null, JSON.stringify(after.j.model.title))
const gone = await call('GET', `/models/${id2}/source`)
ok('...the source is gone (410)', gone.status === 410)
const lin3 = await call('GET', `/lineage/${id2}`)
ok('...and it keeps its place in the DAG', lin3.j.canonical.some(n => n.id === id2))
const revive = await call('POST', '/models', { body: { source: v2src }, secret })
ok('re-uploading deleted source does NOT restore it', revive.j.existing === true && (await call('GET', `/models/${id2}/source`)).status === 410)

// ---- 10. bucket delete previews first ----------------------------------------
const prev = await call('DELETE', '/secrets/self', { secret })
ok('bucket delete previews without confirm', prev.j.deleted === 0 && prev.j.wouldDelete > 0 && /Nothing has been deleted/.test(prev.j.hint),
   JSON.stringify({d: prev.j.deleted, w: prev.j.wouldDelete}))
const self = await call('GET', '/secrets/self', { secret })
ok('secrets/self lists what the secret owns', self.j.models.length >= 3)
const nuke = await call('DELETE', '/secrets/self?confirm=true', { secret })
ok('...and confirm=true tombstones the bucket', nuke.j.deleted === prev.j.wouldDelete, JSON.stringify({n: nuke.j.deleted, w: prev.j.wouldDelete}))
const survivor = await call('GET', `/models/${forkRes.j.id}/source`)
ok('another bucket is untouched by the nuke', survivor.status === 200)

// ---- 11. validation ----------------------------------------------------------
ok('empty source is rejected', (await call('POST', '/models', { body: { source: '  ' } })).status === 400)
ok('bad visibility is rejected', (await call('POST', '/models', { body: { source: src(50), visibility: 'secret' } })).status === 400)
ok('oversize source is rejected', (await call('POST', '/models', { body: { source: 'x'.repeat((1<<20) + 1) } })).status === 413)
const four04 = await call('GET', '/nope')
ok('unknown endpoint lists the real ones', four04.status === 404 && four04.j.next.length > 3)

console.log(`\n${pass} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
