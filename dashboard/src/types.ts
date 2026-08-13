// Shapes the evalkit API returns. Only the fields the UI actually renders are
// typed; deep raw payloads stay `unknown` and are shown as JSON.

export type Coverage = 'FULL' | 'PARTIAL' | 'MISS'
export type AttemptStatus = 'pending' | 'running' | 'collected' | 'judged' | 'error'

export interface TokenUsage {
  provider: string | null
  model: string | null
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  reasoning_tokens: number
}

export interface JudgeSettings {
  model: string
  votes: number
  reasoning_effort: string
  rubric_version: string
  required_criteria: string[]
  pass_threshold: number
  min_criterion_score: number
}

export interface CampaignSummary {
  id: string
  label: string
  kind: 'imported' | 'live'
  created_at: string
  updated_at: string | null
  agent: string | null
  agent_model: string | null
  snapshot_id: string | null
  rounds: number
  scenarios: number
  attempts: number
  status_counts: Record<string, number>
  judge: JudgeSettings | null
  report: {
    mean_score: number
    our_majority_pass: number
    official_majority_pass: number
    scenarios_total: number
    our_pass_attempts: number
    attempts_judged: number
    generated_at: string
  } | null
}

export interface ScenarioOutcome {
  scenario: string
  short: string
  score: number | null
  rounds_total: number
  our_passes: number
  official_passes: number
  official_rounds: number
  our_majority: boolean | null
  official_majority: boolean | null
  stability: 'stable_pass' | 'stable_fail' | 'flaky' | 'unknown'
  taxonomy: string[]
  judged_rounds: number
}

export interface CampaignReport {
  mean_score: number
  score_distribution: Record<string, number>
  campaign: string
  label: string
  kind: 'imported' | 'live'
  generated_at: string
  agent_model: string | null
  snapshot_id: string | null
  judge: JudgeSettings | null
  scenarios_total: number
  attempts_total: number
  attempts_collected: number
  attempts_judged: number
  our_pass_attempts: number
  our_majority_pass: number
  our_any_pass: number
  our_all_pass: number
  official_pass_attempts: number
  official_attempts_available: number
  official_majority_pass: number
  official_any_pass: number
  official_all_pass: number
  official_scenarios_available: number
  agreement: Record<string, number>
  criteria: { name: string; attempts_judged: number; attempts_passed: number; mean_score: number; distribution: Record<string, number> }[]
  deterministic_failures: Record<string, number>
  taxonomy: Record<string, number>
  coverage: Record<string, number>
  per_round_pass: Record<string, number>
  scenarios: ScenarioOutcome[]
  tokens: TokenUsage
  agent_tokens: TokenUsage
  trace_coverage: number
  activity_coverage: number
  rubric_versions: string[]
  notes: string[]
}

export interface MatrixCell {
  status: AttemptStatus
  score: number | null
  ours: boolean | null
  official: boolean | null
  error: string | null
  trace: boolean | null
  ms: number | null
}

export interface MatrixRow {
  scenario: string
  short: string
  cells: Record<string, MatrixCell>
}

export interface CampaignDetail {
  manifest: {
    id: string
    label: string
    kind: 'imported' | 'live'
    created_at: string
    updated_at: string | null
    agent_model: string | null
    agent_commit_sha: string | null
    snapshot_id: string | null
    batch: string | null
    rounds: number
    concurrency: number | null
    scenarios: string[]
    judge: JudgeSettings | null
    source: Record<string, unknown>
    notes: string | null
    target: { slug: string; wful_profile: string; workspace: string } | null
  }
  report: CampaignReport
  rounds: number[]
  matrix: MatrixRow[]
}

export interface ToolCallView {
  index: number
  name: string
  description: string | null
  args: Record<string, unknown> | null
  output: unknown
  output_raw: string
  output_bytes: number
  output_parse_error: string | null
  truncated: boolean
  call_id: string | null
  duration_ms: number | null
  declared_mock: boolean
  runtime_mocked?: boolean
  matches_mock: boolean | null
}

export interface MessageView {
  index: number
  role: 'user' | 'agent' | 'tool' | 'system'
  text: string | null
  tool_index: number | null
  timestamp: string | null
}

export interface OfficialJudgeView {
  model: string | null
  verdict: string | null
  passed: boolean | null
  score: number | null
  explanation: string | null
  suggestions: string | null
  failure_reason: string | null
  prompt: string | null
  prompt_hash: string | null
  token_usage: Record<string, unknown> | null
  saw_tool_output: boolean | null
}

