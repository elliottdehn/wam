/**
 * Everything behind the secret in this browser.
 *
 * There is no other way to enumerate it and no way to recover the secret, so
 * this page is also where the secret itself is shown and can be replaced —
 * pasting one in is how you carry a lineage to another machine.
 */
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type ModelNode } from '../api'
import { Link } from '../Link'
import { forgetSecret, getSecret, setSecret } from '../secret'
import './Gallery.css'

export function Mine() {
  const [models, setModels] = useState<ModelNode[] | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [preview, setPreview] = useState<number | null>(null)
  const [reveal, setReveal] = useState(false)
  const [paste, setPaste] = useState('')
  const secret = getSecret()

  const load = useCallback(() => {
    if (!getSecret()) return setModels([])
    api
      .mine()
      .then((r) => setModels(r.models))
      .catch((e) => {
        setModels([])
        setNotice(e instanceof ApiError ? e.hint : String(e))
      })
  }, [])

  useEffect(load, [load])

  const nuke = async (confirm: boolean) => {
    try {
      const r = await api.removeBucket(confirm)
      setNotice(r.hint ?? null)
      if (confirm) {
        setPreview(null)
        load()
      } else {
        setPreview(r.wouldDelete ?? 0)
      }
    } catch (e) {
      setNotice(e instanceof ApiError ? e.hint : String(e))
    }
  }

  return (
    <div className="gallery">
      <header className="g-head">
        <Link to="/" className="g-back">← wamshare</Link>
      </header>
      <h1>Your models</h1>

      <section className="g-secret">
        <h2>Your secret</h2>
        {secret ? (
          <>
            <p className="g-muted">
              This is the only thing that can delete these models or add a next version
              to the same lineage. It cannot be recovered. Copy it somewhere.
            </p>
            <div className="g-secret-row">
              <code>{reveal ? secret : '•'.repeat(24)}</code>
              <button type="button" onClick={() => setReveal((v) => !v)}>
                {reveal ? 'Hide' : 'Reveal'}
              </button>
              <button type="button" onClick={() => navigator.clipboard?.writeText(secret)}>
                Copy
              </button>
            </div>
          </>
        ) : (
          <p className="g-muted">
            No secret in this browser yet. One is created the first time you share a
            model, or paste an existing one below to pick up a lineage from another
            machine.
          </p>
        )}
        <div className="g-secret-row">
          <input
            value={paste}
            placeholder="paste a secret"
            onChange={(e) => setPaste(e.target.value)}
          />
          <button
            type="button"
            disabled={!paste.trim()}
            onClick={() => {
              setSecret(paste.trim())
              setPaste('')
              load()
            }}
          >
            Use it
          </button>
          {secret && (
            <button
              type="button"
              onClick={() => {
                forgetSecret()
                setModels([])
              }}
            >
              Forget on this device
            </button>
          )}
        </div>
        <p className="g-muted g-small">
          Forgetting only clears this browser. It deletes nothing, and without a copy
          the models behind it can never be deleted or extended.
        </p>
      </section>

      {models && !!models.length && (
        <ul className="g-list">
          {models.map((m) => (
            <li key={m.id}>
              <Link to={`/m/${m.id}`}>
                <span className="g-title">
                  {m.title ?? (m.tombstoned ? 'Deleted' : 'Untitled')}
                </span>
                <span className="g-meta">
                  {m.tombstoned ? 'deleted' : m.visibility === 'public' ? 'public · CC0' : 'private'}
                  {' · '}
                  {new Date(m.createdAt).toLocaleDateString()}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {models && !models.length && <p className="g-muted">Nothing here yet.</p>}

      {!!models?.filter((m) => !m.tombstoned).length && (
        <section className="g-danger">
          <h2>Delete everything</h2>
          {preview === null ? (
            <button type="button" onClick={() => nuke(false)}>
              Show me what this would delete
            </button>
          ) : (
            <>
              <p className="g-muted">
                This would tombstone {preview} model{preview === 1 ? '' : 's'} and cannot
                be undone. Nothing has been deleted yet.
              </p>
              <button type="button" className="danger" onClick={() => nuke(true)}>
                Delete all {preview}
              </button>
              <button type="button" onClick={() => setPreview(null)}>Cancel</button>
            </>
          )}
        </section>
      )}

      {notice && <p className="g-notice">{notice}</p>}
    </div>
  )
}
export default Mine
