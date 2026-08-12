"""Data model.

Three families live here:

* **Storage** — what a campaign and its attempts look like on disk
  (`CampaignManifest`, `AttemptRef`).
* **Views** — the normalized shape of one attempt, derived from the raw
  `wful eval result` payload plus its trace. Both the judge and the dashboard
  consume views, never raw payloads, so the parsing rules live in one place
  (`normalize.py`).
* **Judgement** — our rubric's structured output (`VoteVerdict`, one per LLM
  vote) and the aggregated `AttemptVerdict`.

The `VoteVerdict` tree is also the LLM's response schema. OpenAI strict
json_schema mode requires every property to be required, so nothing in that
subtree carries a default: empty means `""` or `[]`, never null.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

AttemptStatus = Literal["pending", "running", "collected", "judged", "error"]
CampaignKind = Literal["imported", "live"]


class AgentTarget(BaseModel):
    """Which agent, on which platform context, a campaign speaks to."""

    slug: str
    id: str | None = None
    wful_profile: str
    workspace: str
    repo: str | None = None


class AttemptRef(BaseModel):
    """One scenario executed once. The unit of work and of navigation."""

    scenario: str
    short: str
    round: int
    status: AttemptStatus = "pending"
    result_id: str | None = None
    run_id: str | None = None
    communication_id: str | None = None
    official_passed: bool | None = None
    our_passed: bool | None = None
    our_score: float | None = None
    trace_available: bool | None = None
    execution_time_ms: int | None = None
    error: str | None = None
    updated_at: str | None = None

    @property
    def key(self) -> str:
        return f"{self.scenario}/{self.round}"


class JudgeSettings(BaseModel):
    """The judge configuration a verdict was produced with (kept for audit)."""

    model: str
    votes: int
    reasoning_effort: str
    rubric_version: str
    required_criteria: list[str]
    pass_threshold: float = 4.0
    min_criterion_score: int = 3


class CampaignManifest(BaseModel):
    """`data/<id>/campaign.json` — the index of everything in a campaign."""

    id: str
    label: str
    kind: CampaignKind
    created_at: str
    updated_at: str | None = None
    target: AgentTarget | None = None
    snapshot_id: str | None = None
    agent_commit_sha: str | None = None
    agent_model: str | None = None
    batch: str | None = None
    rounds: int = 1
    concurrency: int | None = None
    scenarios: list[str] = Field(default_factory=list)
    judge: JudgeSettings | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    attempts: list[AttemptRef] = Field(default_factory=list)

    def attempt(self, scenario: str, round_: int) -> AttemptRef | None:
        for ref in self.attempts:
            if ref.scenario == scenario and ref.round == round_:
                return ref
        return None


# --------------------------------------------------------------------------
# Views — normalized attempt
# --------------------------------------------------------------------------


class TokenUsage(BaseModel):
    """Token accounting, shared by agent-side (activity) and judge-side usage."""

    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            provider=self.provider or other.provider,
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class ToolCallView(BaseModel):
    """A tool the agent invoked, with the payload it got back.

    `output` is the parsed payload when the raw string is JSON (the platform
    wraps tool results as `{"Response": {"result": …}}`, which we unwrap into
    `output` while keeping the envelope in `output_raw`).
    """

    index: int
    name: str
    description: str | None = None
    args: dict[str, Any] | None = None
    output: Any = None
    output_raw: str = ""
    output_bytes: int = 0
    output_parse_error: str | None = None
    truncated: bool = False
    call_id: str | None = None
    duration_ms: int | None = None
    # Oracle scenarios declare mocks: did this call match one, and byte-wise?
    declared_mock: bool = False
    matches_mock: bool | None = None


class MessageView(BaseModel):
    """One item on the conversation spine, in chronological order."""

    index: int
    role: Literal["user", "agent", "tool", "system"]
    text: str | None = None
    tool_index: int | None = None
    timestamp: str | None = None


class OfficialJudgeView(BaseModel):
    """The platform judge's verdict for a turn, kept for side-by-side compare."""

    model: str | None = None
    verdict: str | None = None
    passed: bool | None = None
    score: float | None = None
    explanation: str | None = None
    suggestions: str | None = None
    failure_reason: str | None = None
    prompt: str | None = None
    prompt_hash: str | None = None
    token_usage: dict[str, Any] | None = None
    # True when the judge prompt carried no tool output at all — the "blind
    # judge" bug that made the official number pessimistic.
    saw_tool_output: bool | None = None


class ExpectedTurnView(BaseModel):
    """What the scenario author asked of this turn."""

    user_message: str | None = None
    reference_response: str | None = None
    expected_output: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools_allowed: list[str] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    judge_model: str | None = None
    turn_prompt: str | None = None
    declared_mocks: list[dict[str, Any]] = Field(default_factory=list)


