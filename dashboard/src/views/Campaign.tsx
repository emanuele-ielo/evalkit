import { useEffect, useMemo, useState } from 'react'
import { api, useAsync, useCampaignEvents } from '../api'
import { Empty, ErrorBox, MetricHint, Pill, Score, Tag, ago, hasScores, num, scoreTone, tokenLine } from '../components/bits'
import type { MatrixCell, MatrixRow, ScenarioOutcome } from '../types'

type Filter = 'all' | 'failing' | 'flaky' | 'disagree' | 'unjudged'
type SortKey = 'scenario' | 'score' | 'official'

const FILTERS: Filter[] = ['all', 'failing', 'flaky', 'disagree', 'unjudged']

function RoundPill({ round, cell }: { round: number; cell: MatrixCell | undefined }) {
  if (!cell) return <span className="rpill">r{round}</span>
  if (cell.status === 'error') {
    return (
      <span className="rpill err" title={cell.error ?? 'error'}>
        r{round}
      </span>
    )
  }
  if (cell.status === 'running') {
    return (
      <span className="rpill" title="in flight">
        r{round}···
      </span>
    )
  }
  const tone = scoreTone(cell.score)
  const official = cell.official === null ? '—' : cell.official ? 'pass' : 'fail'
  return (
    <span
      className={`rpill ${tone === 'none' ? '' : tone}`}
      title={`${cell.score === null ? 'not judged' : `${cell.score.toFixed(2)}/5`} · platform ${official}`}
    >
      r{round}
    </span>
  )
}

/** Official verdict against ours, for one scenario across its rounds. */
function Agreement({ outcome }: { outcome: ScenarioOutcome | undefined }) {
  if (!outcome || outcome.our_majority === null) {
    return <span className="agree faint" title="This scenario does not have an Evalkit verdict yet">not judged</span>
  }
  if (outcome.stability === 'flaky') {
    return (
      <span className="agree" style={{ color: 'var(--warn)' }} title="passes in some rounds and not others">
        flaky
      </span>
    )
  }
  const ours = outcome.our_majority
  const official = outcome.official_majority
  if (official === null) {
    return (
      <span className="agree faint" title="Platform verdict unavailable · Evalkit verdict shown second">
        — / {ours ? 'pass' : 'fail'}
      </span>
    )
  }
  const disagree = ours !== official
  const color = disagree ? 'var(--info)' : ours ? 'var(--good)' : 'var(--bad)'
  return (
    <span
      className="agree"
      style={{ color }}
      title={
        disagree
          ? 'The platform judge and Evalkit reached different pass/fail decisions'
          : 'The platform judge and Evalkit reached the same pass/fail decision'
      }
    >
      {official ? 'pass' : 'fail'} / {ours ? 'pass' : 'fail'}
      {disagree ? ' ≠' : ''}
    </span>
  )
}

