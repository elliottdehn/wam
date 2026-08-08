/**
 * Shows WAM source, highlighted, with a button to take it away.
 *
 *   <WamSource text={wamText} />
 *   <WamSource text={wamText} filename="king_black_dragon.wam" maxLines={24} />
 *
 * The download is the point: the render is the advertisement, the source is
 * the thing you can actually change six numbers in.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { modelName, tokenizeWam, type Token } from '../wam/highlight'
import './WamSource.css'

export interface WamSourceProps {
  text: string
  /** Defaults to the source's own `model <name>` line. */
  filename?: string
  /** Collapse past this many lines, with a control to expand. */
  maxLines?: number
  className?: string
}

function TokenSpan({ token }: { token: Token }) {
  if (token.kind === 'color') {
    return (
      <span className="t-color">
        <i className="swatch" style={{ background: token.value }} aria-hidden="true" />
        {token.value}
      </span>
    )
  }
  if (token.kind === 'text') return <>{token.value}</>
  return <span className={`t-${token.kind}`}>{token.value}</span>
}

export function WamSource({ text, filename, maxLines, className }: WamSourceProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  const lines = useMemo(() => tokenizeWam(text), [text])
  const name = filename ?? `${modelName(text) ?? 'model'}.wam`
  const clipped = maxLines != null && !expanded && lines.length > maxLines
  const shown = clipped ? lines.slice(0, maxLines) : lines

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const download = () => {
    // A Blob URL rather than a data: URI — a large model would otherwise be a
    // very long href, and some browsers refuse to navigate to one.
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    // Revoking immediately can cancel the download in some browsers.
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard is permission-gated and blocked on insecure origins; the
      // download button still works, so this needs no error state.
    }
  }

  return (
    <div className={['wam-source', className].filter(Boolean).join(' ')}>
      <div className="wam-source-bar">
        <span className="wam-source-name">{name}</span>
        <span className="wam-source-meta">
          {lines.length} lines · {new Blob([text]).size} B
        </span>
        <div className="wam-source-actions">
          <button type="button" onClick={copy} aria-live="polite">
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button type="button" className="primary" onClick={download}>
            Download <code>.wam</code>
          </button>
        </div>
      </div>

      <pre className="wam-source-code" tabIndex={0}>
        <code>
          {shown.map((line) => (
            <span className="row" key={line.no}>
              <span className="ln" aria-hidden="true">
                {line.no}
              </span>
              <span className="src">
                {line.tokens.map((t, i) => (
                  <TokenSpan key={i} token={t} />
                ))}
                {'\n'}
              </span>
            </span>
          ))}
        </code>
      </pre>

      {maxLines != null && lines.length > maxLines && (
        <button
          type="button"
          className="wam-source-more"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Show less' : `Show all ${lines.length} lines`}
        </button>
      )}
    </div>
  )
}

export default WamSource
