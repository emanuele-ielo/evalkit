import { useEffect, useMemo, useRef, useState } from 'react'
import { api, useAsync } from '../api'
import { Chip, Empty, ErrorBox, Score, Verdict, diffLines, highlight, ms, num, tokenLine } from '../components/bits'
import type { AttemptDetail, ClaimCheck, Sibling, ToolCallView, TurnView } from '../types'

type Tab = 'verdict' | 'prompt' | 'llm' | 'raw'

/** What the inspector asked the conversation to highlight. */
interface Focus {
  quote: string
  locator: string
}

function ToolCard({
  call,
  focus,
  open,
  onToggle,
}: {
  call: ToolCallView
  focus: Focus | null
  open: boolean
  onToggle: () => void
}) {
  const payload = useMemo(() => {
    try {
      return JSON.stringify(call.output ?? call.output_raw, null, 1)
    } catch {
      return call.output_raw
    }
  }, [call])

  const quoteHere = focus && payload.toLowerCase().includes(focus.quote.toLowerCase()) ? focus.quote : null

  return (
    <details className={`tool${quoteHere ? ' highlight' : ''}`} open={open}>
      <summary
        onClick={(event) => {
          event.preventDefault()
          onToggle()
        }}
      >
        <span className="tname">{call.name}</span>
        <span className="args-preview">{JSON.stringify(call.args ?? {})}</span>
        {call.declared_mock && (
          <Chip tone={call.matches_mock === false ? 'warn' : 'accent'}>
            {call.matches_mock === false ? 'mock drift' : 'mocked'}
          </Chip>
        )}
        <Chip>{(call.output_bytes / 1024).toFixed(1)} KB</Chip>
        {call.duration_ms !== null && <Chip>{ms(call.duration_ms)}</Chip>}
      </summary>
      <div className="tool-body">
        <div>
          <h4>arguments</h4>
          <pre className="payload">{JSON.stringify(call.args ?? {}, null, 1)}</pre>
        </div>
        <div>
          <h4>
            payload returned to the model
            {call.truncated && <span className="faint"> · truncated by the platform</span>}
          </h4>
          <pre className="payload">{highlight(payload, quoteHere)}</pre>
        </div>
      </div>
    </details>
  )
}

function Conversation({
  turns,
  focus,
  openTools,
  toggleTool,
}: {
  turns: TurnView[]
  focus: Focus | null
  openTools: Set<string>
  toggleTool: (key: string) => void
}) {
  return (
    <div className="convo">
      {turns.map((turn) => (
        <section key={turn.index}>
          <div className="turn-head">
            <Chip mono>turn {turn.index + 1}</Chip>
            {turn.passed !== null && <Verdict passed={turn.passed} label="official" />}
            <span className="line" />
          </div>

          {turn.user_message && (
            <div className="msg">
              <div className="who">user</div>
              <div className="bubble user">{turn.user_message}</div>
            </div>
          )}

          {turn.messages
            .filter((message) => message.role === 'tool')
            .map((message) => {
              const call = turn.tool_calls[message.tool_index ?? -1]
              if (!call) return null
              const key = `${turn.index}:${call.index}`
              return (
                <ToolCard
                  key={key}
                  call={call}
                  focus={focus}
                  open={openTools.has(key) || Boolean(focus && call.output_raw.toLowerCase().includes(focus.quote.toLowerCase()))}
                  onToggle={() => toggleTool(key)}
                />
              )
            })}

          <div className="msg">
            <div className="who">agent</div>
            <div className={`bubble agent${turn.passed === false ? ' failed' : ''}`}>
              {turn.agent_text || <span className="faint">(no user-facing answer)</span>}
            </div>
          </div>

          {turn.expected.expected_output && (
            <div className="callout accent">
              <strong>reference facts the answer owed</strong>
              <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{turn.expected.expected_output}</div>
            </div>
          )}
          {turn.expected.reference_response && (
            <details className="callout">
              <summary style={{ cursor: 'pointer' }}>
                <strong>reference answer</strong> <span className="faint">(one good answer, not the only one)</span>
              </summary>
              <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{turn.expected.reference_response}</div>
            </details>
          )}
          {turn.failure_reason && (
            <div className="callout fail">
              <strong>platform judge</strong>
              <div style={{ marginTop: 4 }}>{turn.failure_reason}</div>
            </div>
          )}
        </section>
      ))}
    </div>
  )
}