export interface TurnView {
  index: number
  user_message: string | null
  agent_text: string
  messages: MessageView[]
  tool_calls: ToolCallView[]
  expected: {
    user_message: string | null
    reference_response: string | null
    expected_output: string | null
    metadata: Record<string, unknown>
    tools_allowed: string[]
    assertions: Record<string, unknown>[]
    judge_model: string | null
    turn_prompt: string | null
    declared_mocks: Record<string, unknown>[]
  }
  official: OfficialJudgeView | null
  passed: boolean | null
  failure_reason: string | null
}

export interface LLMCallView {
  source: 'trace' | 'activity' | 'judge_official' | 'judge_ours'
  name: string
  model: string | null
  input_tokens: number | null
  cached_input_tokens: number | null
  output_tokens: number | null
  reasoning_tokens: number | null
  total_tokens: number | null
  duration_ms: number | null
  provider: string | null
}

export interface AttemptViewData {
  campaign: string
  scenario: string
  short: string
  round: number
  result_id: string | null
  communication_id: string | null
  run_status: string | null
  official_passed: boolean | null
  failed_at_turn: number | null
  execution_time_ms: number | null
  created_at: string | null
  channel: string | null
  agent_model: string | null
  snapshot_id: string | null
  scenario_name: string | null
  scenario_description: string | null
  agent_version: string | null
  turns: TurnView[]
  llm_calls: LLMCallView[]
  system_prompt: string | null
  system_prompt_hash: string | null
  system_prompt_source: string | null
  active_skill: string | null
  routing_decision: string | null
  tools_available: string[]
  trace_available: boolean
  trace_note: string | null
  trace_spans: number | null
  activity_available: boolean
  agent_tokens: TokenUsage | null
  applied_mocks: Record<string, unknown>[]
  spans: Record<string, unknown>[]
  warnings: string[]
}

export interface ClaimCheck {
  claim: string
  status: 'SUPPORTED' | 'PARTIAL' | 'UNSUPPORTED' | 'CONTRADICTED'
  evidence_quote: string
  evidence_locator: string
  note: string
}

export interface VoteVerdict {
  claims: ClaimCheck[]
  grounding_score: number
  facts: { fact: string; status: Coverage; evidence_quote: string; note: string }[]
  completeness_coverage: Coverage
  completeness_score: number
  clauses: { clause: string; status: string; evidence_quote: string; note: string }[]
  clauses_score: number
  customer_care_score?: number | null
  provenance?: { cited_sources: string[]; invented_sources: string[]; correct: boolean; note: string } | null
  provenance_score?: number | null
  taxonomy: string[]
  explanation: string
  suggestion: string
}

export interface TurnVerdict {
  turn_index: number
  votes: { index: number; verdict: VoteVerdict | null; usage: TokenUsage | null; error: string | null; duration_ms: number | null }[]
  deterministic: { name: string; passed: boolean; detail: string; hits: string[]; blocking: boolean }[]
  deterministic_passed: boolean
  criteria: { name: string; scores: number[]; score: number; mean: number; votes_total: number; passed: boolean; required: boolean }[]
  score: number
  score_capped_by: string | null
  passed: boolean
  coverage: Coverage | null
  taxonomy: string[]
  explanation: string
  suggestion: string
  tokens: TokenUsage
  errors: string[]
}

export interface AttemptVerdict {
  campaign: string
  scenario: string
  short: string
  round: number
  rubric_version: string
  judge: JudgeSettings
  turns: TurnVerdict[]
  score: number
  passed: boolean
  coverage: Coverage | null
  taxonomy: string[]
  explanation: string
  tokens: TokenUsage
  official_passed: boolean | null
  agreement: string
  judged_at: string | null
  duration_ms: number | null
  errors: string[]
}

export interface Sibling {
  scenario: string
  short: string
  round: number
  score: number | null
  ours: boolean | null
  official: boolean | null
  status: AttemptStatus
}

export interface AttemptDetail {
  view: AttemptViewData
  verdict: AttemptVerdict | null
  legacy_verdict: { rubric_version: string | null; judge_model: string | null; passed: boolean | null; explanation: string | null; taxonomy: string[] } | null
  meta: Record<string, unknown> | null
  raw: Record<string, boolean>
  siblings: Sibling[]
  campaign: { id: string; label: string; rounds: number }
}

export interface ProgressEvent {
  ts: string
  type: string
  scenario?: string
  short?: string
  round?: number
  passed?: boolean
  our_passed?: boolean | null
  official_passed?: boolean | null
  status?: string
  done?: number
  total?: number
  attempts?: number
  error?: string | null
}
