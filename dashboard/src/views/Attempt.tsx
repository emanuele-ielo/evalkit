import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, useAsync } from '../api'
import { Empty, ErrorBox, Pill, Score, Tag, Verdict, diffLines, highlight, kb, ms, num, scoreTone, tokenLine } from '../components/bits'
import { JsonValue, countRows } from '../components/json'
import { Markdown } from '../components/md'
import { deriveLede, worstTurn } from '../lib/lede'
import type { AttemptDetail, ClaimCheck, Sibling, ToolCallView, TurnView } from '../types'

type Tab = 'verdict' | 'prompt' | 'llm' | 'raw'

/** What the inspector asked the conversation to highlight. */
interface Focus {
  quote: string
  locator: string
}

function Collapsible({
  title,
  hint,
  children,
  tone,
}: {
  title: string
  hint?: string
  children: ReactNode
  tone?: 'accent' | 'bad'
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="tool" style={tone === 'accent' ? { borderLeft: '2px solid var(--lavender)' } : undefined}>
      <div className="tool-head" onClick={() => setOpen((value) => !value)}>
        <span style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--fg-2)' }}>{title}</span>
        {hint && (
          <span className="kind" style={{ textWrap: 'pretty' }}>
            {hint}
          </span>
        )}
        <span className="spacer" />
        <span className="m">{open ? '−' : '+'}</span>
      </div>
      {open && <div className="tool-body">{children}</div>}
    </div>
  )
}

function ToolCard({ call, focus }: { call: ToolCallView; focus: Focus | null }) {
  const [raw, setRaw] = useState(false)
  const [open, setOpen] = useState(true)

  const rawText = useMemo(() => {
    try {
      return JSON.stringify({ arguments: call.args, result: call.output ?? call.output_raw }, null, 2)
    } catch {
      return call.output_raw
    }
  }, [call])

  const lit = Boolean(focus && call.output_raw.toLowerCase().includes(focus.quote.toLowerCase()))
  const rows = countRows(call.output)
  const structured = call.output !== null && call.output !== undefined

  return (
    <div className={`tool${lit ? ' lit' : ''}${open ? ' open' : ''}`}>
      <div className="tool-head" onClick={() => setOpen((value) => !value)}>
        <span className="sq" />
        <span className="tname">{call.name}</span>
        <span className="kind">tool call</span>
        <span className="peek">{JSON.stringify(call.args ?? {}).slice(0, 160)}</span>
        {call.declared_mock && (
          <Pill tone={call.matches_mock === false ? 'warn' : 'accent'}>
            {call.matches_mock === false ? 'mock drift' : 'mocked'}
          </Pill>
        )}
        <span className="m">
          {[rows !== null ? `${rows} rows` : null, kb(call.output_bytes), call.duration_ms ? ms(call.duration_ms) : null]
            .filter(Boolean)
            .join(' · ')}
        </span>
        <button
          className="ghost"
          onClick={(event) => {
            event.stopPropagation()
            setRaw((value) => !value)
            setOpen(true)
          }}
        >
          {raw ? 'pretty' : '{ } raw'}
        </button>
      </div>

      {open && !raw && (
        <div className="tool-body">
          <section>
            <span className="eyebrow">Arguments</span>
            <JsonValue value={call.args} focus={focus?.quote} />
          </section>
          <section>
            <span className="eyebrow">
              Payload returned to the model
              {call.truncated && <span className="faint"> · capped by the platform at 16 KB</span>}
            </span>
            {structured ? (
              <JsonValue value={call.output} focus={focus?.quote} />
            ) : (
              <pre className="raw">{highlight(call.output_raw, focus?.quote)}</pre>
            )}
            {call.output_parse_error && <span className="faint" style={{ fontSize: 11 }}>{call.output_parse_error}</span>}
          </section>
        </div>
      )}

      {open && raw && <pre className="raw">{highlight(rawText, focus?.quote)}</pre>}
    </div>
  )
}

