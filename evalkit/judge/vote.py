"""Aggregation: N votes plus the mechanical checks become one scored verdict.

Two decisions worth stating, because they are what make the number stable:

* **Median, not mean, per criterion.** With three votes, one judge scoring an
  answer 1 while the others say 4 cannot drag the criterion down — the median is
  4 and the disagreement stays visible in `scores`. The mean is kept alongside
  to show spread.
* **Scores, not pass/fail.** "Invented nothing but answered nothing" and
  "contradicted the payload" both fail a binary rubric, and they are not the
  same defect. A derived `passed` survives only as the bridge to the platform's
  binary judge, so agreement stays measurable.

Mechanical failures **cap** the score instead of vetoing it: a leaked internal
field, a missing tool call, or a contact detail handed out while a row is pending
channel validation (`pcv_no_contact`) means the answer cannot be better than a 2,
whatever the LLM thought of the prose. Any blocking `DeterministicCheck` flows
through `blocking_failures()` by name-agnostic contract, so new checks join the
cap without changes here.
"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Iterable, Sequence

from ..deterministic import blocking_failures, taxonomy_from_checks
from ..schemas import (
    Agreement,
    AttemptVerdict,
    Coverage,
    CriterionOutcome,
    DeterministicCheck,
    JudgeSettings,
    TokenUsage,
    TurnVerdict,
    VoteRecord,
)

CRITERIA: tuple[str, ...] = ("grounding", "completeness", "clauses", "customer_care")

_VOTE_FIELDS = {
    "grounding": "grounding_score",
    "completeness": "completeness_score",
    "clauses": "clauses_score",
    "customer_care": "customer_care_score",
}

# What a blocking mechanical failure caps the turn's score at.
DETERMINISTIC_CAP = 2.0


def _coverage_majority(values: Sequence[Coverage]) -> Coverage | None:
    if not values:
        return None
    counts = Counter(values)
    best, top = counts.most_common(1)[0]
    # A tie means the votes disagreed about how much was covered: say PARTIAL
    # rather than pick the optimistic or pessimistic side by accident.
    if list(counts.values()).count(top) > 1:
        return "PARTIAL"
    return best


def _taxonomy(votes: Sequence[VoteRecord], checks: Sequence[DeterministicCheck]) -> list[str]:
    usable = [vote.verdict for vote in votes if vote.verdict is not None]
    threshold = max(1, (len(usable) + 1) // 2)
    counts: Counter[str] = Counter()
    for verdict in usable:
        for tag in set(verdict.taxonomy):
            if tag != "none":
                counts[tag] += 1
    tags = {tag for tag, count in counts.items() if count >= threshold}
    tags.update(taxonomy_from_checks(list(checks)))
    return sorted(tags)


def _representative(votes: Sequence[VoteRecord], score: float) -> VoteRecord | None:
    """The vote whose own average is closest to the aggregate explains it best."""
    usable = [vote for vote in votes if vote.verdict is not None]
    if not usable:
        return None

    def distance(vote: VoteRecord) -> float:
        own = [getattr(vote.verdict, field) for field in _VOTE_FIELDS.values()]  # type: ignore[union-attr]
        return abs(sum(own) / len(own) - score)

    return min(usable, key=distance)


def aggregate_turn(
    turn_index: int,
    votes: list[VoteRecord],
    checks: list[DeterministicCheck],
    *,
    required: Iterable[str] = CRITERIA,
    pass_threshold: float = 4.0,
    min_criterion_score: int = 2,
) -> TurnVerdict:
    required_set = set(required)
    usable = [vote for vote in votes if vote.verdict is not None]

    outcomes: list[CriterionOutcome] = []
    for name in CRITERIA:
        field = _VOTE_FIELDS[name]
        scores = [int(getattr(vote.verdict, field)) for vote in usable]
        aggregated = float(median(scores)) if scores else 0.0
        outcomes.append(
            CriterionOutcome(
                name=name,
                scores=scores,
                score=aggregated,
                mean=round(sum(scores) / len(scores), 2) if scores else 0.0,
                votes_total=len(scores),
                required=name in required_set,
                passed=aggregated >= pass_threshold,
            )
        )

    required_outcomes = [outcome for outcome in outcomes if outcome.required and outcome.votes_total]
    score = round(sum(o.score for o in required_outcomes) / len(required_outcomes), 2) if required_outcomes else 0.0

    blocking = blocking_failures(checks)
    deterministic_passed = not blocking
    capped_by = None
    if blocking and score > DETERMINISTIC_CAP:
        score = DETERMINISTIC_CAP
        capped_by = ", ".join(check.name for check in blocking)

    # The per-criterion floor is only a catastrophic-defect guardrail. A 2/5
    # remains visible as a serious weakness, but the turn mean decides the
    # binary bridge; only a 1/5 can veto an otherwise passing average by default.
    weakest = min((o.score for o in required_outcomes), default=0.0)
    passed = bool(usable) and deterministic_passed and score >= pass_threshold and weakest >= min_criterion_score

    tokens = TokenUsage()
    for vote in votes:
        if vote.usage:
            tokens = tokens + vote.usage

    representative = _representative(votes, score)
    errors = [vote.error for vote in votes if vote.error]
    if not usable:
        errors.append("no usable vote — the turn cannot be judged")

    explanation = ""
    suggestion = ""
    if blocking:
        explanation = "; ".join(f"{check.name}: {check.detail}" for check in blocking)
    if representative and representative.verdict:
        detail = representative.verdict.explanation
        explanation = f"{explanation} — {detail}" if explanation else detail
        suggestion = representative.verdict.suggestion

    return TurnVerdict(
        turn_index=turn_index,
        votes=votes,
        deterministic=checks,
        deterministic_passed=deterministic_passed,
        criteria=outcomes,
        score=score,
        score_capped_by=capped_by,
        passed=passed,
        coverage=_coverage_majority([vote.verdict.completeness_coverage for vote in usable]),  # type: ignore[union-attr]
        taxonomy=_taxonomy(votes, checks),
        explanation=explanation,
        suggestion=suggestion,
        tokens=tokens,
        errors=[error for error in errors if error],
    )


def _agreement(ours: bool, official: bool | None) -> Agreement:
    if official is None:
        return "unknown"
    if ours and official:
        return "both_pass"
    if not ours and not official:
        return "both_fail"
    return "ours_pass_official_fail" if ours else "ours_fail_official_pass"


def _worst_coverage(values: Sequence[Coverage]) -> Coverage | None:
    for level in ("MISS", "PARTIAL", "FULL"):
        if level in values:
            return level  # type: ignore[return-value]
    return None


def aggregate_attempt(
    *,
    campaign: str,
    scenario: str,
    short: str,
    round_: int,
    result_id: str | None,
    turns: list[TurnVerdict],
    judge: JudgeSettings,
    official_passed: bool | None,
    duration_ms: float | None = None,
) -> AttemptVerdict:
    """An attempt scores the mean of its turns, and passes only if every turn does."""
    passed = bool(turns) and all(turn.passed for turn in turns)
    score = round(sum(turn.score for turn in turns) / len(turns), 2) if turns else 0.0
    tokens = TokenUsage()
    for turn in turns:
        tokens = tokens + turn.tokens
    weakest = sorted(turns, key=lambda turn: turn.score)[:1]
    explanation = "; ".join(
        f"turn {turn.turn_index + 1}: {turn.explanation}" for turn in (weakest or turns) if turn.explanation
    )
    return AttemptVerdict(
        campaign=campaign,
        scenario=scenario,
        short=short,
        round=round_,
        result_id=result_id,
        rubric_version=judge.rubric_version,
        judge=judge,
        turns=turns,
        score=score,
        passed=passed,
        coverage=_worst_coverage([turn.coverage for turn in turns if turn.coverage]),
        taxonomy=sorted({tag for turn in turns for tag in turn.taxonomy}),
        explanation=explanation[:600],
        tokens=tokens,
        official_passed=official_passed,
        agreement=_agreement(passed, official_passed),
        duration_ms=duration_ms,
        errors=[error for turn in turns for error in turn.errors],
    )


def reaggregate_verdict(
    verdict: AttemptVerdict,
    *,
    required: Iterable[str] = CRITERIA,
    pass_threshold: float = 4.0,
    min_criterion_score: int = 2,
) -> AttemptVerdict:
    """Recompute scores/pass from stored votes and deterministic checks.

    This changes no LLM judgement and consumes no tokens. It is the safe path
    when only the binary aggregation policy changes.
    """
    required_list = list(required)
    settings = verdict.judge.model_copy(
        update={
            "required_criteria": required_list,
            "pass_threshold": pass_threshold,
            "min_criterion_score": min_criterion_score,
        }
    )
    turns = [
        aggregate_turn(
            turn.turn_index,
            turn.votes,
            turn.deterministic,
            required=required_list,
            pass_threshold=pass_threshold,
            min_criterion_score=min_criterion_score,
        )
        for turn in verdict.turns
    ]
    updated = aggregate_attempt(
        campaign=verdict.campaign,
        scenario=verdict.scenario,
        short=verdict.short,
        round_=verdict.round,
        result_id=verdict.result_id,
        turns=turns,
        judge=settings,
        official_passed=verdict.official_passed,
        duration_ms=verdict.duration_ms,
    )
    updated.judged_at = verdict.judged_at
    return updated
