import { useEffect, useMemo, useState } from 'react'
import { api, useAsync, useCampaignEvents } from '../api'
import { Bar, Chip, Empty, ErrorBox, Score, Stat, ago, num, tokenLine } from '../components/bits'
import type { MatrixCell, MatrixRow } from '../types'

type Filter = 'all' | 'failing' | 'flaky' | 'disagree' | 'unjudged'

function cellClass(cell: MatrixCell | undefined): string {
  if (!cell) return 'cell unjudged'
  if (cell.status === 'running') return 'cell running'
  if (cell.status === 'error') return 'cell error'
  if (cell.score !== null && cell.score !== undefined) {
    const tone = cell.score >= 4 ? 'pass' : cell.score >= 3 ? 'error' : 'fail'
    return `cell scored ${tone}`
  }
  return 'cell unjudged'
}

function cellLabel(cell: MatrixCell | undefined): string {
  if (!cell) return '·'
  if (cell.status === 'running') return '···'
  if (cell.status === 'error') return 'err'
  if (cell.score !== null && cell.score !== undefined) return cell.score.toFixed(1)
  if (cell.official === true) return '(pass)'
  if (cell.official === false) return '(fail)'
  return '·'
}

export default function Campaign({ id }: { id: string }) {
  const { events, tick } = useCampaignEvents(id)
  const { data, error, loading, reload } = useAsync(() => api.campaign(id, true), [id])
  const diff = useAsync(() => api.officialDiff(id), [id, tick])
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')

  // A landing attempt (live run or judge pass) refreshes the matrix.
  useEffect(() => {
    if (tick > 0) reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick])

  const rows: MatrixRow[] = useMemo(() => {
    if (!data) return []
    const outcomes = new Map(data.report.scenarios.map((scenario) => [scenario.scenario, scenario]))
    return data.matrix.filter((row) => {
      if (query && !row.short.includes(query) && !row.scenario.includes(query)) return false
      const outcome = outcomes.get(row.scenario)
      const cells = Object.values(row.cells)
      switch (filter) {
        case 'failing':
          return outcome ? outcome.our_majority === false : cells.some((cell) => cell.ours === false)
        case 'flaky':
          return outcome?.stability === 'flaky'
        case 'disagree':
          return cells.some((cell) => cell.ours !== null && cell.official !== null && cell.ours !== cell.official)
        case 'unjudged':
          return cells.some((cell) => cell.ours === null)
        default:
          return true
      }
    })
  }, [data, filter, query])

  if (error) return <div className="page"><ErrorBox error={error} /></div>
  if (loading && !data) return <div className="page"><Empty>loading…</Empty></div>
  if (!data) return null

  const { report, manifest, rounds } = data
  const outcomes = new Map(report.scenarios.map((scenario) => [scenario.scenario, scenario]))
  const running = manifest ? Object.values(data.matrix).some((row) => Object.values(row.cells).some((cell) => cell.status === 'running')) : false

  return (
    <div className="page">
      <div className="section-head">
        <div>
          <h1>{manifest.label}</h1>
          <div className="faint mono" style={{ fontSize: 12 }}>
            {manifest.id} · {manifest.kind} · updated {ago(manifest.updated_at || manifest.created_at)}
          </div>
        </div>
        <span className="spacer" />
        {running && <Chip tone="accent"><span className="pulse" /> live</Chip>}
        <button className="icon-button" onClick={reload}>refresh</button>
      </div>

      <div className="meta" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
        {manifest.target && <Chip mono>{manifest.target.slug}</Chip>}
        {manifest.agent_model && <Chip mono>agent {manifest.agent_model}</Chip>}
        {manifest.agent_commit_sha && <Chip mono>commit {manifest.agent_commit_sha.slice(0, 7)}</Chip>}
        {manifest.snapshot_id && <Chip mono title="agent_repo_snapshot_id">snapshot {manifest.snapshot_id.slice(0, 8)}</Chip>}
        {manifest.batch && <Chip mono>batch {manifest.batch}</Chip>}
        {report.judge && (
          <Chip mono>
            judge {report.judge.model} ×{report.judge.votes} · rubric {report.judge.rubric_version} · effort{' '}
            {report.judge.reasoning_effort}
          </Chip>
        )}
      </div>

      <div className="stat-grid">
        <Stat
          k="score (mean of attempts)"
          v={report.mean_score ? report.mean_score.toFixed(2) : '—'}
          of={5}
          hint="mean of the four criteria, median across votes"
        />
        <Stat
          k="above threshold (ours)"
          v={report.our_majority_pass}
          of={report.scenarios_total}
          delta={{ value: report.our_majority_pass - report.official_majority_pass }}
          hint={`scenarios scoring >= ${report.judge?.pass_threshold ?? 4} in more than half their rounds`}
        />
        <Stat k="any round" v={report.our_any_pass} of={report.scenarios_total} delta={{ value: report.our_any_pass - report.official_any_pass }} />
        <Stat k="every round" v={report.our_all_pass} of={report.scenarios_total} delta={{ value: report.our_all_pass - report.official_all_pass }} />
        <Stat k="attempts passed" v={report.our_pass_attempts} of={report.attempts_judged} hint="individual attempts, ours" />
        <Stat k="judged" v={report.attempts_judged} of={report.attempts_total} />
        <Stat
          k="judge agreement"
          v={diff.data?.agreement_rate != null ? `${Math.round(diff.data.agreement_rate * 100)}%` : '—'}
          hint="how often our verdict matches the platform judge on the same attempt"
        />
      </div>

      {report.notes.length > 0 && (
        <div className="section">
          {report.notes.map((note) => (
            <div key={note} className="callout warn">
              {note}
            </div>
          ))}
        </div>
      )}

      <div className="card-row section" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
        <div className="card">
          <h3>criteria — mean score out of 5</h3>
          {report.criteria.length === 0 && <div className="faint">nothing judged yet</div>}
          {report.criteria.map((criterion) => (
            <div key={criterion.name} className="bar-row">
              <div className="name">{criterion.name}</div>
              <div className="bar-track">
                <div
                  className={`bar-fill${criterion.mean_score >= 4 ? ' pass' : ''}`}
                  style={{ width: `${(criterion.mean_score / 5) * 100}%` }}
                />
              </div>
              <div className="val">{criterion.mean_score.toFixed(2)}/5</div>
            </div>
          ))}
          {Object.keys(report.score_distribution).length > 0 && (
            <div style={{ marginTop: 12 }}>
              <h3>attempts by score</h3>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {Object.entries(report.score_distribution).map(([score, count]) => (
                  <Chip key={score} tone={Number(score) >= 4 ? 'pass' : Number(score) >= 3 ? 'warn' : 'fail'}>
                    {score}★ · {count}
                  </Chip>
                ))}
              </div>
            </div>
          )}
          {Object.keys(report.coverage).length > 0 && (
            <div style={{ marginTop: 12 }}>
              <h3>reference-fact coverage</h3>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {Object.entries(report.coverage).map(([key, value]) => (
                  <Chip key={key} tone={key === 'FULL' ? 'pass' : key === 'MISS' ? 'fail' : 'warn'}>
                    {key} {value}
                  </Chip>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <h3>failure taxonomy</h3>
          {Object.keys(report.taxonomy).length === 0 && <div className="faint">nothing judged yet</div>}
          {Object.entries(report.taxonomy).slice(0, 10).map(([tag, count]) => (
            <Bar key={tag} name={tag} value={count} total={report.attempts_judged || 1} />
          ))}
          {Object.keys(report.deterministic_failures).length > 0 && (
            <div style={{ marginTop: 12 }}>
              <h3>deterministic checks failing</h3>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {Object.entries(report.deterministic_failures).map(([name, count]) => (
                  <Chip key={name} tone="warn" mono>
                    {name} {count}
                  </Chip>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <h3>cost & data coverage</h3>
          <dl className="kv">
            <dt>judge tokens</dt>
            <dd className="mono">{tokenLine(report.tokens)}</dd>
            <dt>agent tokens</dt>
            <dd className="mono">{tokenLine(report.agent_tokens)}</dd>
            <dt>traces</dt>
            <dd>
              {report.trace_coverage}/{report.attempts_collected}
              {report.trace_coverage < report.attempts_collected && (
                <span className="faint"> · expired ones have no system prompt</span>
              )}
            </dd>
            <dt>activities</dt>
            <dd>
              {report.activity_coverage}/{report.attempts_collected}
            </dd>
            <dt>per round</dt>
            <dd className="mono">
              {Object.entries(report.per_round_pass).map(([round, passes]) => `r${round}: ${passes}`).join(' · ') || '—'}
            </dd>
          </dl>
          {events.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <h3>live feed</h3>
              <div className="feed">
                {events.slice(-40).reverse().map((event, index) => (
                  <div key={index}>
                    <span className="faint">{event.ts?.slice(11, 19)} </span>
                    {event.type}
                    {event.short ? ` ${event.short} r${event.round}` : ''}
                    {event.passed !== undefined && (
                      <span className={event.passed ? 'ok' : 'no'}> {event.passed ? 'pass' : 'fail'}</span>
                    )}
                    {event.our_passed !== undefined && event.our_passed !== null && (
                      <span className={event.our_passed ? 'ok' : 'no'}> {event.our_passed ? 'pass' : 'fail'}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Scenarios × rounds</h2>
          <span className="spacer" />
          <span className="faint" style={{ fontSize: 12 }}>
            {rows.length} of {data.matrix.length} shown
          </span>
        </div>

        <div className="filters">
          {(['all', 'failing', 'flaky', 'disagree', 'unjudged'] as Filter[]).map((option) => (
            <button
              key={option}
              className="toggle"
              aria-pressed={filter === option}
              onClick={() => setFilter(option)}
            >
              {option}
            </button>
          ))}
          <input
            className="search"
            placeholder="filter scenarios…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        <div className="matrix-wrap">
          <table className="matrix">
            <thead>
              <tr>
                <th>scenario</th>
                {rounds.map((round) => (
                  <th key={round}>r{round}</th>
                ))}
                <th>score</th>
                <th>official</th>
                <th>failure tags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const outcome = outcomes.get(row.scenario)
                return (
                  <tr key={row.scenario}>
                    <td className="scenario" title={row.scenario}>
                      {row.short}
                    </td>
                    {rounds.map((round) => {
                      const cell = row.cells[String(round)]
                      const mismatch = cell && cell.ours !== null && cell.official !== null && cell.ours !== cell.official
                      return (
                        <td key={round}>
                          <a
                            className={cellClass(cell)}
                            href={`#/c/${encodeURIComponent(id)}/a/${encodeURIComponent(row.scenario)}/${round}`}
                            title={
                              cell
                                ? `score ${cell.score ?? '—'} · official ${
                                    cell.official === null ? '—' : cell.official ? 'pass' : 'fail'
                                  }${cell.error ? ` · ${cell.error}` : ''}`
                                : 'not collected'
                            }
                          >
                            {cellLabel(cell)}
                            {mismatch && <span className="flag">≠</span>}
                          </a>
                        </td>
                      )
                    })}
                    <td className="nowrap">
                      {outcome?.score !== null && outcome?.score !== undefined ? <Score value={outcome.score} /> : '—'}
                    </td>
                    <td className="mono nowrap faint">
                      {outcome ? `${outcome.official_passes}/${outcome.rounds_total}` : '—'}
                    </td>
                    <td className="tags">{outcome?.taxonomy.slice(0, 3).join(', ')}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="legend">
          <span><span className="swatch" style={{ background: 'var(--pass)' }} />score ≥ 4</span>
          <span><span className="swatch" style={{ background: 'var(--warn)' }} />score 3–4</span>
          <span><span className="swatch" style={{ background: 'var(--fail)' }} />score &lt; 3</span>
          <span><span className="swatch" style={{ background: 'var(--surface-3)' }} />not judged — <span className="mono">(pass)</span>/<span className="mono">(fail)</span> is the platform's</span>
          <span>≠ we disagree with the platform judge</span>
        </div>
      </div>

      {diff.data && diff.data.disagreements.length > 0 && (
        <div className="section">
          <h2>Where we disagree with the platform judge</h2>
          <p className="subtle">
            {num(diff.data.counts.ours_pass_official_fail ?? 0)} we pass / it fails ·{' '}
            {num(diff.data.counts.ours_fail_official_pass ?? 0)} we fail / it passes — same attempts, two rubrics.
          </p>
          {diff.data.disagreements.slice(0, 24).map((row) => (
            <div key={`${row.scenario}-${row.round}`} className="card" style={{ marginTop: 10 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                <a className="mono" href={`#/c/${encodeURIComponent(id)}/a/${encodeURIComponent(row.scenario)}/${row.round}`}>
                  {row.short} r{row.round}
                </a>
                <Chip tone={row.agreement === 'ours_pass_official_fail' ? 'pass' : 'fail'}>
                  {row.agreement === 'ours_pass_official_fail' ? 'ours pass · official fail' : 'ours fail · official pass'}
                </Chip>
                {row.taxonomy.slice(0, 3).map((tag) => (
                  <Chip key={tag} mono>{tag}</Chip>
                ))}
              </div>
              <dl className="kv">
                <dt>ours</dt>
                <dd>{row.ours || '—'}</dd>
                <dt>official</dt>
                <dd className="subtle">{row.official || '—'}</dd>
              </dl>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