function Conversation({
  turns,
  focus,
  scores,
}: {
  turns: TurnView[]
  focus: Focus | null
  scores: Map<number, number>
}) {
  return (
    <div className="convo">
      {turns.map((turn) => (
        <section className="turn" key={turn.index}>
          <div className="turn-head">
            <span className="eyebrow">Turn {turn.index + 1}</span>
            {turn.passed !== null && <Verdict passed={turn.passed} label="platform" />}
            <span className="rule" />
          </div>

          {turn.user_message && (
            <div className="msg">
              <span className="who">User</span>
              <div className="bubble user">{highlight(turn.user_message, focus?.quote)}</div>
            </div>
          )}

          {turn.tool_calls.map((call) => (
            <ToolCard key={call.index} call={call} focus={focus} />
          ))}

          <div className="msg">
            <span className="who">Agent</span>
            <div className={`bubble agent${ringFor(scores.get(turn.index), turn.passed)}`}>
              {turn.agent_text ? (
                <Markdown text={turn.agent_text} focus={focus?.quote} />
              ) : (
                <span className="faint">(no user-facing answer)</span>
              )}
            </div>
          </div>

          {turn.expected.expected_output && (
            <Collapsible title="Reference facts the answer owed" hint="what the judge checked against" tone="accent">
              <Markdown text={turn.expected.expected_output} focus={focus?.quote} />
            </Collapsible>
          )}

          {turn.expected.reference_response && (
            <Collapsible title="Reference answer" hint="one good answer, not the only one">
              <Markdown text={turn.expected.reference_response} />
            </Collapsible>
          )}

          {turn.failure_reason && (
            <div className="callout bad">
              <strong>Platform judge</strong>
              <div style={{ marginTop: 4 }}>{turn.failure_reason}</div>
            </div>
          )}
        </section>
      ))}
    </div>
  )
}

/**
 * The ring around an answer marks it as the thing that lost points. Our own score
 * decides that when we have one; the platform's verdict is the fallback for
 * attempts we have not judged.
 */
function ringFor(score: number | undefined, officialPassed: boolean | null): string {
  if (score !== undefined) {
    if (score < 3) return ' flag-bad'
    if (score < 4) return ' flag-warn'
    return ''
  }
  return officialPassed === false ? ' flag-bad' : ''
}

const CLAIM_TONE: Record<string, 'good' | 'warn' | 'bad'> = {
  SUPPORTED: 'good',
  FULL: 'good',
  PRESENT: 'good',
  PARTIAL: 'warn',
  UNSUPPORTED: 'bad',
  CONTRADICTED: 'bad',
  MISS: 'bad',
  ABSENT: 'bad',
}

function Claim({
  status,
  text,
  quote,
  locator,
  note,
  focus,
  onFocus,
}: {
  status: string
  text: string
  quote?: string
  locator?: string
  note?: string
  focus: Focus | null
  onFocus?: (focus: Focus) => void
}) {
  const clickable = Boolean(quote && onFocus)
  const lit = Boolean(quote && focus && focus.quote === quote)
  return (
    <div
      className={`claim${clickable ? ' clickable' : ''}${lit ? ' lit' : ''}`}
      onClick={() => quote && onFocus?.({ quote, locator: locator ?? '' })}
      title={clickable ? 'highlight this evidence in the payload on the left' : undefined}
    >
      <span className={`status ${CLAIM_TONE[status] ?? 'warn'}`}>{status}</span>
      <span className="text">{text}</span>
      {quote ? (
        <>
          <span className="ev">“{quote}”</span>
          {locator && <span className="loc">{locator}</span>}
        </>
      ) : (
        note && <span className="ev faint">{note}</span>
      )}
      {clickable && !lit && <span className="hint">click to locate in the payload</span>}
    </div>
  )
}

