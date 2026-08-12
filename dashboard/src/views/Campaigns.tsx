import { api, useAsync } from '../api'
import { Empty, ErrorBox, Pill, ago, hasScores, num } from '../components/bits'
import type { CampaignSummary } from '../types'

function Figure({ campaign }: { campaign: CampaignSummary }) {
  const report = campaign.report
  if (!report || report.attempts_judged === 0) {
    return (
      <div className="figure">
        <span className="hero-num" style={{ color: 'var(--fg-5)' }}>
          —
        </span>
        <span className="faint" style={{ fontSize: 11.5 }}>
          collected, not judged
        </span>
      </div>
    )
  }
  if (!hasScores(report)) {
    return (
      <div className="figure">
        <span className="hero-num" style={{ color: 'var(--fg-5)' }}>
          —
        </span>
        <span className="faint" style={{ fontSize: 11.5 }}>
          rubric {campaign.judge?.rubric_version ?? 'v1'} — pass/fail, no score
          <br />
          {report.our_majority_pass}/{report.scenarios_total} scenarios pass
        </span>
      </div>
    )
  }
  return (
    <div className="figure">
      <span className="hero-num">
        {report.mean_score.toFixed(2)}
        <small> / 5</small>
      </span>
      <span className="faint" style={{ fontSize: 11.5 }}>
        {report.attempts_judged} attempts judged
        <br />
        {report.our_majority_pass}/{report.scenarios_total} scenarios above threshold
      </span>
    </div>
  )
}

export default function Campaigns() {
  const { data, error, loading, reload } = useAsync(() => api.campaigns(), [])

  return (
    <div className="page">
      <div className="run-head">
        <div className="titles">
          <h1>Campaigns</h1>
          <p className="lede">
            Every eval run evalkit has collected — imported history and live runs, scored 1–5 with our rubric and set
            against the platform judge on the very same attempts.
          </p>
        </div>
        <span className="spacer" />
        <button className="button" onClick={reload}>
          Refresh
        </button>
      </div>

      {error && <ErrorBox error={error} />}
      {loading && !data && <Empty>loading…</Empty>}
      {data && data.length === 0 && (
        <Empty>
          Nothing collected yet. Import what you already have:
          <br />
          <code className="cmd">evalkit import ~/GIT/wonderful/Vera/fase5e/attempts --agent vera</code>
        </Empty>
      )}

      <div className="campaign-grid">
        {data?.map((campaign) => {
          const running = (campaign.status_counts.running ?? 0) > 0
          const unjudged = campaign.attempts - (campaign.status_counts.judged ?? 0)
          return (
            <a key={campaign.id} className="campaign-card" href={`#/c/${encodeURIComponent(campaign.id)}`}>
              <div className="head">
                <div style={{ minWidth: 0 }}>
                  <div className="label">{campaign.label}</div>
                  <div className="id">{campaign.id}</div>
                </div>
                <span className="spacer" />
                {running && <span className="pulse" title="attempts in flight" />}
                <Pill tone={campaign.kind === 'live' ? 'accent' : 'default'}>{campaign.kind}</Pill>
              </div>

              <Figure campaign={campaign} />

              <div className="faint" style={{ fontSize: 11.5 }}>
                {num(campaign.attempts)} attempts · {campaign.scenarios} scenarios × {campaign.rounds} round
                {campaign.rounds === 1 ? '' : 's'}
                {unjudged > 0 && ` · ${unjudged} not judged`}
              </div>

              <div className="meta">
                {campaign.agent_model && <Pill mono>{campaign.agent_model}</Pill>}
                {campaign.judge && (
                  <Pill mono title={`rubric ${campaign.judge.rubric_version}`}>
                    judge {campaign.judge.model} ×{campaign.judge.votes}
                  </Pill>
                )}
                {campaign.judge && <Pill mono>rubric {campaign.judge.rubric_version}</Pill>}
                <Pill>{ago(campaign.updated_at || campaign.created_at)}</Pill>
              </div>
            </a>
          )
        })}
      </div>
    </div>
  )
}
