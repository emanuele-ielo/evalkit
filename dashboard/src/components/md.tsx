/**
 * The smallest markdown renderer that makes agent answers readable.
 *
 * Agents write `**bold**`, `` `code` `` and «quoted versions»; the old dashboard
 * showed those markers raw, which made every answer look broken. This handles
 * exactly what shows up in eval payloads — bold, inline code, guillemets,
 * bullet lists, `# ` headings, blank-line paragraphs — and nothing else.
 */

import type { ReactNode } from 'react'
import { highlight } from './bits'

// The `**` delimiters must be exactly two asterisks. Agents mask phone numbers
// as `**334**********`, and a greedy bold parser reads the tail of that run as an
// opening delimiter and emboldens the rest of the sentence. Requiring that no
// asterisk sits on either side of the pair leaves long runs as literal text.
const INLINE = /((?<!\*)\*\*(?!\*)([^*]+)(?<!\*)\*\*(?!\*)|`([^`]+)`|«([^»]+)»)/

function inline(text: string, focus: string | null | undefined, key: string): ReactNode[] {
  const parts: ReactNode[] = []
  let rest = text
  let index = 0
  while (rest.length > 0) {
    const match = rest.match(INLINE)
    if (!match || match.index === undefined) {
      parts.push(highlight(rest, focus))
      break
    }
    if (match.index > 0) parts.push(highlight(rest.slice(0, match.index), focus))
    if (match[2] !== undefined) {
      parts.push(<strong key={`${key}-${index++}`}>{highlight(match[2], focus)}</strong>)
    } else if (match[3] !== undefined) {
      parts.push(<code key={`${key}-${index++}`}>{highlight(match[3], focus)}</code>)
    } else {
      parts.push(<em key={`${key}-${index++}`}>«{highlight(match[4], focus)}»</em>)
    }
    rest = rest.slice(match.index + match[1].length)
  }
  return parts
}

export function Markdown({ text, focus }: { text: string; focus?: string | null }) {
  const blocks = text.trim().split(/\n\s*\n/)
  return (
    <div className="md">
      {blocks.map((block, blockIndex) => {
        const lines = block.split('\n')
        const bullets = lines.filter((line) => /^\s*[-*]\s+/.test(line))
        if (bullets.length > 0 && bullets.length === lines.filter((line) => line.trim()).length) {
          return (
            <ul key={blockIndex}>
              {bullets.map((line, lineIndex) => (
                <li key={lineIndex}>{inline(line.replace(/^\s*[-*]\s+/, ''), focus, `${blockIndex}-${lineIndex}`)}</li>
              ))}
            </ul>
          )
        }
        if (block.trimStart().startsWith('# ')) {
          return (
            <div className="h" key={blockIndex}>
              {inline(block.trimStart().slice(2), focus, `h${blockIndex}`)}
            </div>
          )
        }
        return <p key={blockIndex}>{inline(block, focus, `p${blockIndex}`)}</p>
      })}
    </div>
  )
}
