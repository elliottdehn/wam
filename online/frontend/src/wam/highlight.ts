/**
 * A tokenizer for WAM source.
 *
 * Deliberately **structural, not lexical**: it highlights the first word of a
 * line, `key=` pairs, numbers, hex colours and comments — and never consults a
 * list of keywords. WAM's vocabulary is still growing (`curl`, `swing`,
 * `continues`, `around` all postdate this branch's compiler), and a hardcoded
 * keyword list would quietly stop colouring the newest constructs, which are
 * exactly the ones worth seeing. Structure is stable; the word list is not.
 */
export type TokenKind =
  | 'section'   // a top-level block: model, palette, skeleton, parts, …
  | 'directive' // the first word of an indented line: bone, loft, ring, …
  | 'key'       // the left side of key=value
  | 'number'
  | 'color'     // #rrggbb — rendered with a swatch
  | 'comment'
  | 'punct'
  | 'text'

export interface Token {
  kind: TokenKind
  value: string
}

export interface Line {
  no: number
  tokens: Token[]
}

const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/
const NUMBER = /^-?(?:\d+\.?\d*|\.\d+)%?/
const WORD = /^[A-Za-z_][\w.*-]*/

/**
 * `#` is both the comment marker and the start of a hex colour, and a palette
 * line has one of each. Splitting on the first `#` that is not a colour is the
 * whole trick.
 */
function splitComment(line: string): [string, string | null] {
  for (let i = 0; i < line.length; i++) {
    if (line[i] !== '#') continue
    if (HEX.test(line.slice(i))) {
      i += HEX.exec(line.slice(i))![0].length - 1
      continue
    }
    return [line.slice(0, i), line.slice(i)]
  }
  return [line, null]
}

export function tokenizeWam(source: string): Line[] {
  return source.replace(/\r\n?/g, '\n').split('\n').map((raw, i) => {
    const tokens: Token[] = []
    const [code, comment] = splitComment(raw)

    let rest = code
    let first = true
    // A section header sits flush left; anything indented belongs to one.
    const indented = /^\s/.test(raw)

    while (rest.length) {
      const ws = /^\s+/.exec(rest)
      if (ws) {
        tokens.push({ kind: 'text', value: ws[0] })
        rest = rest.slice(ws[0].length)
        continue
      }
      let m: RegExpExecArray | null
      if ((m = HEX.exec(rest))) {
        tokens.push({ kind: 'color', value: m[0] })
      } else if ((m = NUMBER.exec(rest))) {
        tokens.push({ kind: 'number', value: m[0] })
      } else if ((m = WORD.exec(rest))) {
        const isKey = rest[m[0].length] === '='
        tokens.push({
          kind: isKey ? 'key' : first ? (indented ? 'directive' : 'section') : 'text',
          value: m[0],
        })
        first = false
      } else {
        // Exactly one character. A greedier class swallowed the digits after
        // an `=` (`rough=0.8` tokenized as key + `=0.8`), which silently
        // dropped the number highlighting on every key=value in the file.
        m = /^./.exec(rest)!
        tokens.push({ kind: 'punct', value: m[0] })
      }
      rest = rest.slice(m[0].length)
    }

    if (comment !== null) tokens.push({ kind: 'comment', value: comment })
    return { no: i + 1, tokens }
  })
}

/** The `model <name>` line, for naming the download. */
export function modelName(source: string): string | null {
  const m = /^\s*model\s+([A-Za-z_][\w-]*)/m.exec(source)
  return m ? m[1] : null
}
