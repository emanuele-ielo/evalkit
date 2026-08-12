/**
 * Renders any tool payload as something a human reads instead of a JSON dump.
 *
 * The kit is agent-agnostic, so nothing here knows about Vera's corpus rows.
 * Structure is inferred from the values themselves: a short string is a badge,
 * the first medium string is a heading, long strings are prose, arrays of
 * primitives are chips, arrays of objects are cards. On a retrieval payload
 * that happens to produce exactly the "result row" card you'd have designed by
 * hand — and on an unknown payload it degrades to a readable key/value tree
 * rather than to nothing.
 */

import { useState } from 'react'
import { highlight } from './bits'
import { Markdown } from './md'

const BADGE_MAX = 16
const TITLE_MAX = 120
/** Retrieval payloads run to dozens of rows; show a handful and offer the rest. */
const PREVIEW_ROWS = 5

type Json = unknown

function isObject(value: Json): value is Record<string, Json> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPrimitive(value: Json): boolean {
  return value === null || value === undefined || typeof value !== 'object'
}

function isProse(text: string): boolean {
  return text.length > TITLE_MAX || text.includes('\n') || text.includes('**')
}

/** Does the focused quote live anywhere inside this subtree? */
function contains(value: Json, focus: string | null | undefined): boolean {
  if (!focus || focus.length < 4) return false
  try {
    return JSON.stringify(value)?.toLowerCase().includes(focus.toLowerCase()) ?? false
  } catch {
    return false
  }
}

/** Items in the largest array of objects near the surface — the "N rows" count. */
export function countRows(value: Json, depth = 0): number | null {
  if (depth > 3 || value === null || typeof value !== 'object') return null
  if (Array.isArray(value)) {
    if (value.length > 0 && value.every(isObject)) return value.length
    return null
  }
  let best: number | null = null
  for (const nested of Object.values(value)) {
    const found = countRows(nested, depth + 1)
    if (found !== null && (best === null || found > best)) best = found
  }
  return best
}

/** `{needs_lob_validation: true, ...}` is a flag set, not a tree of fields. */
function flagsOf(value: Record<string, Json>): string[] | null {
  const entries = Object.entries(value)
  if (entries.length === 0 || !entries.every(([, item]) => typeof item === 'boolean')) return null
  return entries.filter(([, item]) => item === true).map(([key]) => key)
}

/** `{source: {version: "APP Aprile 2023"}}` is one fact; render it on one line. */
function foldToScalar(key: string, value: Record<string, Json>): { label: string; text: string } | null {
  const path = [key]
  let current: Json = value
  while (isObject(current)) {
    const entries = Object.entries(current)
    if (entries.length !== 1 || path.length > 4) return null
    path.push(entries[0][0])
    current = entries[0][1]
  }
  if (current === null || current === undefined || typeof current === 'object' || current === '') return null
  return { label: path.join(' › '), text: String(current) }
}

function Str({ text, focus }: { text: string; focus?: string | null }) {
  if (isProse(text)) {
    return (
      <div className="j-prose">
        <Markdown text={text} focus={focus} />
      </div>
    )
  }
  return <span className="j-short">{highlight(text, focus)}</span>
}

/** One object inside an array: badge + heading + prose + footnotes. */
function Card({
  value,
  index,
  total,
  focus,
}: {
  value: Record<string, Json>
  index: number
  total: number
  focus?: string | null
}) {
  const badges: { key: string; text: string }[] = []
  const titles: { key: string; text: string }[] = []
  const prose: { key: string; text: string }[] = []
  const chips: { key: string; items: Json[] }[] = []
  const scalars: { key: string; value: Json }[] = []
  const nested: { key: string; value: Json }[] = []
  const notes: { label: string; text: string }[] = []

  for (const [key, item] of Object.entries(value)) {
    if (typeof item === 'string') {
      if (!item.trim()) continue
      if (isProse(item)) prose.push({ key, text: item })
      else if (item.length <= BADGE_MAX) badges.push({ key, text: item })
      else titles.push({ key, text: item })
    } else if (Array.isArray(item)) {
      if (item.length === 0) continue
      if (item.every(isPrimitive)) chips.push({ key, items: item })
      else nested.push({ key, value: item })
    } else if (isObject(item)) {
      if (Object.keys(item).length === 0) continue
      const flags = flagsOf(item)
      if (flags) {
        if (flags.length > 0) chips.push({ key, items: flags })
        continue
      }
      const folded = foldToScalar(key, item)
      if (folded) notes.push(folded)
      else nested.push({ key, value: item })
    } else if (item !== null && item !== undefined) {
      scalars.push({ key, value: item })
    }
  }

  const heading = titles.shift()
  const lit = contains(value, focus)

  return (
    <div className={`j-card${lit ? ' lit' : ''}`}>
      <div className="card-top">
        <span className="j-count">
          #{index + 1} of {total}
        </span>
        {badges.length > 0 && (
          <span className="badge" title={badges[0].key}>
            {badges[0].text}
          </span>
        )}
        {heading && <span className="title">{highlight(heading.text, focus)}</span>}
        {lit && <span className="cited">judge quoted this row</span>}
      </div>

      {prose.map((entry) => (
        <div className="j-prose" key={entry.key}>
          <Markdown text={entry.text} focus={focus} />
        </div>
      ))}

      {nested.map((entry) => (
        <div className="j-nest" key={entry.key}>
          <span className="j-key">{entry.key}</span>
          <div className="body">
            <JsonValue value={entry.value} focus={focus} />
          </div>
        </div>
      ))}

      {(badges.length > 1 || titles.length > 0 || chips.length > 0 || scalars.length > 0 || notes.length > 0) && (
        <div className="foot">
          {titles.map((entry) => (
            <span className="fk" key={entry.key} title={entry.key}>
              {highlight(entry.text, focus)}
            </span>
          ))}
          {notes.map((entry) => (
            <span className="fk" key={entry.label} title={entry.label}>
              {entry.label.split(' › ').pop()} <span className="j-short">{highlight(entry.text, focus)}</span>
            </span>
          ))}
          {badges.slice(1).map((entry) => (
            <span className="tag" key={entry.key} title={entry.key}>
              {highlight(entry.text, focus)}
            </span>
          ))}
          {scalars.map((entry) => (
            <span className="fk" key={entry.key}>
              {entry.key} <span className="j-scalar">{String(entry.value)}</span>
            </span>
          ))}
          {chips.flatMap((entry) =>
            entry.items.map((item, itemIndex) => (
              <span className="tag" key={`${entry.key}-${itemIndex}`} title={entry.key}>
                {String(item)}
              </span>
            )),
          )}
        </div>
      )}
    </div>
  )
}