class LLMCallView(BaseModel):
    """An LLM call with its cost metadata.

    Sources differ in what the platform exposes: `trace` spans carry the model,
    provider chain and timing but no token counts; `activity` carries the
    agent-side token totals for the whole conversation (input/cached/output);
    the judges report their own per-call usage.
    """

    source: Literal["trace", "activity", "judge_official", "judge_ours"]
    name: str
    model: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: float | None = None
    provider: str | None = None


class TurnView(BaseModel):
    index: int
    user_message: str | None = None
    agent_text: str = ""
    messages: list[MessageView] = Field(default_factory=list)
    tool_calls: list[ToolCallView] = Field(default_factory=list)
    expected: ExpectedTurnView = Field(default_factory=ExpectedTurnView)
    official: OfficialJudgeView | None = None
    passed: bool | None = None
    failure_reason: str | None = None


class AttemptView(BaseModel):
    """Everything about one attempt, ready for the judge and the dashboard."""

    campaign: str
    scenario: str
    short: str
    round: int
    result_id: str | None = None
    run_id: str | None = None
    communication_id: str | None = None
    run_status: str | None = None
    official_passed: bool | None = None
    failed_at_turn: int | None = None
    execution_time_ms: int | None = None
    created_at: str | None = None
    channel: str | None = None
    agent_id: str | None = None
    agent_model: str | None = None
    snapshot_id: str | None = None
    scenario_name: str | None = None
    scenario_description: str | None = None
    agent_version: str | None = None
    turns: list[TurnView] = Field(default_factory=list)
    llm_calls: list[LLMCallView] = Field(default_factory=list)
    system_prompt: str | None = None
    system_prompt_hash: str | None = None
    system_prompt_source: str | None = None
    active_skill: str | None = None
    routing_decision: str | None = None
    tools_available: list[str] = Field(default_factory=list)
    trace_available: bool = False
    trace_note: str | None = None
    trace_spans: int | None = None
    activity_available: bool = False
    agent_tokens: TokenUsage | None = None
    applied_mocks: list[dict[str, Any]] = Field(default_factory=list)
    spans: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Judgement
# --------------------------------------------------------------------------

TaxonomyTag = Literal[
    "unsupported_claim",
    "contradicts_payload",
    "missing_required_fact",
    "partial_answer",
    "missing_clause",
    "wrong_channel_or_procedure",
    "overreach_outside_kb",
    "internal_field_leak",
    "no_tool_call",
    "ignored_tool_payload",
    "hedging_without_answer",
    "poor_customer_care",
    "mentions_internal_sources",
    "premature_tool_call",
    "none",
]

ClaimStatus = Literal["SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONTRADICTED"]
Coverage = Literal["FULL", "PARTIAL", "MISS"]


class ClaimCheck(BaseModel):
    """One atomic claim from the agent's answer, checked against the payload."""

    claim: str
    status: ClaimStatus
    evidence_quote: str
    evidence_locator: str
    note: str


class FactCheck(BaseModel):
    """One reference fact the answer was supposed to convey."""

    fact: str
    status: Coverage
    evidence_quote: str
    note: str


class ClauseCheck(BaseModel):
    """A condition/caveat the payload marks as mandatory to relay."""

    clause: str
    status: Literal["PRESENT", "PARTIAL", "ABSENT", "NOT_REQUIRED"]
    evidence_quote: str
    note: str


class ProvenanceCheck(BaseModel):
    """Legacy v2/v3 evidence retained only when old verdicts are read."""

    cited_sources: list[str]
    invented_sources: list[str]
    correct: bool
    note: str


Score = Literal[1, 2, 3, 4, 5]


class VoteVerdictCore(BaseModel):
    """Fields shared by current and historical judge votes.

    Each criterion is scored 1–5 rather than pass/fail: "said nothing false but
    answered nothing" and "contradicted the payload" are both failures on a
    binary scale, and they are not the same defect.
    """

    claims: list[ClaimCheck]
    grounding_score: Score
    facts: list[FactCheck]
    completeness_coverage: Coverage
    completeness_score: Score
    clauses: list[ClauseCheck]
    clauses_score: Score
    explanation: str
    suggestion: str

    model_config = {"extra": "forbid"}


class VoteVerdictV4(VoteVerdictCore):
    """Strict response schema sent to the v4 judge."""

    customer_care_score: Score
    taxonomy: list[TaxonomyTag]


class LegacyVoteVerdictV3(VoteVerdictCore):
    """Old vote shape, read-only compatibility for v2/v3 verdict files."""

    provenance: ProvenanceCheck
    provenance_score: Score
    taxonomy: list[str]


