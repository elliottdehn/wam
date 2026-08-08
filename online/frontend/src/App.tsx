const SAMPLE = `model king_black_dragon
  height 4.6
  style chunky

palette
  hide  #232b2c rough=0.72
  ember #e2531b rough=0.30

skeleton
  bone neck1 parent=chest  curl=26 len=0.28
  bone head  parent=neck3  curl=-8 len=0.27

parts
  loft skull bones=head..head continues=neck
    ring 0.20 w=0.26 dtop=0.20 dbot=0.15
    ring 1.00 w=0.085 dtop=0.085 dbot=0.06

checks
  assert dist(sskull.l, sskull.r) > 1.10
  assert tris < 9000`

const FEATURES = [
  {
    title: 'A link that spins',
    body: 'Every model gets a page with a real turntable — orbit it, wireframe it, scrub the animations. No plugin, no download, no account for whoever you send it to.',
  },
  {
    title: 'glTF on the way out',
    body: 'Skinned mesh, skeleton and animation tracks, exported clean. Drop it into Blender, Godot, Unity or three.js and it just opens.',
  },
  {
    title: 'The source, not just the render',
    body: 'The .wam that produced it comes down with it. Change six numbers, recompile, and you have your own. That is the point.',
  },
]

export default function App() {
  return (
    <div className="page">
      <div className="glow" aria-hidden="true" />

      <header className="masthead">
        <span className="wordmark">
          wam<span className="wordmark-dim">share</span>
        </span>
        <span className="badge">Nothing to browse yet</span>
      </header>

      <main>
        <section className="hero">
          <p className="eyebrow">WAM — a text language for low-poly characters</p>
          <h1>
            Where&rsquo;s the Art,
            <br />
            <span className="accent">Man?</span>
          </h1>
          <p className="lede">
            You write discrete, named, relative decisions — bone angles, ring widths,
            palette colours. The compiler makes every vertex, weight, normal and
            winding. Then you put it here and send someone the link.
          </p>
        </section>

        <section className="sample" aria-label="Example WAM source">
          <div className="sample-chrome">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
            <span className="sample-name">king_black_dragon.wam</span>
          </div>
          <pre>
            <code>{SAMPLE}</code>
          </pre>
          <p className="sample-caption">
            2,218 triangles. Three heads, and a check that keeps them apart.
          </p>
        </section>

        <section className="features">
          {FEATURES.map((f) => (
            <article key={f.title}>
              <h2>{f.title}</h2>
              <p>{f.body}</p>
            </article>
          ))}
        </section>
      </main>

      <footer>
        <p>
          Built for models compiled with the WAM toolchain. Uploading is coming — for
          now the compiler still drops a self-contained viewer next to your{' '}
          <code>.gltf</code>.
        </p>
      </footer>
    </div>
  )
}
