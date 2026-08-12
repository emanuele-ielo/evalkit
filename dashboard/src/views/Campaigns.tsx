import { api, useAsync } from '../api'
import { Empty, ErrorBox, MetricHint, ago, hasScores, num } from '../components/bits'
import type { CampaignSummary } from '../types'

function MetricLabel({ children, help, align = 'left' }: { children: string; help: string; align?: 'left' | 'right' }) {
  return (
    <span className="metric-label">
      <MetricHint label={children} align={align}>
        {help}
      </MetricHint>
    </span>
  )
}

function CampaignResult({ campaign }: { campaign: CampaignSummary }) {
  const report = campaign.report

  if (!report || report.attempts_judged === 0) {
    return (
      <div className="campaign-result pending">
        <div className="campaign-primary">
          <MetricLabel
            help="Evalkit collected the results, but its judge has not reviewed them yet. A score will appear after judging."
          >
            Evaluation
          </MetricLabel>
          <strong className="result-state">Not judged</strong>
        </div>
        <div className="campaign-metric">
          <strong>{num(campaign.scenarios)}</strong>
          <MetricLabel help="Scenarios are the distinct test cases used to check how the agent behaves.">scenarios</MetricLabel>
        </div>
        <div className="campaign-metric">
          <strong>{num(campaign.attempts)}</strong>
          <MetricLabel
            help="An attempt is one execution of a scenario. Repeating a scenario across rounds creates multiple attempts."
            align="right"
          >
            attempts
          </MetricLabel>
        </div>
      </div>
    )
  }

  if (!hasScores(report)) {
    return (
      <div className="campaign-result">
        <div className="campaign-primary">
          <MetricLabel help="A scenario passes when it succeeds in most of its evaluated rounds.">
            Scenarios passed
          </MetricLabel>
          <strong className="result-fraction">
            {report.our_majority_pass}<small> / {report.scenarios_total}</small>
          </strong>
        </div>
        <div className="campaign-metric">
          <strong>Pass/fail</strong>
          <MetricLabel help="This campaign used an older yes-or-no evaluation, so it has no score from 1 to 5.">
            rubric
          </MetricLabel>
        </div>
        <div className="campaign-metric">
          <strong>{num(report.attempts_judged)}</strong>
          <MetricLabel
            help="The number of individual attempts that Evalkit's judge has reviewed."
            align="right"
          >
            attempts judged
          </MetricLabel>
        </div>
      </div>
    )
  }

  return (
    <div className="campaign-result">
      <div className="campaign-primary">
        <MetricLabel help="The average score across judged attempts, from 1 (poor) to 5 (excellent).">
          Average score
        </MetricLabel>
        <strong className="result-score">
          {report.mean_score.toFixed(2)}<small> / 5</small>
        </strong>
      </div>
      <div className="campaign-metric">
        <strong>
          {report.our_majority_pass}<small> / {report.scenarios_total}</small>
        </strong>
        <MetricLabel help="The first number is how many test cases passed in most rounds; the second is the total tested.">
          scenarios passed
        </MetricLabel>
      </div>
      <div className="campaign-metric">
        <strong>
          {report.attempts_judged}<small> / {campaign.attempts}</small>
        </strong>
        <MetricLabel
          help="The first number has been reviewed by Evalkit's judge; the second is the total number collected."
          align="right"
        >
          attempts judged
        </MetricLabel>
      </div>
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
          <p className="lede">Compare evaluation runs, then open one to inspect its scenarios and attempts.</p>
        </div>
        <span className="spacer" />
        <button
          className="button"
          onClick={reload}
          disabled={loading}
          title="Fetch the latest campaign status and results"
        >
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
          return (
            <article key={campaign.id} className="campaign-card">
              <div className="campaign-card-top">
                <div className="campaign-kind">
                  {running && <span className="pulse" title="Attempts in flight" />}
                  <span>{running ? 'Running' : campaign.kind}</span>
                </div>
                <time dateTime={campaign.updated_at || campaign.created_at}>
                  Updated {ago(campaign.updated_at || campaign.created_at)}
                </time>
              </div>

              <h2 className="campaign-title">
                <a href={`#/c/${encodeURIComponent(campaign.id)}`} aria-label={`Open campaign ${campaign.label}`}>
                  {campaign.label}
                </a>
              </h2>

              <CampaignResult campaign={campaign} />

              {(campaign.agent_model || campaign.judge) && (
                <div className="campaign-config">
                  {campaign.agent_model && (
                    <span>
                      <small>Agent</small>
                      <strong>{campaign.agent_model}</strong>
                    </span>
                  )}
                  {campaign.judge && (
                    <span>
                      <small>Judge</small>
                      <strong>{campaign.judge.model} ×{campaign.judge.votes}</strong>
                      <em>rubric {campaign.judge.rubric_version}</em>
                    </span>
                  )}
                </div>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}
