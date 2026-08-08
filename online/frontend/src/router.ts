/**
 * A router in thirty lines, because the site has four routes and a dependency
 * for that would be more code than this.
 *
 * Version scrubbing on a model page pushes state rather than replacing it, so
 * the back button undoes a step through the lineage the way it looks like it
 * should.
 */
import { useCallback, useEffect, useState } from 'react'

export function usePath(): [string, (to: string, opts?: { replace?: boolean }) => void] {
  const [path, setPath] = useState(() => window.location.pathname)

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const navigate = useCallback((to: string, opts: { replace?: boolean } = {}) => {
    if (to === window.location.pathname) return
    window.history[opts.replace ? 'replaceState' : 'pushState']({}, '', to)
    setPath(to)
    window.scrollTo(0, 0)
  }, [])

  return [path, navigate]
}
