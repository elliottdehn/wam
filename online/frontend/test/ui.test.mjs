/**
 * End-to-end UI suite: drives a real Chrome over CDP against `wrangler dev`.
 *
 * Covers the flows the API design implies but the API tests cannot see — that
 * a minted secret is shown once and stored, that publishing surfaces the
 * server's own CC0 wording, that scrubbing the version track changes the URL
 * *without* a page load, and that a deleted model keeps its place in the
 * lineage.
 *
 *   npx wrangler dev --port 8787                       # in online/backend
 *   chrome --headless --remote-debugging-port=9240 ...  # any Chromium
 *   node test/ui.test.mjs
 */
const t=(await fetch('http://127.0.0.1:9240/json').then(r=>r.json())).find(x=>x.type==='page')
const ws=new WebSocket(t.webSocketDebuggerUrl); let id=0; const p=new Map()
await new Promise(r=>ws.addEventListener('open',r))
ws.addEventListener('message',e=>{const m=JSON.parse(e.data); if(p.has(m.id)){p.get(m.id)(m.result);p.delete(m.id)}})
const cmd=(m,q={})=>new Promise(r=>{const i=++id;p.set(i,r);ws.send(JSON.stringify({id:i,method:m,params:q}))})
const ev=async e=>(await cmd('Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true}))?.result?.value
const wait=ms=>new Promise(r=>setTimeout(r,ms))
let pass=0,fail=0
const ok=(l,c,d='')=>{ c?pass++:fail++; console.log(`${c?'PASS':'FAIL'}  ${l}${d&&!c?'\n        '+d:''}`) }
const until=async(fn,ms=60000)=>{const t0=Date.now();for(;;){if(await fn())return true;if(Date.now()-t0>ms)return false;await wait(200)}}

await cmd('Page.enable'); await cmd('Runtime.enable')
const go=async(path)=>{ await cmd('Page.navigate',{url:'http://localhost:8787'+path+'?v='+Date.now()}); await wait(500) }

// ---- landing + drop-zone share ------------------------------------------
// Start from a clean slate: a leftover secret from a previous run puts this
// run's models in an older bucket and every count drifts.
await go('/')
await ev("localStorage.clear()")
await go('/')
ok('landing renders', !!await ev("document.querySelector('.prompt-strip')?1:0"))
ok('masthead links to the gallery', await ev("[...document.querySelectorAll('.badge')].map(a=>a.textContent).join(',')") === 'Gallery,Your models')

// compile a model via the drop zone by driving the file input programmatically
const SRC = `# ui test ${Date.now()}\nmodel uitest\n  height 2.0\n  style chunky\n\nskeleton\n  root pelvis at 0.5\n  bone spine parent=pelvis dir=up len=0.25\n\nparts\n  loft body bones=spine..spine material=hide\n    ring 0.00 w=0.22 d=0.22\n    ring 1.00 w=0.10 d=0.10\n    cap start=dome end=dome\n`
await ev(`window.__src = ${JSON.stringify(SRC)}`)
await ev(`(async()=>{const dt=new DataTransfer();
  dt.items.add(new File([window.__src],'uitest.wam',{type:'text/plain'}));
  const inp=[...document.querySelectorAll('input[type=file]')].pop();
  Object.defineProperty(inp,'files',{value:dt.files,configurable:true});
  inp.dispatchEvent(new Event('change',{bubbles:true}));})()`)
ok('drop zone compiles a dropped .wam', await until(async()=>await ev("document.querySelectorAll('canvas').length >= 2")), 'no second canvas')
ok('a share panel appears once it compiled', await until(async()=>!!await ev("document.querySelector('.share-panel')?1:0")))

// share it
await ev("document.querySelector('.share-panel .primary').click()")
ok('sharing returns a link', await until(async()=>!!await ev("document.querySelector('.share-panel.done')?1:0")))
ok('the minted secret is shown once, prominently', !!await ev("document.querySelector('.share-secret')?1:0"))
ok('...and is stored', (await ev("localStorage.getItem('wamshare.secret')"))?.length > 30)
const link = await ev("document.querySelector('.share-panel.done input').value")
ok('the link points at a model page', /\/m\/[0-9a-f]{32}$/.test(link ?? ''), link)
const mid = link.split('/m/')[1]

// ---- model page ----------------------------------------------------------
await go(`/m/${mid}`)
ok('model page loads and compiles the stored source', await until(async()=>await ev("document.querySelectorAll('canvas').length >= 1")))
ok('it shows as private', /private/.test(await ev("document.querySelector('.mp-badge').textContent") ?? ''))
ok('owner controls appear for your own model', await until(async()=>!!await ev("document.querySelector('.mp-owner')?1:0")))
ok('source viewer offers the download', !!await ev("document.querySelector('.wam-source-actions .primary')?1:0"))

// publish
await ev("[...document.querySelectorAll('.mp-actions button')].find(b=>/Publish/.test(b.textContent)).click()")
ok('publishing flips it to public', await until(async()=>/public/.test(await ev("document.querySelector('.mp-badge').textContent") ?? '')))
ok('...and the notice quotes the server hint about CC0', /CC0/.test(await ev("document.querySelector('.mp-notice')?.textContent") ?? ''))

// ---- v2 + lineage --------------------------------------------------------
const V2 = SRC.replace('ring 1.00 w=0.10 d=0.10','ring 1.00 w=0.16 d=0.16')
const up2 = await ev(`(async()=>{const r=await fetch('/api/models',{method:'POST',
  headers:{'content-type':'application/json','x-wam-secret':localStorage.getItem('wamshare.secret')},
  body:JSON.stringify({source:${JSON.stringify(V2)},title:'v2',parents:['${mid}'],visibility:'public'})});
  return (await r.json()).id})()`)
await go(`/m/${up2}`)
ok('lineage shows both versions on one page', await until(async()=>await ev("document.querySelectorAll('.mp-versions button').length") === 2))
ok('...the current one is marked', await ev("document.querySelector('.mp-versions button.on span.v-title')?.textContent") === 'v2')
ok('...and the newest is flagged as latest', !!await ev("document.querySelector('.v-tip')?1:0"))

// scrub back a version without a page load
const before = await ev("performance.getEntriesByType('navigation').length")
await ev("[...document.querySelectorAll('.mp-versions button')][0].click()")
ok('scrubbing changes the URL', await until(async()=>(await ev("location.pathname")) === `/m/${mid}`), await ev("location.pathname"))
ok('...without a page load', (await ev("performance.getEntriesByType('navigation').length")) === before)

// ---- gallery -------------------------------------------------------------
await go('/gallery')
ok('gallery lists the tip only', await until(async()=>{
  const ids = await ev("[...document.querySelectorAll('.g-list a')].map(a=>a.getAttribute('href')).join(',')")
  return ids?.includes(up2) && !ids.includes(mid)
}), await ev("[...document.querySelectorAll('.g-list a')].map(a=>a.getAttribute('href')).join(',')"))

// ---- your models ---------------------------------------------------------
await go('/mine')
ok('your-models lists exactly this run\'s two', await until(async()=>await ev("document.querySelectorAll('.g-list li').length") === 2),
   await ev("document.querySelectorAll('.g-list li').length"))
ok('the secret is masked until revealed', /^•+$/.test(await ev("document.querySelector('.g-secret-row code').textContent") ?? ''))
await ev("[...document.querySelectorAll('.g-secret button')].find(b=>b.textContent==='Reveal').click()")
ok('...and can be revealed', (await ev("document.querySelector('.g-secret-row code').textContent"))?.length > 30)
await ev("document.querySelector('.g-danger button').click()")
ok('bucket delete previews first', await until(async()=>/would tombstone 2 models/.test(await ev("document.querySelector('.g-danger .g-muted')?.textContent") ?? '')),
   await ev("document.querySelector('.g-danger')?.textContent"))

// ---- delete one ----------------------------------------------------------
await go(`/m/${mid}`)
await until(async()=>!!await ev("document.querySelector('.mp-owner')?1:0"))
await ev("[...document.querySelectorAll('.mp-actions button')].find(b=>b.textContent==='Delete').click()")
await ev("[...document.querySelectorAll('.mp-actions button')].find(b=>/Yes, delete/.test(b.textContent)).click()")
ok('deleting shows the tombstone state', await until(async()=>!!await ev("document.querySelector('.mp-tombstone')?1:0")))
await go(`/m/${up2}`)
ok('...and the deleted version keeps its place in the track',
   await until(async()=>await ev("document.querySelectorAll('.mp-versions button').length") === 2))

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail?1:0)
