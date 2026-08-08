/**
 * Public tips, newest first — the end of each track rather than every node,
 * so the index is not somebody's tuning session.
 */
import { useEffect, useState } from 'react'
import { api, type ModelNode } from '../api'
import { Link } from '../Link'
import './Gallery.css'

export function Gallery() {
  const [models, setModels] = useState<ModelNode[] | null>(null)
  const [hint, setHint] = useState('')

  useEffect(() => {
    api
      .gallery()
      .then((r) => {
        setModels(r.models)
        setHint(r.hint ?? '')
      })
      .catch(() => setModels([]))
  }, [])

  return (
    <div className="gallery">
      <header className="g-head">
        <Link to="/" className="g-back">← wamshare</Link>
      </header>
      <h1>Gallery</h1>
      <p className="g-muted">{hint || 'Loading…'}</p>
      {models && !!models.length && (
        <ul className="g-list">
          {models.map((m) => (
            <li key={m.id}>
              <Link to={`/m/${m.id}`}>
                <span className="g-title">{m.title ?? 'Untitled'}</span>
                {m.description && <span className="g-desc">{m.description}</span>}
                <span className="g-meta">CC0 · {new Date(m.createdAt).toLocaleDateString()}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
export default Gallery
