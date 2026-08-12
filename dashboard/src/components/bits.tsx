import type { ReactNode } from 'react'
import type { TokenUsage } from '../types'

export type Tone = 'default' | 'good' | 'warn' | 'bad' | 'info' | 'accent' | 'solid'

/** The one place the 1–5 scale turns into a colour. */
export function scoreTone(value: number | null | undefined): 'good' | 'warn' | 'bad' | 'none' {
  if (value === null || value === undefined) return 'none'
  if (value >= 4) return 'good'
  if (value >= 3) return 'warn'
  return 'bad'
}

/**
 * Does this campaign actually carry 1–5 scores?
 *
 * A mean on the 1–5 scale can never legitimately be 0, so a zero mean means the
 * campaign was judged with the old pass/fail rubric and the score fields are
 * just unset defaults. Showing "0.00 / 5" for those would invent a number.
 */
export function hasScores(report: { mean_score: number; attempts_judged: number } | null | undefined): boolean {
  return Boolean(report && report.attempts_judged > 0 && report.mean_score > 0)
}

export function Pill({
  children,
  tone = 'default',
  mono = false,
  title,
}: {
  children: ReactNode
  tone?: Tone
  mono?: boolean
  title?: string
}) {
  const classes = ['pill']
  if (tone !== 'default') classes.push(tone)
  if (mono) classes.push('mono')
  return (
    <span className={classes.join(' ')} title={title}>
      {children}
    </span>
  )
}

export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="tag" title={title}>
      {children}
    </span>
  )
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <span className="eyebrow">{children}</span>
}

export function Verdict({ passed, label }: { passed: boolean | null | undefined; label?: string }) {
  if (passed === null || passed === undefined) return <Pill>{label ? `${label} —` : '—'}</Pill>
  return (
    <Pill tone={passed ? 'good' : 'bad'}>
      {label ? `${label} ` : ''}
      {passed ? 'pass' : 'fail'}
    </Pill>
  )
}

export function Score({ value, digits = 2 }: { value: number | null | undefined; digits?: number }) {
  if (value === null || value === undefined) return <span className="score none">—</span>
  return (
    <span className={`score ${scoreTone(value)}`}>
      {value.toFixed(digits)}
      <small>/5</small>
    </span>
  )
}

/** The big number in a hero stat card. */
export function HeroNum({ value, of }: { value: ReactNode; of?: ReactNode }) {
  return (
    <span className="hero-num">
      {value}
      {of !== undefined && <small> / {of}</small>}
    </span>
  )
}

export function Stat({
  k,
  value,
  of,
  foot,
  tone,
  hint,
}: {
  k: string
  value: ReactNode
  of?: ReactNode
  foot?: ReactNode
  tone?: 'up' | 'down'
  hint?: string
}) {
  return (
    <div className="stat" title={hint}>
      <span className="k">{k}</span>
      <HeroNum value={value} of={of} />
      {foot !== undefined && <span className={`foot${tone ? ` ${tone}` : ''}`}>{foot}</span>}
    </div>
  )
}

/** A criterion or ratio bar. `max` defaults to the 1–5 scale. */
export function Bar({
  name,
  value,
  max = 5,
  label,
  tone,
}: {
  name: string
  value: number
  max?: number
  label?: string
  tone?: 'good' | 'warn' | 'bad' | 'info'
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
  return (
    <div className="bar-row">
      <span className="name">{name}</span>
      <span className="bar-track">
        <span className={`bar-fill${tone ? ` ${tone}` : ''}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="val">{label ?? value.toFixed(2)}</span>
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

export function kb(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
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
export function highlight(text: string, needle: string | null | undefined): ReactNode {
  if (!needle || needle.length < 4) return text
  const haystack = text.toLowerCase()
  const target = needle.toLowerCase()
  let position = haystack.indexOf(target)
  if (position === -1) return text
  const out: ReactNode[] = []
  let cursor = 0
  let key = 0
  while (position !== -1) {
    out.push(text.slice(cursor, position))
    out.push(<mark key={key++}>{text.slice(position, position + needle.length)}</mark>)
    cursor = position + needle.length
    position = haystack.indexOf(target, cursor)
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
