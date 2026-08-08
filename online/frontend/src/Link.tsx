/**
 * An anchor that routes in-page, while still behaving like a link for
 * modified clicks — cmd-click has to open a new tab or it is not a link.
 */
export function Link({
  to,
  children,
  ...rest
}: { to: string } & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      href={to}
      {...rest}
      onClick={(e) => {
        // Let modified clicks open a new tab, as a real link should.
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
        e.preventDefault()
        window.history.pushState({}, '', to)
        window.dispatchEvent(new PopStateEvent('popstate'))
        window.scrollTo(0, 0)
      }}
    >
      {children}
    </a>
  )
}
