/**
 * The install strip, except the install command is a prompt.
 *
 * Every other tool's hero has `npm install thing` and a copy button. The
 * equivalent here is not a command — you do not install WAM, you point an
 * agent at it — so the strip is the sentence you paste into Claude Code.
 *
 * The default state is the **plain** prompt with nothing filled in, so Copy is
 * useful on the first click. A description is optional and appended after the
 * colon; leave it blank and the colon goes with it, because a prompt ending in
 * a bare `:` reads as truncated when it lands in someone's terminal.
 */
import { useEffect, useRef, useState } from 'react'
import './PromptStrip.css'

const REPO = 'https://github.com/elliottdehn/wam'
const BASE = `Clone ${REPO} and make me a model that I can see`

/**
 * Deliberately modest. These are the level of ambition that comes out well on
 * a first pass — one creature, one clear silhouette, no equipment layer.
 */
const PREPARED = [
  'a giant scorpion with its sting raised',
  'a squat stone golem',
  'a treant with a hollow trunk',
  'a plague rat that walks on two legs',
]

export interface PromptStripProps {
  className?: string
}

export function PromptStrip({ className }: PromptStripProps) {
  const [description, setDescription] = useState('')
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const detail = description.trim()
  const prompt = detail ? `${BASE}: ${detail}.` : `${BASE}.`

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(prompt)
    } catch {
      // Clipboard is permission-gated and blocked on insecure origins. Select
      // the sentence instead so the keyboard shortcut still works.
      const el = document.getElementById('prompt-text')
      if (el) {
        const range = document.createRange()
        range.selectNodeContents(el)
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(range)
      }
      return
    }
    setCopied(true)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(false), 1800)
  }

  const use = (text: string) => {
    setDescription(text)
    inputRef.current?.focus()
  }

  return (
    <div className={['prompt-strip', className].filter(Boolean).join(' ')}>
      <div className="prompt-box">
        <div className="prompt-line">
          <span className="prompt-caret" aria-hidden="true">
            ›
          </span>
          <p className="prompt-text" id="prompt-text">
            Clone <span className="prompt-repo">{REPO}</span> and make me a model
            that I can see
            <span className="prompt-tail">{detail ? `: ${detail}.` : '.'}</span>
          </p>
          <button type="button" className="prompt-copy" onClick={copy}>
            {copied ? 'Copied' : 'Copy prompt'}
          </button>
        </div>

        <div className="prompt-fill">
          <label htmlFor="prompt-description">optional</label>
          <input
            id="prompt-description"
            ref={inputRef}
            className="prompt-description"
            value={description}
            spellCheck={false}
            placeholder="describe what you want, or leave it to the agent"
            onChange={(e) => setDescription(e.target.value)}
          />
          {detail && (
            <button
              type="button"
              className="prompt-clear"
              onClick={() => setDescription('')}
              aria-label="Clear the description"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <div className="prompt-hint">
        <span>Paste into Claude Code. Or start from one of these:</span>
        <span className="prompt-chips">
          {PREPARED.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => use(s)}
              aria-pressed={detail === s}
              className={detail === s ? 'on' : undefined}
            >
              {s}
            </button>
          ))}
        </span>
      </div>
    </div>
  )
}

export default PromptStrip
