/**
 * Shell and routes. Four of them: landing, gallery, your models, and a model
 * page — which is really a lineage page.
 */
import { useState } from 'react'
import { SharePanel } from './components/SharePanel'
import { Gallery } from './pages/Gallery'
import { Landing } from './pages/Landing'
import { Mine } from './pages/Mine'
import { ModelPage } from './pages/ModelPage'
import { usePath } from './router'
import type { WamModel } from './wam/types'

export default function App() {
  const [path, navigate] = usePath()
  // What the drop-zone turntable last compiled, so the landing page can offer
  // to turn it into a link.
  const [shareable, setShareable] = useState<{ model: WamModel; source: string } | null>(null)

  const model = /^\/m\/([A-Za-z0-9_-]+)\/?$/.exec(path)
  if (model) return <ModelPage id={model[1]} navigate={navigate} />
  if (path === '/gallery') return <Gallery />
  if (path === '/mine') return <Mine />

  return (
    <Landing
      onShareable={(m, source) => setShareable({ model: m, source })}
      shareSlot={
        shareable ? <SharePanel source={shareable.source} model={shareable.model} /> : null
      }
    />
  )
}
