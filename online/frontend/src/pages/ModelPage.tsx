/**
 * A link lands you in a lineage, not on a single model.
 *
 * The page shows the model at the position you arrived at, the canonical track
 * it belongs to (versions sharing its secret), and derivatives owned by anyone
 * else as branches. Moving along the track happens here — no page load — but
 * it still pushes the URL, because `/m/:id` has to keep meaning exactly that
 * model or every link ever shared quietly starts meaning something else.
 */
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Lineage, type ModelNode } from '../api'
import { WamSource } from '../components/WamSource'
import { WamTurntable } from '../components/WamTurntable'
import { Link } from '../Link'
import { getSecret } from '../secret'
import './ModelPage.css'

interface Props {
  id: string
  navigate: (to: string, opts?: { replace?: boolean }) => void
}

export function ModelPage({ id, navigate }: Props) {
  const [model, setModel] = useState<ModelNode | null>(null)
  const [source, setSource] = useState<string | null>(null)
  const [lineage, setLineage] = useState<Lineage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const load = useCallback(async (modelId: string) => {
    setError(null)
    setSource(null)
    setModel(null)
    setConfirmDelete(false)
    try {
      const [m, l] = await Promise.all([api.model(modelId), api.lineage(modelId)])
      setModel(m.model)
      setLineage(l)
      if (!m.model.tombstoned) setSource(await api.source(modelId))
    } catch (err) {
      setError(err instanceof ApiError ? err.hint : String(err))
    }
  }, [])

  useEffect(() => {
    void load(id)
  }, [id, load])

  // Ownership is a local check: the model carries its bucketId and we know
  // ours from anything we have uploaded. Acting on it is still server-checked.
  const [myBucket, setMyBucket] = useState<string | null>(null)
  useEffect(() => {
    if (!getSecret()) return
    api
      .mine()
      .then((r) => setMyBucket(r.bucketId))
      .catch(() => setMyBucket(null))
  }, [])
  const mine = !!model && !!myBucket && model.bucketId === myBucket

  const act = async (label: string, fn: () => Promise<{ hint?: string }>) => {
    setBusy(label)
    setNotice(null)
    try {
      const r = await fn()
      setNotice(r.hint ?? null)
      await load(id)
    } catch (err) {
      setNotice(err instanceof ApiError ? err.hint : String(err))
    } finally {
      setBusy(null)
    }
  }

  if (error) {
    return (
      <div className="model-page">
        <p className="mp-error">{error}</p>
        <Link to="/">← wamshare</Link>
      </div>
    )
  }
  if (!model) return <div className="model-page"><p className="mp-muted">Loading…</p></div>

  const track = lineage?.canonical ?? []
  const position = track.findIndex((n) => n.id === model.id)

  return (
    <div className="model-page">
      <header className="mp-head">
        <Link to="/" className="mp-back">← wamshare</Link>
        <span className={`mp-badge mp-${model.visibility}`}>
          {model.visibility === 'public' ? 'public · CC0 1.0' : 'private · all rights reserved'}
        </span>
      </header>

      <h1>{model.title ?? (model.tombstoned ? 'Deleted model' : 'Untitled')}</h1>
      {model.description && <p className="mp-desc">{model.description}</p>}

      {model.tombstoned ? (
        <p className="mp-tombstone">
          This model was deleted. The link still resolves and its place in the lineage
          is intact, but the source is gone.
        </p>
      ) : (
        source && (
          <div className="mp-grid">
            <WamTurntable source={source} label={model.title ?? model.id} />
            <WamSource text={source} filename={`${model.title ?? model.id}.wam`} maxLines={26} />
          </div>
        )
      )}

      {/* ---- version track ---------------------------------------------- */}
      {track.length > 1 && (
        <section className="mp-track">
          <h2>Versions</h2>
          <p className="mp-muted">
            {track.length} versions share this model&rsquo;s secret. That is what makes
            them one lineage rather than someone else&rsquo;s fork.
          </p>
          <ol className="mp-versions">
            {track.map((n, i) => (
              <li key={n.id}>
                <button
                  type="button"
                  className={n.id === model.id ? 'on' : undefined}
                  aria-current={n.id === model.id ? 'true' : undefined}
                  onClick={() => navigate(`/m/${n.id}`)}
                >
                  <span className="v-num">v{i + 1}</span>
                  <span className="v-title">{n.title ?? (n.tombstoned ? 'deleted' : 'untitled')}</span>
                  {lineage?.tips.includes(n.id) && <span className="v-tip">latest</span>}
                </button>
              </li>
            ))}
          </ol>
          {position >= 0 && position < track.length - 1 && (
            <p className="mp-muted">
              You are looking at v{position + 1}. There is a newer version on this track.
            </p>
          )}
        </section>
      )}

      {/* ---- branches ---------------------------------------------------- */}
      {!!lineage?.branches.length && (
        <section className="mp-branches">
          <h2>Derived by others</h2>
          <p className="mp-muted">
            Public models that declare descent from this track. The claim is not
            verified; the percentage is how much source they actually share.
          </p>
          <ul>
            {lineage.branches.map((b) => (
              <li key={b.id}>
                <Link to={`/m/${b.id}`}>{b.title ?? b.id.slice(0, 12)}</Link>
                {b.similarity !== null && (
                  <span className="mp-sim">{Math.round(b.similarity * 100)}% shared</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ---- owner controls ---------------------------------------------- */}
      {mine && !model.tombstoned && (
        <section className="mp-owner">
          <h2>Yours</h2>
          <div className="mp-actions">
            {model.visibility === 'private' && (
              <button
                type="button"
                className="primary"
                disabled={!!busy}
                onClick={() => act('publish', () => api.publish(model.id))}
              >
                {busy === 'publish' ? 'Publishing…' : 'Publish under CC0'}
              </button>
            )}
            {confirmDelete ? (
              <>
                <button
                  type="button"
                  className="danger"
                  disabled={!!busy}
                  onClick={() => act('delete', () => api.remove(model.id))}
                >
                  {busy === 'delete' ? 'Deleting…' : 'Yes, delete it'}
                </button>
                <button type="button" onClick={() => setConfirmDelete(false)}>Cancel</button>
              </>
            ) : (
              <button type="button" onClick={() => setConfirmDelete(true)}>Delete</button>
            )}
          </div>
          <p className="mp-muted">
            {model.visibility === 'private'
              ? 'Publishing dedicates this to the public domain and cannot be undone — the licence holds for copies already made, even if you delete it later.'
              : 'This is public under CC0. Deleting removes it from here; it does not un-license copies already made.'}
          </p>
        </section>
      )}

      {notice && <p className="mp-notice">{notice}</p>}
    </div>
  )
}

export default ModelPage