function VerdictPanel({ detail, focus, onFocus }: { detail: AttemptDetail; focus: Focus | null; onFocus: (focus: Focus) => void }) {
  const [voteIndex, setVoteIndex] = useState<number | 'aggregate'>('aggregate')
  const verdict = detail.verdict

  if (!verdict) {
    const legacy = detail.legacy_verdict
    return (
      <div className="inspector">
        {legacy ? (
          <div className="callout warn">
            <strong>judged with rubric {legacy.rubric_version} — pass/fail, not 1–5</strong>
            <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Verdict passed={legacy.passed} />
              <span className="faint">by {legacy.judge_model}</span>
            </div>
            <div style={{ marginTop: 8 }}>{legacy.explanation}</div>
            <div style={{ marginTop: 10 }} className="faint">
              A pass/fail cannot be turned into a score without inventing one. Re-judge the campaign to put it on the
              1–5 scale:
              <br />
              <code className="cmd">evalkit judge {detail.campaign.id} --force</code>
            </div>
          </div>
        ) : (
          <Empty>
            not judged yet
            <br />
            <code className="cmd">evalkit judge {detail.campaign.id}</code>
          </Empty>
        )}
      </div>
    )
  }

  const lede = deriveLede(verdict)
  const turn = worstTurn(verdict)
  const vote = voteIndex === 'aggregate' ? null : turn?.votes.find((item) => item.index === voteIndex)?.verdict ?? null
  const official = detail.view.turns[0]?.official

  return (
    <div className="inspector">
      <div className={`lede-card ${lede.tone}`}>
        <span className="kicker">
          <span className="dot" />
          {lede.kicker}
        </span>
        <span className="headline">{lede.headline}</span>
        {verdict.explanation && <span className="body">{verdict.explanation}</span>}
        {verdict.taxonomy.length > 0 && (
          <div className="dist" style={{ marginTop: 2 }}>
            {verdict.taxonomy.map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </div>
        )}
      </div>

      <div className="verdict-top">
        <span className="big-num">
          {verdict.score.toFixed(2)}
          <small> / 5</small>
        </span>
        {verdict.coverage && (
          <Pill tone={verdict.coverage === 'FULL' ? 'good' : verdict.coverage === 'MISS' ? 'bad' : 'warn'}>
            {verdict.coverage}
          </Pill>
        )}
        <Pill tone={verdict.passed ? 'good' : 'bad'}>threshold {verdict.passed ? 'pass' : 'fail'}</Pill>
      </div>

      {turn?.score_capped_by && (
        <div className="notice bad">
          <span className="dot" />
          score capped at 2.00 by a mechanical failure: <span className="mono">{turn.score_capped_by}</span>
        </div>
      )}

      {verdict.turns.map((each) => (
        <div className="group" key={each.turn_index}>
          <h3>
            Criteria — median of {each.votes.length} vote{each.votes.length === 1 ? '' : 's'}
            {verdict.turns.length > 1 && ` · turn ${each.turn_index + 1}`}
          </h3>
          {each.criteria.map((criterion) => (
            <div className="crit-row" key={criterion.name}>
              <span className="cname">
                {criterion.name}
                {!criterion.required && <span className="faint"> (advisory)</span>}
              </span>
              <span className="votes">{criterion.scores.join(' · ') || '—'}</span>
              <Score value={criterion.score} />
            </div>
          ))}
        </div>
      ))}

      {turn && turn.deterministic.length > 0 && (
        <div className="group">
          <h3>Deterministic checks</h3>
          {turn.deterministic.map((check) => (
            <div className="check" key={check.name}>
              <span className={`dot${check.passed ? '' : check.blocking ? ' bad' : ' off'}`} />
              <span className="body">
                <span className="cn">{check.name}</span>
                <span className="cd">{check.hits.length > 0 ? check.hits.join(', ') : check.detail}</span>
              </span>
              <span className="state">
                <Pill tone={check.passed ? 'good' : check.blocking ? 'bad' : 'warn'}>
                  {check.passed ? 'pass' : check.blocking ? 'fail' : 'advisory'}
                </Pill>
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="group">
        <h3>Evidence per vote</h3>
        <div className="votes-row">
          <button className="vote-pill" aria-pressed={voteIndex === 'aggregate'} onClick={() => setVoteIndex('aggregate')}>
            aggregate
          </button>
          {turn?.votes.map((record) => (
            <button
              key={record.index}
              className="vote-pill"
              aria-pressed={voteIndex === record.index}
              onClick={() => setVoteIndex(record.index)}
              title={record.error ?? undefined}
            >
              vote {record.index + 1}
              {record.error
                ? ' ✕'
                : record.verdict
                  ? ` · ${(
                      (record.verdict.grounding_score +
                        record.verdict.completeness_score +
                        record.verdict.clauses_score +
                        record.verdict.provenance_score) /
                      4
                    ).toFixed(1)}`
                  : ''}
            </button>
          ))}
        </div>

        {voteIndex === 'aggregate' ? (
          <span className="faint" style={{ fontSize: 11.5, textWrap: 'pretty' }}>
            Pick a vote to read the claims, reference facts and clauses it checked. Clicking any of them highlights the
            quoted evidence inside the tool payload on the left.
          </span>
        ) : vote ? (
          <>
            <h3 style={{ marginTop: 6 }}>Claims — grounding</h3>
            {vote.claims.length === 0 && <span className="faint" style={{ fontSize: 12 }}>no claims recorded</span>}
            {vote.claims.map((claim: ClaimCheck, index) => (
              <Claim
                key={index}
                status={claim.status}
                text={claim.claim}
                quote={claim.evidence_quote || undefined}
                locator={claim.evidence_locator}
                note={claim.note}
                focus={focus}
                onFocus={onFocus}
              />
            ))}

            <h3 style={{ marginTop: 12 }}>Reference facts — completeness</h3>
            {vote.facts.map((fact, index) => (
              <Claim
                key={index}
                status={fact.status}
                text={fact.fact}
                quote={fact.evidence_quote || undefined}
                note={fact.note}
                focus={focus}
                onFocus={onFocus}
              />
            ))}

            <h3 style={{ marginTop: 12 }}>Clauses</h3>
            {vote.clauses.length === 0 && <span className="faint" style={{ fontSize: 12 }}>none required</span>}
            {vote.clauses.map((clause, index) => (
              <Claim
                key={index}
                status={clause.status}
                text={clause.clause}
                quote={clause.evidence_quote || undefined}
                note={clause.note}
                focus={focus}
                onFocus={onFocus}
              />
            ))}

            <h3 style={{ marginTop: 12 }}>Provenance</h3>
            <dl className="kv">
              <dt>cited</dt>
              <dd>{vote.provenance.cited_sources.join(' · ') || '—'}</dd>
              <dt>invented</dt>
              <dd style={{ color: vote.provenance.invented_sources.length ? 'var(--bad)' : undefined }}>
                {vote.provenance.invented_sources.join(' · ') || 'none'}
              </dd>
            </dl>
            {vote.suggestion && (
              <div className="callout warn">
                <strong>Suggestion</strong>
                <div style={{ marginTop: 4 }}>{vote.suggestion}</div>
              </div>
            )}
          </>
        ) : (
          <div className="callout bad">this vote failed: {turn?.votes.find((r) => r.index === voteIndex)?.error}</div>
        )}
      </div>

      {(official?.explanation || official?.failure_reason) && (
        <div className="card tight">
          <h3>Platform judge said</h3>
          <span className="lede">{official.failure_reason || official.explanation}</span>
          {official.model && (
            <span className="faint" style={{ fontSize: 11 }}>
              {official.model}
              {official.saw_tool_output === false && ' · its prompt carried no tool output'}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function PromptPanel({ detail }: { detail: AttemptDetail }) {
  const view = detail.view
  const [compareRound, setCompareRound] = useState<number | null>(null)
  const other = useAsync(
    () => (compareRound ? api.attempt(detail.campaign.id, view.scenario, compareRound) : Promise.resolve(null)),
    [detail.campaign.id, view.scenario, compareRound],
  )

  const diff = useMemo(() => {
    if (!other.data?.view.system_prompt || !view.system_prompt) return null
    return diffLines(other.data.view.system_prompt, view.system_prompt)
  }, [other.data, view.system_prompt])

  const rounds = detail.siblings.filter((s) => s.scenario === view.scenario && s.round !== view.round).map((s) => s.round)

  return (
    <div className="inspector">
      {!view.system_prompt ? (
        <div className="callout warn">
          <strong>no system prompt for this attempt</strong>
          <div style={{ marginTop: 4 }}>
            {view.trace_note || 'the trace was not available'} — traces expire quickly, so only attempts collected
            against a live trace carry the agent's prompt.
          </div>
        </div>
      ) : (
        <>
          <dl className="kv">
            <dt>hash</dt>
            <dd className="mono">{view.system_prompt_hash}</dd>
            <dt>source</dt>
            <dd className="mono">{view.system_prompt_source}</dd>
            <dt>chars</dt>
            <dd className="num">{num(view.system_prompt.length)}</dd>
            <dt>skill</dt>
            <dd>
              {view.active_skill || '—'}
              {view.routing_decision && <span className="faint"> → {view.routing_decision}</span>}
            </dd>
            <dt>tools</dt>
            <dd className="mono">{view.tools_available.join(', ') || '—'}</dd>
          </dl>

          {rounds.length > 0 && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="faint" style={{ fontSize: 11.5 }}>
                diff vs round
              </span>
              {rounds.map((round) => (
                <button
                  key={round}
                  className="vote-pill"
                  aria-pressed={compareRound === round}
                  onClick={() => setCompareRound(compareRound === round ? null : round)}
                >
                  r{round}
                </button>
              ))}
              {compareRound !== null && diff && (
                <span className="faint" style={{ fontSize: 11 }}>
                  {diff.every((line) => line.kind === 'same') ? 'identical' : 'differs'}
                </span>
              )}
            </div>
          )}

          {diff ? (
            <pre className="block">
              {diff.map((line, index) => (
                <span key={index} className={`diff-line ${line.kind}`}>
                  {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : '  '}
                  {line.text}
                </span>
              ))}
            </pre>
          ) : (
            <pre className="block">{view.system_prompt}</pre>
          )}
        </>
      )}

      {detail.view.turns[0]?.official?.prompt && (
        <div className="group">
          <h3>Platform judge prompt</h3>
          <span className="faint" style={{ fontSize: 11 }}>
            hash {detail.view.turns[0].official?.prompt_hash}
            {detail.view.turns[0].official?.saw_tool_output === false && ' — carried no tool output'}
          </span>
          <pre className="block">{detail.view.turns[0].official?.prompt}</pre>
        </div>
      )}
    </div>
  )
}

function LlmPanel({ detail }: { detail: AttemptDetail }) {
  const { view, verdict } = detail
  return (
    <div className="inspector">
      <div className="group">
        <h3>LLM calls</h3>
        <div className="grid-table">
          <div className="r head">
            <span>Source</span>
            <span>Model</span>
            <span>In</span>
            <span>Cached</span>
            <span>Out</span>
          </div>
          {view.llm_calls.map((call, index) => (
            <div className="r" key={index}>
              <span className="src">{call.source.replace('judge_', 'judge · ')}</span>
              <span className="mdl">{call.model || '—'}</span>
              <span>{num(call.input_tokens)}</span>
              <span className="faint">{num(call.cached_input_tokens)}</span>
              <span>{num(call.output_tokens)}</span>
            </div>
          ))}
          {verdict?.turns.flatMap((turn) =>
            turn.votes.map((record) => (
              <div className="r" key={`v${turn.turn_index}-${record.index}`}>
                <span className="src">judge vote {record.index + 1}</span>
                <span className="mdl">{record.usage?.model || verdict.judge.model}</span>
                <span>{num(record.usage?.input_tokens)}</span>
                <span className="faint">{num(record.usage?.cached_input_tokens)}</span>
                <span>{num(record.usage?.output_tokens)}</span>
              </div>
            )),
          )}
        </div>
      </div>

      <dl className="kv" style={{ paddingTop: 6, borderTop: '1px solid var(--line-1)' }}>
        <dt>agent tokens</dt>
        <dd className="num">{tokenLine(view.agent_tokens)}</dd>
        <dt>our judge</dt>
        <dd className="num">{verdict ? tokenLine(verdict.tokens) : '—'}</dd>
        <dt>agent version</dt>
        <dd>{view.agent_version || '—'}</dd>
        <dt>wall clock</dt>
        <dd className="num">
          {ms(view.execution_time_ms)}
          {view.trace_spans !== null && ` · ${view.trace_spans} trace spans`}
        </dd>
      </dl>

      {view.warnings.length > 0 && (
        <div className="group">
          <h3>Data caveats</h3>
          {view.warnings.map((warning) => (
            <div className="notice" key={warning}>
              <span className="dot" />
              {warning}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RawPanel({ detail }: { detail: AttemptDetail }) {
  const { view, raw, campaign } = detail
  return (
    <div className="inspector">
      <div className="group">
        <h3>Raw payloads</h3>
        <div className="raw-links">
          {(['result', 'trace', 'activity', 'verdict'] as const).map((kind) => (
            <a
              key={kind}
              className="vote-pill"
              aria-disabled={!raw[kind]}
              href={api.rawUrl(campaign.id, view.scenario, view.round, kind)}
              target="_blank"
              rel="noreferrer"
            >
              {kind}.json
            </a>
          ))}
        </div>
      </div>

      <div className="group">
        <h3>Identifiers</h3>
        <dl className="kv">
          <dt>scenario</dt>
          <dd className="mono">{view.scenario}</dd>
          <dt>result id</dt>
          <dd className="mono">{view.result_id || '—'}</dd>
          <dt>communication</dt>
          <dd className="mono">{view.communication_id || '—'}</dd>
          <dt>snapshot</dt>
          <dd className="mono">{view.snapshot_id || '—'}</dd>
          <dt>created</dt>
          <dd>{view.created_at || '—'}</dd>
        </dl>
      </div>

      {view.applied_mocks.length > 0 && (
        <div className="group">
          <h3>Mocks applied at runtime</h3>
          <JsonValue value={view.applied_mocks} />
        </div>
      )}
    </div>
  )
}

export default function Attempt({ id, scenario, round }: { id: string; scenario: string; round: number }) {
  const { data, error, loading } = useAsync(() => api.attempt(id, scenario, round), [id, scenario, round])
  const [tab, setTab] = useState<Tab>('verdict')
  const [focus, setFocus] = useState<Focus | null>(null)

  const turnScores = useMemo(
    () => new Map((data?.verdict?.turns ?? []).map((turn) => [turn.turn_index, turn.score])),
    [data],
  )

  const siblings: Sibling[] = data?.siblings ?? []
  const position = siblings.findIndex((item) => item.scenario === scenario && item.round === round)

  // j/k and arrows move through the campaign without leaving the keyboard.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement) return
      const step = event.key === 'j' || event.key === 'ArrowDown' ? 1 : event.key === 'k' || event.key === 'ArrowUp' ? -1 : 0
      if (step === 0) return
      const next = siblings[position + step]
      if (next) {
        event.preventDefault()
        window.location.hash = `#/c/${encodeURIComponent(id)}/a/${encodeURIComponent(next.scenario)}/${next.round}`
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [siblings, position, id])

  useEffect(() => {
    setFocus(null)
  }, [scenario, round])

  if (error) return <div className="page"><ErrorBox error={error} /></div>
  if (loading && !data) return <div className="page"><Empty>loading…</Empty></div>
  if (!data) return null

  const { view } = data

  return (
    <div className="attempt">
      <div className="pane left">
        <span className="rail-head">
          {siblings.length} attempts · <kbd>j</kbd> <kbd>k</kbd>
        </span>
        {siblings.map((sibling) => {
          const current = sibling.scenario === scenario && sibling.round === round
          const mismatch = sibling.ours !== null && sibling.official !== null && sibling.ours !== sibling.official
          return (
            <a
              key={`${sibling.scenario}-${sibling.round}`}
              className="sib"
              aria-current={current}
              href={`#/c/${encodeURIComponent(id)}/a/${encodeURIComponent(sibling.scenario)}/${sibling.round}`}
            >
              <span className={`dot ${scoreTone(sibling.score)}`} />
              <span className="name">{sibling.short}</span>
              <span className="r">r{sibling.round}</span>
              {mismatch && <span className="mismatch">≠</span>}
              {sibling.score !== null && sibling.score !== undefined && (
                <span className="s">{sibling.score.toFixed(1)}</span>
              )}
            </a>
          )
        })}
      </div>

      <div className="pane center">
        <div className="attempt-head">
          <Pill tone="solid" mono>
            {view.short} · round {view.round}
          </Pill>
          {data.verdict && <Score value={data.verdict.score} />}
          <Verdict passed={view.official_passed} label="platform" />
          {view.agent_model && <Pill mono>{view.agent_model}</Pill>}
          <span className="spacer" />
          {focus && (
            <button className="ghost" onClick={() => setFocus(null)}>
              clear highlight
            </button>
          )}
        </div>
        <Conversation turns={view.turns} focus={focus} scores={turnScores} />
      </div>

      <div className="pane right">
        <div className="tabs">
          {(['verdict', 'prompt', 'llm', 'raw'] as Tab[]).map((option) => (
            <button key={option} className="tab" aria-selected={tab === option} onClick={() => setTab(option)}>
              {option}
            </button>
          ))}
        </div>
        {tab === 'verdict' && <VerdictPanel detail={data} focus={focus} onFocus={setFocus} />}
        {tab === 'prompt' && <PromptPanel detail={data} />}
        {tab === 'llm' && <LlmPanel detail={data} />}
        {tab === 'raw' && <RawPanel detail={data} />}
      </div>
    </div>
  )
}