function Claim({ claim, onFocus }: { claim: ClaimCheck; onFocus: (focus: Focus) => void }) {
  return (
    <div
      className="claim"
      onClick={() => claim.evidence_quote && onFocus({ quote: claim.evidence_quote, locator: claim.evidence_locator })}
      title={claim.evidence_quote ? 'click to highlight the evidence in the payload' : undefined}
    >
      <div className="top">
        <span className={`status ${claim.status}`}>{claim.status}</span>
      </div>
      <div className="text">{claim.claim}</div>
      {claim.evidence_quote ? (
        <>
          <div className="ev">“{claim.evidence_quote}”</div>
          <div className="loc">{claim.evidence_locator}</div>
        </>
      ) : (
        claim.note && <div className="ev faint">{claim.note}</div>
      )}
    </div>
  )
}

function VerdictPanel({ detail, onFocus }: { detail: AttemptDetail; onFocus: (focus: Focus) => void }) {
  const [voteIndex, setVoteIndex] = useState<number | 'aggregate'>('aggregate')
  const verdict = detail.verdict
  if (!verdict) {
    const legacy = detail.legacy_verdict
    return (
      <div className="inspector">
        {legacy ? (
          <div className="callout warn">
            <strong>judged with rubric {legacy.rubric_version} (pass/fail)</strong>
            <div style={{ marginTop: 6 }}>
              <Verdict passed={legacy.passed} /> <span className="faint">by {legacy.judge_model}</span>
            </div>
            <div style={{ marginTop: 6 }}>{legacy.explanation}</div>
            <div style={{ marginTop: 8 }} className="faint">
              The 1–5 scale cannot be derived from a pass/fail verdict — re-judge this campaign to score it:
              <br />
              <code className="mono">evalkit judge {detail.campaign.id} --force</code>
            </div>
          </div>
        ) : (
          <Empty>
            not judged yet — run
            <br />
            <code className="mono">evalkit judge {detail.campaign.id}</code>
          </Empty>
        )}
      </div>
    )
  }
  const turn = verdict.turns[0]
  const vote = voteIndex === 'aggregate' ? null : turn?.votes.find((item) => item.index === voteIndex)?.verdict ?? null

  return (
    <div className="inspector">
      <div className="verdict-head">
        <Score value={verdict.score} size="lg" />
        {verdict.coverage && <Chip tone={verdict.coverage === 'FULL' ? 'pass' : verdict.coverage === 'MISS' ? 'fail' : 'warn'}>{verdict.coverage}</Chip>}
        <Verdict passed={verdict.passed} label="threshold" />
      </div>
      {turn?.score_capped_by && (
        <div className="callout warn" style={{ fontSize: 12 }}>
          score capped at {2} by a mechanical failure: <span className="mono">{turn.score_capped_by}</span>
        </div>
      )}

      <p className="subtle" style={{ fontSize: 12.5 }}>{verdict.explanation}</p>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        {verdict.taxonomy.map((tag) => (
          <Chip key={tag} mono tone="warn">{tag}</Chip>
        ))}
      </div>

      <h3>criteria — median of the votes</h3>
      <table className="plain">
        <thead>
          <tr>
            <th>criterion</th>
            <th className="num">votes</th>
            <th className="num">median</th>
          </tr>
        </thead>
        <tbody>
          {turn?.criteria.map((criterion) => (
            <tr key={criterion.name}>
              <td>
                {criterion.name}
                {!criterion.required && <span className="faint"> (advisory)</span>}
              </td>
              <td className="num faint">{criterion.scores.join(' · ') || '—'}</td>
              <td className="num" style={{ width: 68 }}>
                <Score value={criterion.score} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ marginTop: 16 }}>deterministic checks</h3>
      <table className="plain">
        <tbody>
          {turn?.deterministic.map((check) => (
            <tr key={check.name}>
              <td className="mono" style={{ fontSize: 11.5 }}>{check.name}</td>
              <td>{check.hits.length > 0 ? <span className="faint">{check.hits.join(', ')}</span> : <span className="faint">{check.detail}</span>}</td>
              <td style={{ width: 52 }}><Verdict passed={check.passed} /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ marginTop: 16 }}>evidence per vote</h3>
      <div className="vote-tabs">
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
        <p className="faint" style={{ fontSize: 12 }}>
          Pick a vote to read its claims, facts and clauses. Clicking a claim highlights its quoted evidence inside the tool
          payload on the left.
        </p>
      ) : vote ? (
        <>
          <h3>claims (grounding)</h3>
          {vote.claims.map((claim, index) => (
            <Claim key={index} claim={claim} onFocus={onFocus} />
          ))}

          <h3 style={{ marginTop: 14 }}>reference facts (completeness)</h3>
          {vote.facts.map((fact, index) => (
            <div className="claim" key={index}>
              <div className="top">
                <span className={`status ${fact.status === 'FULL' ? 'SUPPORTED' : fact.status === 'MISS' ? 'UNSUPPORTED' : 'PARTIAL'}`}>
                  {fact.status}
                </span>
              </div>
              <div className="text">{fact.fact}</div>
              {fact.evidence_quote && <div className="ev">“{fact.evidence_quote}”</div>}
              {fact.note && <div className="loc">{fact.note}</div>}
            </div>
          ))}

          <h3 style={{ marginTop: 14 }}>clauses</h3>
          {vote.clauses.length === 0 && <div className="faint" style={{ fontSize: 12 }}>none required</div>}
          {vote.clauses.map((clause, index) => (
            <div className="claim" key={index}>
              <div className="top">
                <span className={`status ${clause.status === 'PRESENT' ? 'SUPPORTED' : clause.status === 'ABSENT' ? 'UNSUPPORTED' : 'PARTIAL'}`}>
                  {clause.status}
                </span>
              </div>
              <div className="text">{clause.clause}</div>
              {clause.evidence_quote && <div className="ev">“{clause.evidence_quote}”</div>}
            </div>
          ))}

          <h3 style={{ marginTop: 14 }}>provenance</h3>
          <dl className="kv">
            <dt>cited</dt>
            <dd>{vote.provenance.cited_sources.join(' · ') || '—'}</dd>
            <dt>invented</dt>
            <dd className={vote.provenance.invented_sources.length ? 'mono' : 'faint'}>
              {vote.provenance.invented_sources.join(' · ') || 'none'}
            </dd>
          </dl>
          {vote.suggestion && (
            <div className="callout warn" style={{ marginTop: 12 }}>
              <strong>suggestion</strong>
              <div style={{ marginTop: 4 }}>{vote.suggestion}</div>
            </div>
          )}
        </>
      ) : (
        <div className="callout fail">this vote failed: {turn?.votes.find((r) => r.index === voteIndex)?.error}</div>
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
            {view.trace_note || 'the trace was not available'} — traces expire quickly, so only attempts collected with a
            live trace carry the agent's prompt.
          </div>
        </div>
      ) : (
        <>
          <dl className="kv">
            <dt>hash</dt>
            <dd className="mono">{view.system_prompt_hash}</dd>
            <dt>source</dt>
            <dd className="mono" style={{ fontSize: 11 }}>{view.system_prompt_source}</dd>
            <dt>chars</dt>
            <dd>{num(view.system_prompt.length)}</dd>
            <dt>skill</dt>
            <dd>{view.active_skill || '—'} {view.routing_decision && <span className="faint">→ {view.routing_decision}</span>}</dd>
            <dt>tools</dt>
            <dd className="mono" style={{ fontSize: 11 }}>{view.tools_available.join(', ') || '—'}</dd>
          </dl>

          {rounds.length > 0 && (
            <div className="filters" style={{ marginTop: 12 }}>
              <span className="faint" style={{ fontSize: 12 }}>diff vs round</span>
              {rounds.map((round) => (
                <button
                  key={round}
                  className="toggle"
                  aria-pressed={compareRound === round}
                  onClick={() => setCompareRound(compareRound === round ? null : round)}
                >
                  r{round}
                </button>
              ))}
            </div>
          )}

          {diff ? (
            <pre className="block" style={{ marginTop: 10 }}>
              {diff.map((line, index) => (
                <span key={index} className={`diff-line ${line.kind}`}>
                  {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : '  '}
                  {line.text}
                </span>
              ))}
            </pre>
          ) : (
            <pre className="block" style={{ marginTop: 10 }}>{view.system_prompt}</pre>
          )}
        </>
      )}

      {detail.view.turns[0]?.official?.prompt && (
        <details style={{ marginTop: 14 }}>
          <summary className="subtle" style={{ cursor: 'pointer', fontSize: 12.5 }}>
            platform judge prompt (hash {detail.view.turns[0].official?.prompt_hash}
            {detail.view.turns[0].official?.saw_tool_output === false && ' — carried no tool output'})
          </summary>
          <pre className="block" style={{ marginTop: 8 }}>{detail.view.turns[0].official?.prompt}</pre>
        </details>
      )}
    </div>
  )
}