# Public name remains the current judge schema for callers that imported it.
VoteVerdict = VoteVerdictV4


class DeterministicCheck(BaseModel):
    """A check decided in code, before any LLM sees the attempt."""

    name: str
    passed: bool
    detail: str
    hits: list[str] = Field(default_factory=list)
    blocking: bool = True


class CriterionOutcome(BaseModel):
    """One criterion after aggregating the votes.

    `score` is the **median** of the votes — one judge out of line cannot move
    it — with the mean kept alongside to show spread.
    """

    name: str
    scores: list[int] = Field(default_factory=list)
    score: float = 0.0
    mean: float = 0.0
    votes_total: int = 0
    required: bool = True
    # Kept so the binary metrics stay comparable with the platform judge.
    passed: bool = False


class VoteRecord(BaseModel):
    """A vote plus its cost and any error that killed it."""

    index: int
    verdict: LegacyVoteVerdictV3 | VoteVerdictV4 | None = None
    usage: TokenUsage | None = None
    error: str | None = None
    duration_ms: float | None = None


Agreement = Literal[
    "both_pass",
    "both_fail",
    "ours_pass_official_fail",
    "ours_fail_official_pass",
    "unknown",
]


class TurnVerdict(BaseModel):
    """Our aggregated judgement for one turn: N votes, median score per criterion."""

    turn_index: int
    votes: list[VoteRecord] = Field(default_factory=list)
    deterministic: list[DeterministicCheck] = Field(default_factory=list)
    deterministic_passed: bool = True
    criteria: list[CriterionOutcome] = Field(default_factory=list)
    score: float = 0.0
    score_capped_by: str | None = None
    passed: bool = False
    coverage: Coverage | None = None
    taxonomy: list[str] = Field(default_factory=list)
    explanation: str = ""
    suggestion: str = ""
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    errors: list[str] = Field(default_factory=list)


class AttemptVerdict(BaseModel):
    """`verdict.json` — our verdict for an attempt (all turns must pass)."""

    campaign: str
    scenario: str
    short: str
    round: int
    result_id: str | None = None
    rubric_version: str
    judge: JudgeSettings
    turns: list[TurnVerdict] = Field(default_factory=list)
    score: float = 0.0
    passed: bool = False
    coverage: Coverage | None = None
    taxonomy: list[str] = Field(default_factory=list)
    explanation: str = ""
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    official_passed: bool | None = None
    agreement: Agreement = "unknown"
    judged_at: str | None = None
    duration_ms: float | None = None
    errors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


class ScenarioOutcome(BaseModel):
    scenario: str
    short: str
    rounds_total: int
    score: float | None = None
    our_passes: int
    official_passes: int
    our_majority: bool | None = None
    official_majority: bool | None = None
    stability: Literal["stable_pass", "stable_fail", "flaky", "unknown"] = "unknown"
    taxonomy: list[str] = Field(default_factory=list)
    judged_rounds: int = 0


class CriterionStats(BaseModel):
    name: str
    attempts_judged: int
    attempts_passed: int
    mean_score: float = 0.0
    distribution: dict[str, int] = Field(default_factory=dict)


class CampaignReport(BaseModel):
    campaign: str
    label: str
    kind: CampaignKind
    generated_at: str
    agent_model: str | None = None
    snapshot_id: str | None = None
    judge: JudgeSettings | None = None
    scenarios_total: int = 0
    attempts_total: int = 0
    attempts_collected: int = 0
    attempts_judged: int = 0
    # Our numbers — the score is the headline, the pass counts are the bridge
    # to the platform's binary judge.
    mean_score: float = 0.0
    score_distribution: dict[str, int] = Field(default_factory=dict)
    our_pass_attempts: int = 0
    our_majority_pass: int = 0
    our_any_pass: int = 0
    our_all_pass: int = 0
    # Platform numbers, same attempts
    official_pass_attempts: int = 0
    official_majority_pass: int = 0
    official_any_pass: int = 0
    official_all_pass: int = 0
    agreement: dict[str, int] = Field(default_factory=dict)
    criteria: list[CriterionStats] = Field(default_factory=list)
    deterministic_failures: dict[str, int] = Field(default_factory=dict)
    taxonomy: dict[str, int] = Field(default_factory=dict)
    coverage: dict[str, int] = Field(default_factory=dict)
    per_round_pass: dict[str, int] = Field(default_factory=dict)
    scenarios: list[ScenarioOutcome] = Field(default_factory=list)
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    agent_tokens: TokenUsage = Field(default_factory=TokenUsage)
    trace_coverage: int = 0
    activity_coverage: int = 0
    rubric_versions: list[str] = Field(default_factory=list)
    wall_clock_ms: int | None = None
    notes: list[str] = Field(default_factory=list)