export default function Campaign({ id }: { id: string }) {
  const { events, tick } = useCampaignEvents(id)
  const { data, error, loading, reload } = useAsync(() => api.campaign(id, true), [id])
  const diff = useAsync(() => api.officialDiff(id), [id, tick])
  const [filter, setFilter] = useState<Filter>('all')
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'scenario', dir: 'asc' })
  const [internals, setInternals] = useState(false)

  // A landing attempt (live run or judge pass) refreshes the table.
  useEffect(() => {
    if (tick > 0) reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick])

  const outcomes = useMemo(
    () => new Map((data?.report.scenarios ?? []).map((scenario) => [scenario.scenario, scenario])),
    [data],
  )

  const rows: MatrixRow[] = useMemo(() => {
    if (!data) return []
    const kept = data.matrix.filter((row) => {
      if (query && !row.short.toLowerCase().includes(query.toLowerCase()) && !row.scenario.toLowerCase().includes(query.toLowerCase())) {
        return false
      }
      const outcome = outcomes.get(row.scenario)
      if (tagFilter && !(outcome?.taxonomy ?? []).includes(tagFilter)) return false
      const cells = Object.values(row.cells)
      switch (filter) {
        case 'failing':
          return outcome ? outcome.our_majority === false : cells.some((cell) => cell.ours === false)
        case 'flaky':
          return outcome?.stability === 'flaky'
        case 'disagree':
          return cells.some((cell) => cell.ours !== null && cell.official !== null && cell.ours !== cell.official)
        case 'unjudged':
          return cells.some((cell) => cell.score === null)
        default:
          return true
      }
    })
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...kept].sort((a, b) => {
      if (sort.key === 'scenario') return sign * a.short.localeCompare(b.short)
      if (sort.key === 'score') {
        const left = outcomes.get(a.scenario)?.score
        const right = outcomes.get(b.scenario)?.score
        if (left === null || left === undefined) return 1
        if (right === null || right === undefined) return -1
        return sign * (left - right)
      }
      const rank = (row: MatrixRow) => {
        const outcome = outcomes.get(row.scenario)
        if (!outcome || outcome.our_majority === null) return 4
        if (outcome.stability === 'flaky') return 2
        if (outcome.official_majority !== null && outcome.our_majority !== outcome.official_majority) return 1
        return outcome.our_majority ? 3 : 0
      }
      return sign * (rank(a) - rank(b))
    })
  }, [data, filter, tagFilter, query, sort, outcomes])

  if (error) return <div className="page"><ErrorBox error={error} /></div>
  if (loading && !data) return <div className="page"><Empty>loading…</Empty></div>
  if (!data) return null

  const { report, manifest, rounds } = data
  const scored = hasScores(report)
  const running = data.matrix.some((row) => Object.values(row.cells).some((cell) => cell.status === 'running'))
  const unjudged = report.attempts_collected - report.attempts_judged
  const judgedRoundsMax = Math.max(0, ...report.scenarios.map((scenario) => scenario.judged_rounds))
  const flaky = report.scenarios.filter((scenario) => scenario.stability === 'flaky').length
  const taxonomy = Object.entries(report.taxonomy).sort((a, b) => b[1] - a[1])
  const taxonomyTop = taxonomy.length > 0 ? taxonomy[0][1] : 1

  const sortHeader = (key: SortKey, label: string, help?: string) => (
    <button
      onClick={() => setSort((current) => ({ key, dir: current.key === key && current.dir === 'asc' ? 'desc' : 'asc' }))}
      title={help ? `${help} Click to sort by this column.` : `Sort by ${label}`}
    >
      <span className={help ? 'metric-text' : undefined}>{label}</span>
      {sort.key === key && <span className="arrow">{sort.dir === 'asc' ? ' ↑' : ' ↓'}</span>}
    </button>
  )

  return (
    <div className="page">
      <div className="run-head">
        <div className="titles">
          <h1>{manifest.label}</h1>
          <div className="meta">
            {manifest.agent_model && <Pill mono>{manifest.agent_model}</Pill>}
            {manifest.batch && <Pill mono>batch {manifest.batch}</Pill>}
            {report.judge && (
              <Pill mono>
                judge {report.judge.model} ×{report.judge.votes} · rubric {report.judge.rubric_version}
              </Pill>
            )}
            {manifest.snapshot_id && <Pill mono>snapshot {manifest.snapshot_id.slice(0, 8)}</Pill>}
            {manifest.agent_commit_sha && <Pill mono>commit {manifest.agent_commit_sha.slice(0, 7)}</Pill>}
          </div>
        </div>
        <span className="spacer" />
        <Pill tone={running ? 'good' : 'default'}>
          {running && <span className="pulse" />}
          {running ? 'live · ' : ''}
          updated {ago(manifest.updated_at || manifest.created_at)}
        </Pill>
        <button className="button" onClick={reload}>
          Refresh
        </button>
      </div>

      <div className="stat-row">
        <div className="stat">
          <span className="k">
            <MetricHint label="Mean score" align="left">
              Average across the rubric criteria and judged attempts, from 1 (poor) to 5 (excellent).
            </MetricHint>
          </span>
          <span className="hero-num">
            {scored ? report.mean_score.toFixed(2) : '—'}
            {scored && <small> / 5</small>}
          </span>
          <span className="foot">
            {scored
              ? `mean of ${report.attempts_judged} judged attempts`
              : report.attempts_judged > 0
                ? `rubric ${report.judge?.rubric_version ?? 'v1'} — pass/fail, not scored`
                : 'nothing judged yet'}
          </span>
        </div>

        <div className="stat">
          <span className="k">
            <MetricHint label="Pass — every round" align="left">
              {scored
                ? `Scenarios whose every judged round passed its saved mean, criterion-floor and mechanical guardrails.`
                : 'Scenarios the judge marked as passed in every judged round.'}
            </MetricHint>
          </span>
          <span className="hero-num">
            {report.our_all_pass}
            <small> / {report.scenarios_total}</small>
          </span>
          <span
            className={`foot${
              report.attempts_judged === 0 || report.our_all_pass === report.official_all_pass
                ? ''
                : report.our_all_pass > report.official_all_pass
                  ? ' up'
                  : ' down'
            }`}
          >
            {report.attempts_judged === 0
              ? `the platform judge passed ${report.official_all_pass}`
              : report.our_all_pass === report.official_all_pass
                ? 'same as the platform judge'
                : `${report.our_all_pass > report.official_all_pass ? '+' : ''}${report.our_all_pass - report.official_all_pass} vs platform judge`}
          </span>
        </div>

        <div className="stat">
          <span className="k">
            <MetricHint label="Flaky" align="left">
              Scenarios that pass in some rounds and fail in others. Fewer is better.
            </MetricHint>
          </span>
          <span className="hero-num">
            {judgedRoundsMax > 1 ? flaky : '—'}
            {judgedRoundsMax > 1 && <small> / {report.scenarios_total}</small>}
          </span>
          <span className="foot">
            {judgedRoundsMax > 1
              ? 'score varies across rounds'
              : judgedRoundsMax === 1
                ? 'one round judged — flakiness needs more'
                : 'nothing judged yet'}
          </span>
        </div>

        <div className="stat">
          <span className="k">
            <MetricHint label="Judge agreement" align="right">
              Percentage of attempts where Evalkit and the platform judge reached the same pass/fail decision.
            </MetricHint>
          </span>
          <span className="hero-num">
            {diff.data?.agreement_rate != null ? Math.round(diff.data.agreement_rate * 100) : '—'}
            {diff.data?.agreement_rate != null && <small>%</small>}
          </span>
          <span className="foot">ours vs platform, {report.attempts_judged} judged</span>
        </div>
      </div>

      {unjudged > 0 && (
        <div className="notice">
          <span className="dot" />
          {num(unjudged)} collected {unjudged === 1 ? 'attempt' : 'attempts'} not judged yet — every number here covers
          the {report.attempts_judged} that are.
        </div>
      )}
      {!scored && report.attempts_judged > 0 && (
        <div className="notice">
          <span className="dot" />
          <span>
            Judged with rubric {report.judge?.rubric_version ?? 'v1'}, which was pass/fail — a pass cannot be turned
            into a score without inventing one. Re-judge to put this run on the 1–5 scale:{' '}
            <code className="cmd">evalkit judge {manifest.id} --force</code>
          </span>
        </div>
      )}
      {report.notes
        .filter((note) => !(unjudged > 0 && /not judged yet/i.test(note)))
        .map((note) => (
        <div className="notice" key={note}>
          <span className="dot" />
          {note}
        </div>
      ))}

      <div className="card-row">
        <div className="card">
          <h3>
            <MetricHint
              label={scored ? 'Criteria — mean of 5' : 'Criteria — share of attempts passing'}
              align="left"
            >
              Each criterion is one dimension of the rubric. The bar summarizes that dimension across judged attempts.
            </MetricHint>
          </h3>
          {report.criteria.length === 0 && <span className="faint">nothing judged yet</span>}
          {report.criteria.map((criterion) => {
            // A pass/fail rubric has no per-criterion score, only a pass rate.
            const rate = criterion.attempts_judged > 0 ? criterion.attempts_passed / criterion.attempts_judged : 0
            const width = scored ? (criterion.mean_score / 5) * 100 : rate * 100
            const tone = scored
              ? scoreTone(criterion.mean_score)
              : rate >= 0.75
                ? 'good'
                : rate >= 0.4
                  ? 'warn'
                  : 'bad'
            return (
              <div className="bar-row" key={criterion.name}>
                <span className="name">
                  <MetricHint label={criterion.name} align="left">
                    {scored
                      ? `Average score for this rubric criterion across ${criterion.attempts_judged} judged attempts.`
                      : `${criterion.attempts_passed} of ${criterion.attempts_judged} judged attempts passed this criterion.`}
                  </MetricHint>
                </span>
                <span className="bar-track">
                  <span className={`bar-fill ${tone === 'none' ? '' : tone}`} style={{ width: `${width}%` }} />
                </span>
                <span className="val">
                  {scored ? criterion.mean_score.toFixed(2) : `${Math.round(rate * 100)}%`}
                </span>
              </div>
            )
          })}
          {Object.keys(report.score_distribution).length > 0 && (
            <div className="dist">
              {Object.entries(report.score_distribution)
                .sort((a, b) => Number(a[0]) - Number(b[0]))
                .map(([score, count]) => (
                  <Pill
                    key={score}
                    tone={Number(score) >= 4 ? 'good' : Number(score) >= 3 ? 'warn' : 'bad'}
                    title={`${count} judged attempts received a score of ${score} out of 5`}
                  >
                    {score}★ · {count}
                  </Pill>
                ))}
            </div>
          )}
          {Object.keys(report.coverage).length > 0 && (
            <div className="dist">
              {Object.entries(report.coverage).map(([key, value]) => (
                <Pill
                  key={key}
                  tone={key === 'FULL' ? 'good' : key === 'MISS' ? 'bad' : 'warn'}
                  title={`${value} judged attempts had ${key.toLowerCase()} coverage of the reference facts required for a correct answer`}
                >
                  reference facts {key.toLowerCase()} · {value}
                </Pill>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head" onClick={() => setInternals((open) => !open)}>
            <h3>Run internals</h3>
            <span className="faint" style={{ fontSize: 11 }}>
              tokens · coverage · feed
            </span>
            <span className="spacer" />
            <span className="glyph">{internals ? '−' : '+'}</span>
          </div>
          {internals ? (
            <>
              <dl className="kv">
                <dt>
                  <MetricHint label="judge tokens" align="left">
                    Text units consumed by the AI judge while evaluating this campaign. More tokens generally mean more cost.
                  </MetricHint>
                </dt>
                <dd className="num">{tokenLine(report.tokens)}</dd>
                <dt>
                  <MetricHint label="agent tokens" align="left">
                    Text units consumed by the tested agent while producing its answers.
                  </MetricHint>
                </dt>
                <dd className="num">{tokenLine(report.agent_tokens)}</dd>
                <dt>
                  <MetricHint label="traces" align="left">
                    Attempts with a detailed execution trace available, out of all collected attempts.
                  </MetricHint>
                </dt>
                <dd className="num">
                  {report.trace_coverage}/{report.attempts_collected}
                  {report.trace_coverage < report.attempts_collected && ' · expired ones carry no system prompt'}
                </dd>
                <dt>
                  <MetricHint label="activities" align="left">
                    Attempts with a platform activity record available, out of all collected attempts.
                  </MetricHint>
                </dt>
                <dd className="num">
                  {report.activity_coverage}/{report.attempts_collected}
                </dd>
                <dt>
                  <MetricHint label="per round" align="left">
                    Number of scenarios that passed in each repeated evaluation round.
                  </MetricHint>
                </dt>
                <dd className="num">
                  {Object.entries(report.per_round_pass)
                    .map(([round, passes]) => `r${round}: ${passes}`)
                    .join(' · ') || '—'}
                </dd>
                {Object.keys(report.deterministic_failures).length > 0 && (
                  <>
                    <dt>
                      <MetricHint label="checks failing" align="left">
                        Mechanical rule checks that failed, with the number of affected attempts.
                      </MetricHint>
                    </dt>
                    <dd>
                      <div className="dist">
                        {Object.entries(report.deterministic_failures).map(([name, count]) => (
                          <Tag key={name}>
                            {name} {count}
                          </Tag>
                        ))}
                      </div>
                    </dd>
                  </>
                )}
              </dl>
              {events.length > 0 && (
                <div className="feed">
                  {events
                    .slice(-40)
                    .reverse()
                    .map((event, index) => (
                      <div key={index}>
                        <span className="t">{event.ts?.slice(11, 19)} </span>
                        {event.type}
                        {event.short ? ` ${event.short} r${event.round}` : ''}
                        {event.our_passed !== undefined && event.our_passed !== null && (
                          <span className={event.our_passed ? 'ok' : 'no'}> {event.our_passed ? 'pass' : 'fail'}</span>
                        )}
                      </div>
                    ))}
                </div>
              )}
            </>
          ) : (
            <span className="faint" style={{ fontSize: 12, textWrap: 'pretty' }}>
              {report.trace_coverage === report.attempts_collected
                ? `All ${report.attempts_collected} traces captured.`
                : `${report.trace_coverage} of ${report.attempts_collected} traces captured.`}{' '}
              Token spend, data coverage and the live judge feed live here — out of the way until you need them.
            </span>
          )}
        </div>
      </div>

      {taxonomy.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2>Where it loses points</h2>
            <span className="count">
              {tagFilter ? (
                <>
                  filtering by <span className="mono">{tagFilter}</span> — click again to clear
                </>
              ) : (
                'click a tag to filter the scenarios below'
              )}
            </span>
          </div>
          <div className="card">
            <div className="taxo">
              {taxonomy.slice(0, 12).map(([tag, count]) => (
                <button
                  className="taxo-row"
                  key={tag}
                  aria-pressed={tagFilter === tag}
                  onClick={() => setTagFilter((current) => (current === tag ? null : tag))}
                  title={`${count} of ${report.attempts_judged} judged attempts`}
                >
                  <span className="name">{tag}</span>
                  <span className="n">
                    {count} · {Math.round((100 * count) / (report.attempts_judged || 1))}%
                  </span>
                  <span className="track">
                    <span className="fill" style={{ width: `${(100 * count) / (taxonomyTop || 1)}%` }} />
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <h2>Scenarios</h2>
          <span className="spacer" />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {FILTERS.map((option) => (
              <button key={option} className="toggle" aria-pressed={filter === option} onClick={() => setFilter(option)}>
                {option}
              </button>
            ))}
          </div>
          <input
            className="search"
            placeholder="filter…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span className="count">
            <MetricHint label={`${rows.length} of ${data.matrix.length} shown`} align="right">
              Scenarios currently visible after applying the selected filters, out of the campaign total.
            </MetricHint>
          </span>
        </div>

        <div className="table">
          <div className="trow thead">
            {sortHeader('scenario', 'Scenario')}
            <span>
              <MetricHint label="Rounds" align="left">
                Repeated executions of the same scenario. Repetition reveals unstable results.
              </MetricHint>
            </span>
            {sortHeader('score', 'Score', 'Average Evalkit score for this scenario across its judged rounds.')}
            <span className="agree">
              {sortHeader(
                'official',
                'Official / ours',
                'The platform judge verdict compared with Evalkit’s verdict for the same scenario.',
              )}
            </span>
            <span>Failure tags</span>
          </div>
          {rows.length === 0 && <Empty>nothing matches this filter</Empty>}
          {rows.map((row) => {
            const outcome = outcomes.get(row.scenario)
            const firstRound = rounds[0] ?? 1
            return (
              <a
                className="trow"
                key={row.scenario}
                href={`#/c/${encodeURIComponent(id)}/a/${encodeURIComponent(row.scenario)}/${firstRound}`}
                title={row.scenario}
              >
                <span className="sid">{row.short}</span>
                <span className="rounds">
                  {rounds.map((round) => (
                    <RoundPill key={round} round={round} cell={row.cells[String(round)]} />
                  ))}
                </span>
                <Score value={outcome?.score ?? null} />
                <Agreement outcome={outcome} />
                <span className="tags">
                  {(outcome?.taxonomy ?? []).slice(0, 3).map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </span>
              </a>
            )
          })}
        </div>

        <div className="legend">
          <span>
            <span className="swatch" style={{ background: 'var(--good)' }} />
            score ≥ 4
          </span>
          <span>
            <span className="swatch" style={{ background: 'var(--warn)' }} />
            3–4
          </span>
          <span>
            <span className="swatch" style={{ background: 'var(--bad)' }} />
            below 3
          </span>
          <span>
            <span className="swatch" style={{ background: 'var(--fg-5)' }} />
            not judged
          </span>
          <span>≠ we disagree with the platform judge</span>
        </div>
      </div>

      {diff.data && diff.data.disagreements.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2>Where we disagree with the platform judge</h2>
          </div>
          <p className="lede">
            {num(diff.data.counts.ours_pass_official_fail ?? 0)} we pass and it fails ·{' '}
            {num(diff.data.counts.ours_fail_official_pass ?? 0)} we fail and it passes — the same attempts, two rubrics.
          </p>
          {diff.data.disagreements.slice(0, 24).map((row) => (
            <div className="card tight" key={`${row.scenario}-${row.round}`}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <a className="mono" href={`#/c/${encodeURIComponent(id)}/a/${encodeURIComponent(row.scenario)}/${row.round}`}>
                  {row.short} r{row.round}
                </a>
                <Pill tone={row.agreement === 'ours_pass_official_fail' ? 'good' : 'bad'}>
                  {row.agreement === 'ours_pass_official_fail' ? 'ours pass · platform fail' : 'ours fail · platform pass'}
                </Pill>
                {row.taxonomy.slice(0, 3).map((tag) => (
                  <Tag key={tag}>{tag}</Tag>
                ))}
              </div>
              <dl className="kv">
                <dt>ours</dt>
                <dd>{row.ours || '—'}</dd>
                <dt>platform</dt>
                <dd>{row.official || '—'}</dd>
              </dl>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
