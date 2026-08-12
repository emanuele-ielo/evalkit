import { api, useAsync } from '../api'
import { Chip, Empty, ErrorBox, Score, ago, num } from '../components/bits'
import type { CampaignSummary } from '../types'

function ScoreLine({ campaign }: { campaign: CampaignSummary }) {
  const report = campaign.report
  if (!report || report.attempts_judged === 0) {
    return (
      <div className="score">
        <span className="big faint">—</span>
        <span className="of">not judged yet</span>
      </div>
    )
  }
  return (
    <div className="score">
      <Score value={report.mean_score} size="lg" />
      <span className="of">
        mean over {report.attempts_judged} attempts · {report.our_majority_pass}/{report.scenarios_total} above threshold
      </span>
    </div>
  )
}

export default function Campaigns() {
  const { data, error, loading, reload } = useAsync(() => api.campaigns(), [])

  return (
    <div className="page">
      <div className="section-head">
        <h1>Campaigns</h1>
        <span className="spacer" />
        <button className="icon-button" onClick={reload}>
          refresh
        </button>
      </div>
      <p className="subtle">
        Every eval run collected by evalkit: imported history and live runs, judged with our rubric and compared with
        the platform judge on the same attempts.
      </p>

      {error && <ErrorBox error={error} />}
      {loading && !data && <Empty>loading…</Empty>}
      {data && data.length === 0 && (
        <Empty>
          No campaigns yet. Import what you already have:
          <br />
          <code className="mono">evalkit import ~/GIT/wonderful/Vera/fase5e/attempts --agent vera</code>
        </Empty>
      )}

      <div className="campaign-grid">
        {data?.map((campaign) => {
          const running = (campaign.status_counts.running ?? 0) > 0
          return (
            <a key={campaign.id} className="campaign-card" href={`#/c/${encodeURIComponent(campaign.id)}`}>
              <div className="head">
                <div>
                  <div className="label">{campaign.label}</div>
                  <div className="id">{campaign.id}</div>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {running && <span className="pulse" title="attempts in flight" />}
                  <Chip tone={campaign.kind === 'live' ? 'accent' : 'default'}>{campaign.kind}</Chip>
                </div>
              </div>

              <ScoreLine campaign={campaign} />

              <div className="faint" style={{ fontSize: 12 }}>
                {num(campaign.attempts)} attempts · {campaign.scenarios} scenarios × {campaign.rounds} rounds ·{' '}
                {campaign.status_counts.judged ?? 0} judged
              </div>

              <div className="meta">
                {campaign.agent && <Chip mono>{campaign.agent}</Chip>}
                {campaign.agent_model && <Chip mono>{campaign.agent_model}</Chip>}
                {campaign.judge && (
                  <Chip mono title={`rubric ${campaign.judge.rubric_version}`}>
                    judge {campaign.judge.model} ×{campaign.judge.votes}
                  </Chip>
                )}
                <Chip>{ago(campaign.updated_at || campaign.created_at)}</Chip>
              </div>
            </a>
          )
        })}
      </div>
    </div>
  )
}