function LlmPanel({ detail }: { detail: AttemptDetail }) {
  const { view, verdict } = detail
  return (
    <div className="inspector">
      <h3>llm calls</h3>
      <table className="plain">
        <thead>
          <tr>
            <th>source</th>
            <th>model</th>
            <th className="num">in</th>
            <th className="num">cached</th>
            <th className="num">out</th>
            <th className="num">ms</th>
          </tr>
        </thead>
        <tbody>
          {view.llm_calls.map((call, index) => (
            <tr key={index}>
              <td>{call.source.replace('judge_', 'judge:')}</td>
              <td className="mono" style={{ fontSize: 11 }}>{call.model || '—'}</td>
              <td className="num">{num(call.input_tokens)}</td>
              <td className="num">{num(call.cached_input_tokens)}</td>
              <td className="num">{num(call.output_tokens)}</td>
              <td className="num">{call.duration_ms ? Math.round(call.duration_ms) : '—'}</td>
            </tr>
          ))}
          {verdict?.turns.flatMap((turn) =>
            turn.votes.map((record) => (
              <tr key={`v${turn.turn_index}-${record.index}`}>
                <td>judge:ours vote {record.index + 1}</td>
                <td className="mono" style={{ fontSize: 11 }}>{record.usage?.model || verdict.judge.model}</td>
                <td className="num">{num(record.usage?.input_tokens)}</td>
                <td className="num">{num(record.usage?.cached_input_tokens)}</td>
                <td className="num">{num(record.usage?.output_tokens)}</td>
                <td className="num">{record.duration_ms ? Math.round(record.duration_ms) : '—'}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>

      <h3 style={{ marginTop: 16 }}>totals</h3>
      <dl className="kv">
        <dt>agent tokens</dt>
        <dd className="mono">{tokenLine(view.agent_tokens)}</dd>
        <dt>our judge</dt>
        <dd className="mono">{verdict ? tokenLine(verdict.tokens) : '—'}</dd>
        <dt>agent version</dt>
        <dd className="mono" style={{ fontSize: 11.5 }}>{view.agent_version || '—'}</dd>
        <dt>wall clock</dt>
        <dd>{ms(view.execution_time_ms)}</dd>
        <dt>trace spans</dt>
        <dd>{view.trace_spans ?? '—'}</dd>
      </dl>

      {view.warnings.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>data caveats</h3>
          {view.warnings.map((warning) => (
            <div className="callout warn" key={warning} style={{ fontSize: 12 }}>{warning}</div>
          ))}
        </>
      )}
    </div>
  )
}

function RawPanel({ detail }: { detail: AttemptDetail }) {
  const { view, raw, campaign } = detail
  return (
    <div className="inspector">
      <h3>raw payloads</h3>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {(['result', 'trace', 'activity', 'verdict'] as const).map((kind) => (
          <a
            key={kind}
            className="toggle"
            href={api.rawUrl(campaign.id, view.scenario, view.round, kind)}
            target="_blank"
            rel="noreferrer"
            style={{ opacity: raw[kind] ? 1 : 0.4, pointerEvents: raw[kind] ? 'auto' : 'none' }}
          >
            {kind}.json
          </a>
        ))}
      </div>
      <h3 style={{ marginTop: 16 }}>identifiers</h3>
      <dl className="kv">
        <dt>scenario</dt>
        <dd className="mono" style={{ fontSize: 11.5 }}>{view.scenario}</dd>
        <dt>result id</dt>
        <dd className="mono" style={{ fontSize: 11.5 }}>{view.result_id || '—'}</dd>
        <dt>communication</dt>
        <dd className="mono" style={{ fontSize: 11.5 }}>{view.communication_id || '—'}</dd>
        <dt>snapshot</dt>
        <dd className="mono" style={{ fontSize: 11.5 }}>{view.snapshot_id || '—'}</dd>
        <dt>created</dt>
        <dd>{view.created_at || '—'}</dd>
      </dl>
      {view.applied_mocks.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>mocks applied at runtime</h3>
          <pre className="block">{JSON.stringify(view.applied_mocks.map((m) => m.tool_name ?? m), null, 1)}</pre>
        </>
      )}
    </div>
  )
}

export default function Attempt({ id, scenario, round }: { id: string; scenario: string; round: number }) {
  const { data, error, loading } = useAsync(() => api.attempt(id, scenario, round), [id, scenario, round])
  const [tab, setTab] = useState<Tab>('verdict')
  const [focus, setFocus] = useState<Focus | null>(null)
  const [openTools, setOpenTools] = useState<Set<string>>(new Set())
  const listRef = useRef<HTMLDivElement>(null)

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
    setOpenTools(new Set())
  }, [scenario, round])

  if (error) return <div className="page"><ErrorBox error={error} /></div>
  if (loading && !data) return <div className="page"><Empty>loading…</Empty></div>
  if (!data) return null

  const { view } = data

  return (
    <div className="attempt">
      <div className="pane left" ref={listRef}>
        <div className="pane-head">
          <span className="faint" style={{ fontSize: 11.5 }}>
            {siblings.length} attempts · <kbd>j</kbd>/<kbd>k</kbd>
          </span>
        </div>
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
              <span
                className={`dot ${
                  sibling.score === null || sibling.score === undefined
                    ? 'none'
                    : sibling.score >= 4
                      ? 'pass'
                      : sibling.score >= 3
                        ? 'warn'
                        : 'fail'
                }`}
              />
              <span className="name">{sibling.short}</span>
              <span className="r">r{sibling.round}</span>
              {sibling.score !== null && sibling.score !== undefined && (
                <span className="r" style={{ marginLeft: 'auto' }}>{sibling.score.toFixed(1)}</span>
              )}
              {mismatch && <span className="mismatch">≠</span>}
            </a>
          )
        })}
      </div>

      <div className="pane center">
        <div className="pane-head">
          <Chip mono>{view.short} · round {view.round}</Chip>
          {data.verdict && <Score value={data.verdict.score} />}
          <Verdict passed={view.official_passed} label="official" />
          {view.agent_model && <Chip mono>{view.agent_model}</Chip>}
          <span className="spacer" />
          {focus && (
            <button className="icon-button" onClick={() => setFocus(null)}>
              clear highlight
            </button>
          )}
        </div>
        <Conversation
          turns={view.turns}
          focus={focus}
          openTools={openTools}
          toggleTool={(key) =>
            setOpenTools((current) => {
              const next = new Set(current)
              if (next.has(key)) next.delete(key)
              else next.add(key)
              return next
            })
          }
        />
      </div>

      <div className="pane right">
        <div className="tabs">
          {(['verdict', 'prompt', 'llm', 'raw'] as Tab[]).map((option) => (
            <button key={option} className="tab" aria-selected={tab === option} onClick={() => setTab(option)}>
              {option}
            </button>
          ))}
        </div>
        {tab === 'verdict' && <VerdictPanel detail={data} onFocus={setFocus} />}
        {tab === 'prompt' && <PromptPanel detail={data} />}
        {tab === 'llm' && <LlmPanel detail={data} />}
        {tab === 'raw' && <RawPanel detail={data} />}
      </div>
    </div>
  )
}
