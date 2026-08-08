/**
 * Turn a compiled model into a link.
 *
 * Private is the default and the button says so, because publishing dedicates
 * the model to the public domain and cannot be undone. The secret is shown
 * once, prominently, and has to be dismissed deliberately — it is the only
 * thing that can delete the upload or add a next version to the same lineage,
 * and there is no recovery path.
 */
import { useState } from 'react'
import { api, ApiError, type UploadResult } from '../api'
import { Link } from '../Link'
import { acknowledgeSecret } from '../secret'
import type { WamModel } from '../wam/types'
import './SharePanel.css'

export interface SharePanelProps {
  source: string
  /** Compiled locally; sent as an unverified claim, which is all it can be. */
  model?: WamModel | null
  /** Declares descent. Must be a public model or the server refuses it. */
  parents?: string[]
}

export function SharePanel({ source, model, parents }: SharePanelProps) {
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [savedSecret, setSavedSecret] = useState<string | null>(null)

  const share = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.upload({
        source,
        title: title.trim() || model?.name || undefined,
        visibility: 'private',
        parents,
        // Claims, not facts: there is no compiler on the server to check them.
        meta: model
          ? {
              tris: model.tris.length / 3,
              verts: model.verts.length / 3,
              bones: model.bones.length,
              anims: model.anims.map((a) => a.name),
            }
          : undefined,
      })
      setResult(r)
      if (r.secret) setSavedSecret(r.secret)
    } catch (err) {
      setError(err instanceof ApiError ? err.hint : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (result) {
    // The server knows the canonical origin; the browser only knows the host
    // it happens to be on.
    const link = result.url || new URL(`/m/${result.id}`, window.location.origin).toString()
    return (
      <div className="share-panel done">
        {savedSecret && (
          <div className="share-secret" role="alert">
            <h3>Save this secret</h3>
            <p>
              It is the only way to delete this model or add a next version to the same
              lineage. It cannot be recovered, and it is shown once.
            </p>
            <code>{savedSecret}</code>
            <div className="share-row">
              <button type="button" onClick={() => navigator.clipboard?.writeText(savedSecret)}>
                Copy secret
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => {
                  acknowledgeSecret()
                  setSavedSecret(null)
                }}
              >
                I have saved it
              </button>
            </div>
          </div>
        )}
        <p className="share-hint">{result.hint}</p>
        <div className="share-row">
          <input readOnly value={link} onFocus={(e) => e.target.select()} />
          <button type="button" onClick={() => navigator.clipboard?.writeText(link)}>
            Copy link
          </button>
          <Link className="share-open" to={`/m/${result.id}`}>Open it →</Link>
        </div>
        {result.existing && !result.owned && (
          <p className="share-warn">
            You do not own this link — change anything in the source and share again to
            get one you do.
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="share-panel">
      <div className="share-row">
        <input
          value={title}
          placeholder={model?.name ? `title (default: ${model.name})` : 'title'}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button type="button" className="primary" disabled={busy} onClick={share}>
          {busy ? 'Sharing…' : 'Share privately'}
        </button>
      </div>
      <p className="share-note">
        Private by default: unlisted, all rights reserved, reachable only by its link.
        You can publish it under CC0 afterwards — that part cannot be undone.
      </p>
      {error && <p className="share-error">{error}</p>}
    </div>
  )
}

export default SharePanel
