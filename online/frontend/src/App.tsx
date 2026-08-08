import { PromptStrip } from './components/PromptStrip'
import { WamSource } from './components/WamSource'
import { WamTurntable } from './components/WamTurntable'
import { DEMO_WAM } from './wam/demo'

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

          <PromptStrip />
        </section>

        <section className="showcase" aria-label="A model and the source that made it">
          <WamSource text={DEMO_WAM} maxLines={22} />
          <WamTurntable
            source={DEMO_WAM}
            anim="idle"
            label="sentinel.wam — compiled in your browser"
          />
        </section>

        <section className="tryit" aria-label="Try your own model">
          <h2>Drop your own</h2>
          <p>
            Nothing is uploaded — the compiler runs here, in a worker, on your
            machine. The first model pays for the runtime; after that it is cached.
          </p>
          <WamTurntable />
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