/** A long list of rows, truncated until asked — or until the focus is inside one. */
function CardList({ items, focus }: { items: Json[]; focus?: string | null }) {
  const [all, setAll] = useState(false)
  const hiddenHit = items.slice(PREVIEW_ROWS).some((item) => contains(item, focus))
  const expanded = all || hiddenHit
  const shown = expanded ? items : items.slice(0, PREVIEW_ROWS)
  return (
    <div className="j-list">
      {shown.map((item, index) =>
        isObject(item) ? (
          <Card key={index} value={item} index={index} total={items.length} focus={focus} />
        ) : (
          <div className="j-card" key={index}>
            <span className="j-count">
              #{index + 1} of {items.length}
            </span>
            <JsonValue value={item} focus={focus} />
          </div>
        ),
      )}
      {items.length > PREVIEW_ROWS && (
        <button className="ghost" onClick={() => setAll((value) => !value)} style={{ alignSelf: 'flex-start' }}>
          {expanded ? `show first ${PREVIEW_ROWS}` : `+ ${items.length - PREVIEW_ROWS} more rows`}
        </button>
      )}
    </div>
  )
}

/**
 * `{"Response": {"result": {...}}}` is two levels of wrapper and no information.
 * Fold a chain of single-key objects into one breadcrumb.
 */
function foldChain(key: string, value: Record<string, Json>): { label: string; leaf: Record<string, Json> } {
  const path = [key]
  let leaf = value
  for (;;) {
    const inner = Object.entries(leaf)
    if (inner.length !== 1) break
    const [innerKey, innerValue] = inner[0]
    if (!isObject(innerValue) || Object.keys(innerValue).length === 0) break
    path.push(innerKey)
    leaf = innerValue
  }
  return { label: path.join(' › '), leaf }
}

export function JsonValue({ value, focus }: { value: Json; focus?: string | null }) {
  if (value === null || value === undefined) return <span className="j-null">null</span>

  if (typeof value === 'boolean' || typeof value === 'number') {
    return <span className="j-scalar">{String(value)}</span>
  }

  if (typeof value === 'string') return <Str text={value} focus={focus} />

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="j-null">[ ]</span>
    if (value.every(isPrimitive)) {
      return (
        <div className="j-chips">
          {value.map((item, index) => (
            <span className="j-chip" key={index}>
              {String(item)}
            </span>
          ))}
        </div>
      )
    }
    return <CardList items={value} focus={focus} />
  }

  const entries = Object.entries(value as Record<string, Json>)
  if (entries.length === 0) return <span className="j-null">{'{ }'}</span>

  return (
    <div className="j-obj">
      {entries.map(([key, item]) => {
        const complex =
          (isObject(item) && Object.keys(item).length > 0) || (Array.isArray(item) && item.length > 0)
        if (!complex) {
          return (
            <div className="j-pair" key={key}>
              <span className="j-key">{key}</span>
              <JsonValue value={item} focus={focus} />
            </div>
          )
        }
        const folded = isObject(item) ? foldChain(key, item) : null
        return (
          <div className="j-nest" key={key}>
            <span className="j-key">
              {folded ? folded.label : key}
              {Array.isArray(item) && <span className="j-count"> · {item.length} items</span>}
            </span>
            <div className="body">
              <JsonValue value={folded ? folded.leaf : item} focus={focus} />
            </div>
          </div>
        )
      })}
    </div>
  )
}
