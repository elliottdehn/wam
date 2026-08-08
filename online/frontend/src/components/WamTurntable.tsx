/**
 * A turntable for a WAM model. Three ways to feed it:
 *
 *   <WamTurntable source={wamText} />   compiles, then spins
 *   <WamTurntable model={viewerJson} /> already compiled — no Pyodide at all
 *   <WamTurntable />                    drop or pick a .wam
 *
 * Just the turntable: it spins, and that is the whole interaction.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { compileWam, type CompileStage } from '../wam/compile'
import { Turntable, type TurntableOptions } from '../wam/turntable'
import type { WamModel } from '../wam/types'
import './WamTurntable.css'

const STAGE_TEXT: Record<CompileStage, string> = {
  cached: 'Loading',
  runtime: 'Fetching the Python runtime',
  numpy: 'Loading numpy',
  compiler: 'Loading the WAM compiler',
  ready: 'Compiling',
  compiling: 'Compiling',
}

export interface WamTurntableProps extends TurntableOptions {
  /** WAM source text. Compiled in the browser. */
  source?: string
  /** An already-compiled viewer blob; skips the compiler entirely. */
  model?: WamModel
  className?: string
  /** Shown under the canvas. Defaults to the model's own name. */
  label?: string
}

export function WamTurntable({
  source,
  model,
  className,
  label,
  ...view
}: WamTurntableProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const engineRef = useRef<Turntable | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const [loaded, setLoaded] = useState<WamModel | null>(model ?? null)
  const [stage, setStage] = useState<CompileStage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  const busy = stage !== null && !loaded

  // ---- compile whatever we were handed -------------------------------------
  const ingest = useCallback((text: string, signal?: AbortSignal) => {
    setError(null)
    setStage('compiling')
    compileWam(text, { onStage: setStage, signal })
      .then((m) => {
        if (signal?.aborted) return
        setLoaded(m)
        setStage(null)
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setLoaded(null)
        setStage(null)
        setError(err instanceof Error ? err.message : String(err))
      })
  }, [])

  useEffect(() => {
    if (model) {
      setLoaded(model)
      return
    }
    if (!source) return
    const ac = new AbortController()
    ingest(source, ac.signal)
    return () => ac.abort()
  }, [source, model, ingest])

  // ---- renderer lifecycle ---------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !loaded) return
    let engine: Turntable
    try {
      engine = new Turntable(canvas, view)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return
    }
    engineRef.current = engine
    engine.load(loaded)
    // Reduced motion stops the *automatic* spin; it should not take away the
    // ability to turn the model by hand.
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    engine.start(!still)
    return () => {
      engine.dispose()
      engineRef.current = null
    }
    // `view` is spread from props; re-creating the context on every parent
    // render would be worse than missing a mid-flight option change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded])

  // ---- file input -----------------------------------------------------------
  const takeFile = useCallback(
    (file: File | undefined) => {
      if (!file) return
      if (!/\.wam$/i.test(file.name)) {
        setError(`${file.name} is not a .wam file`)
        return
      }
      file.text().then((t) => ingest(t))
    },
    [ingest],
  )

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      takeFile(e.dataTransfer.files?.[0])
    },
    [takeFile],
  )

  const empty = !loaded && !busy
  const name = label ?? loaded?.name

  return (
    <figure
      className={['wam-turntable', dragging && 'is-dragging', className]
        .filter(Boolean)
        .join(' ')}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="wam-stage">
        {loaded && <canvas ref={canvasRef} />}

        {busy && (
          <div className="wam-overlay" role="status">
            <span className="wam-spinner" aria-hidden="true" />
            <p>{stage ? STAGE_TEXT[stage] : 'Working'}…</p>
            {stage === 'runtime' && (
              <small>First time only — it is cached after this.</small>
            )}
          </div>
        )}

        {empty && (
          <div className="wam-overlay wam-empty">
            <p className="wam-empty-title">Drop a <code>.wam</code> here</p>
            <button type="button" onClick={() => inputRef.current?.click()}>
              Choose a file
            </button>
            <input
              ref={inputRef}
              type="file"
              accept=".wam,text/plain"
              hidden
              onChange={(e) => {
                takeFile(e.target.files?.[0] ?? undefined)
                e.target.value = ''
              }}
            />
          </div>
        )}

        {error && (
          <div className="wam-overlay wam-error" role="alert">
            <p className="wam-error-title">That didn’t compile</p>
            <pre>{error}</pre>
            <button type="button" onClick={() => setError(null)}>
              Try another
            </button>
          </div>
        )}
      </div>

      {(name || loaded) && (
        <figcaption>
          {name}
          {loaded && (
            <span className="wam-hint">drag to orbit · scroll to zoom · double-click to reset</span>
          )}
        </figcaption>
      )}
    </figure>
  )
}

export default WamTurntable
