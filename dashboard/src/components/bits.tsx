import type { ReactNode } from 'react'
import type { TokenUsage } from '../types'

export function Chip({
  children,
  tone = 'default',
  mono = false,
  title,
}: {
  children: ReactNode
  tone?: 'default' | 'pass' | 'fail' | 'warn' | 'accent'
  mono?: boolean
  title?: string
}) {
  const classes = ['chip']
  if (tone !== 'default') classes.push(tone)
  if (mono) classes.push('mono')
  return (
    <span className={classes.join(' ')} title={title}>
      {children}
    </span>
  )
}

export function Verdict({ passed, label }: { passed: boolean | null | undefined; label?: string }) {
  if (passed === null || passed === undefined) return <Chip>{label ? `${label} —` : '—'}</Chip>
  return (
    <Chip tone={passed ? 'pass' : 'fail'}>
      {label ? `${label} ` : ''}
      {passed ? 'pass' : 'fail'}
    </Chip>
  )
}

export function Score({ value, size = 'md' }: { value: number | null | undefined; size?: 'md' | 'lg' }) {
  if (value === null || value === undefined) return <Chip>—</Chip>
  const tone = value >= 4 ? 'pass' : value >= 3 ? 'warn' : 'fail'
  return (
    <span className={`score ${tone} ${size}`}>
      {value.toFixed(2)}
      <small>/5</small>
    </span>
  )
}

export function Stat({
  k,
  v,
  of,
  delta,
  hint,
}: {
  k: string
  v: ReactNode
  of?: ReactNode
  delta?: { value: number; suffix?: string } | null
  hint?: string
}) {
  return (
    <div className="stat" title={hint}>
      <div className="k">{k}</div>
      <div className="v">
        {v}
        {of !== undefined && <small> / {of}</small>}
      </div>
      {delta ? (
        <div className={`d ${delta.value > 0 ? 'up' : delta.value < 0 ? 'down' : ''}`}>
          {delta.value > 0 ? '+' : ''}
          {delta.value} {delta.suffix ?? 'vs official'}
        </div>
      ) : null}
    </div>
  )
}

export function Bar({ name, value, total, tone }: { name: string; value: number; total: number; tone?: 'pass' }) {
  const pct = total > 0 ? Math.round((100 * value) / total) : 0
  return (
    <div className="bar-row">
      <div className="name">{name}</div>
      <div className="bar-track">
        <div className={`bar-fill${tone ? ` ${tone}` : ''}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="val">
        {value}/{total} · {pct}%
      </div>
    </div>
  )
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="error-box">{error}</div>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US')
}

export function ms(value: number | null | undefined): string {
  if (!value) return '—'
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(1)} s`
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const seconds = Math.max(0, (Date.now() - then) / 1000)
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

export function tokenLine(usage: TokenUsage | null | undefined): string {
  if (!usage) return '—'
  const parts = [`in ${num(usage.input_tokens)}`]
  if (usage.cached_input_tokens) parts.push(`cached ${num(usage.cached_input_tokens)}`)
  parts.push(`out ${num(usage.output_tokens)}`)
  if (usage.reasoning_tokens) parts.push(`reasoning ${num(usage.reasoning_tokens)}`)
  return parts.join(' · ')
}

/** Highlight every occurrence of `needle` inside `text`. */
export function highlight(text: string, needle: string | null): ReactNode {
  if (!needle || needle.length < 4) return text
  const index = text.toLowerCase().indexOf(needle.toLowerCase())
  if (index === -1) return text
  const out: ReactNode[] = []
  let cursor = 0
  let position = index
  let key = 0
  while (position !== -1) {
    out.push(text.slice(cursor, position))
    out.push(<mark key={key++}>{text.slice(position, position + needle.length)}</mark>)
    cursor = position + needle.length
    position = text.toLowerCase().indexOf(needle.toLowerCase(), cursor)
    if (key > 40) break
  }
  out.push(text.slice(cursor))
  return out
}

/** Line diff good enough for two versions of a system prompt. */
export function diffLines(before: string, after: string): { kind: 'same' | 'add' | 'del'; text: string }[] {
  const a = before.split('\n')
  const b = after.split('\n')
  const n = a.length
  const m = b.length
  if (n * m > 400_000) {
    return [{ kind: 'same', text: `(prompts too large to diff: ${n} vs ${m} lines)` }]
  }
  const table: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }
  const out: { kind: 'same' | 'add' | 'del'; text: string }[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] })
      i += 1
      j += 1
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({ kind: 'del', text: a[i] })
      i += 1
    } else {
      out.push({ kind: 'add', text: b[j] })
      j += 1
    }
  }
  while (i < n) {
    out.push({ kind: 'del', text: a[i] })
    i += 1
  }
  while (j < m) {
    out.push({ kind: 'add', text: b[j] })
    j += 1
  }
  return out
}
