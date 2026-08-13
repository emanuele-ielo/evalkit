"""Metrics over a campaign, and the diff between two campaigns.

Scenario-level numbers use **majority over rounds**, the same convention the Vera
analysis settled on: a scenario that passes 2 of 3 rounds counts as a pass, and
`any`/`all` are reported next to it so flakiness stays visible instead of hiding
inside an average.

Comparisons are attempt-level, with an exact McNemar test on the discordant
pairs. That is the honest way to say "this change moved the number" when the same
scenarios are re-run: it only counts attempts that actually flipped.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Sequence

from .schemas import (
    AttemptVerdict,
    CampaignManifest,
    CampaignReport,
    CriterionStats,
    ScenarioOutcome,
    TokenUsage,
)
from .store import CampaignStore, utcnow


def _majority(passes: int, total: int) -> bool | None:
    if total == 0:
        return None
    return passes * 2 > total


def _stability(passes: int, total: int) -> str:
    if total == 0:
        return "unknown"
    if passes == total:
        return "stable_pass"
    if passes == 0:
        return "stable_fail"
    return "flaky"


def build_report(store: CampaignStore, manifest: CampaignManifest) -> CampaignReport:
    """Compute every campaign-level number from what is on disk."""
    report = CampaignReport(
        campaign=manifest.id,
        label=manifest.label,
        kind=manifest.kind,
        generated_at=utcnow(),
        agent_model=manifest.agent_model,
        snapshot_id=manifest.snapshot_id,
        judge=manifest.judge,
        scenarios_total=len(manifest.scenarios) or len({ref.scenario for ref in manifest.attempts}),
        attempts_total=len(manifest.attempts),
    )

    by_scenario: dict[str, list[tuple[int, AttemptVerdict | None, bool | None]]] = {}
    criteria_judged: Counter[str] = Counter()
    criteria_passed: Counter[str] = Counter()
    criteria_scores: dict[str, list[float]] = {}
    criteria_dist: dict[str, Counter[str]] = {}
    attempt_scores: list[float] = []
    score_dist: Counter[str] = Counter()
    deterministic_failures: Counter[str] = Counter()
    taxonomy: Counter[str] = Counter()
    coverage: Counter[str] = Counter()
    per_round_pass: Counter[str] = Counter()
    agreement: Counter[str] = Counter()
    rubric_versions: set[str] = set()
    judge_models: set[str] = set()
    judge_tokens = TokenUsage()
    agent_tokens = TokenUsage()
    legacy_verdicts = 0

    for ref in manifest.attempts:
        paths = store.attempt_paths(ref.scenario, ref.round)
        collected = paths.exists()
        if collected:
            report.attempts_collected += 1
        if paths.trace.exists():
            trace = store.read_trace(ref.scenario, ref.round)
            if isinstance(trace, dict) and "missing" not in trace:
                report.trace_coverage += 1
        activity = store.read_activity(ref.scenario, ref.round) if paths.activity.exists() else None
        if isinstance(activity, dict):
            report.activity_coverage += 1
            usage = activity.get("tokens_usage") or {}
            agent_tokens = agent_tokens + TokenUsage(
                input_tokens=int(usage.get("total_input_tokens") or 0),
                cached_input_tokens=int(usage.get("total_cached_input_tokens") or 0),
                output_tokens=int(usage.get("total_output_tokens") or 0),
            )

        verdict = store.read_verdict(ref.scenario, ref.round)
        if verdict is None and store.read_legacy_verdict(ref.scenario, ref.round):
            legacy_verdicts += 1
        by_scenario.setdefault(ref.scenario, []).append((ref.round, verdict, ref.official_passed))

        if ref.official_passed is not None:
            report.official_attempts_available += 1
        if ref.official_passed:
            report.official_pass_attempts += 1
        if verdict is None:
            continue

        report.attempts_judged += 1
        rubric_versions.add(verdict.rubric_version)
        judge_models.add(verdict.judge.model)
        attempt_scores.append(verdict.score)
        score_dist[str(int(round(verdict.score)))] += 1
        judge_tokens = judge_tokens + verdict.tokens
        if verdict.official_passed is not None:
            agreement[verdict.agreement] += 1
        if verdict.passed:
            report.our_pass_attempts += 1
            per_round_pass[str(ref.round)] += 1
        if verdict.coverage:
            coverage[verdict.coverage] += 1
        for tag in verdict.taxonomy:
            taxonomy[tag] += 1

        for name in dict.fromkeys(outcome.name for turn in verdict.turns for outcome in turn.criteria):
            criteria_scores.setdefault(name, [])
            criteria_dist.setdefault(name, Counter())
            outcomes = [
                outcome
                for turn in verdict.turns
                for outcome in turn.criteria
                if outcome.name == name and outcome.votes_total > 0
            ]
            if not outcomes:
                continue
            criteria_judged[name] += 1
            if all(outcome.passed for outcome in outcomes):
                criteria_passed[name] += 1
            # One number per attempt per criterion: its weakest turn.
            weakest = min(outcome.score for outcome in outcomes)
            criteria_scores[name].append(weakest)
            criteria_dist[name][str(int(round(weakest)))] += 1

        for turn in verdict.turns:
            for check in turn.deterministic:
                if not check.passed:
                    deterministic_failures[check.name] += 1

    for scenario, rows in sorted(by_scenario.items()):
        judged = [(round_, verdict) for round_, verdict, _ in rows if verdict is not None]
        our_passes = sum(1 for _, verdict in judged if verdict.passed)
        official_values = [official for _, _, official in rows if official is not None]
        official_passes = sum(1 for official in official_values if official)
        short = next((ref.short for ref in manifest.attempts if ref.scenario == scenario), scenario)
        scenario_scores = [verdict.score for _, verdict in judged]
        outcome = ScenarioOutcome(
            scenario=scenario,
            short=short,
            score=round(sum(scenario_scores) / len(scenario_scores), 2) if scenario_scores else None,
            rounds_total=len(rows),
            judged_rounds=len(judged),
            our_passes=our_passes,
            official_passes=official_passes,
            official_rounds=len(official_values),
            our_majority=_majority(our_passes, len(judged)),
            official_majority=_majority(official_passes, len(official_values)),
            stability=_stability(our_passes, len(judged)),  # type: ignore[arg-type]
            taxonomy=sorted({tag for _, verdict in judged for tag in verdict.taxonomy}),
        )
        report.scenarios.append(outcome)
        if outcome.our_majority:
            report.our_majority_pass += 1
        if our_passes >= 1:
            report.our_any_pass += 1
        if judged and our_passes == len(judged):
            report.our_all_pass += 1
        if outcome.official_majority:
            report.official_majority_pass += 1
        if official_values:
            report.official_scenarios_available += 1
        if official_passes >= 1:
            report.official_any_pass += 1
        if official_values and official_passes == len(official_values):
            report.official_all_pass += 1

    report.criteria = [
        CriterionStats(
            name=name,
            attempts_judged=criteria_judged[name],
            attempts_passed=criteria_passed[name],
            mean_score=round(sum(criteria_scores[name]) / len(criteria_scores[name]), 2) if criteria_scores[name] else 0.0,
            distribution=dict(sorted(criteria_dist[name].items())),
        )
        for name in criteria_scores
        if criteria_judged[name]
    ]
    report.mean_score = round(sum(attempt_scores) / len(attempt_scores), 2) if attempt_scores else 0.0
    report.score_distribution = dict(sorted(score_dist.items()))
    report.deterministic_failures = dict(deterministic_failures.most_common())
    report.taxonomy = dict(taxonomy.most_common())
    report.coverage = dict(coverage)
    report.per_round_pass = dict(sorted(per_round_pass.items()))
    report.agreement = dict(agreement)
    report.tokens = judge_tokens
    report.agent_tokens = agent_tokens
    report.rubric_versions = sorted(rubric_versions)

    notes: list[str] = []
    if len(rubric_versions) > 1:
        notes.append(f"verdicts mix rubric versions {sorted(rubric_versions)} — re-judge with --force to align them")
    if len(judge_models) > 1:
        # Two judges in one number is two numbers. Say so loudly.
        notes.append(
            f"verdicts were produced by different judge models {sorted(judge_models)} — "
            "this number is not internally comparable; re-judge with --force on one model"
        )
    if legacy_verdicts:
        notes.append(
            f"{legacy_verdicts} verdict(s) come from an older rubric and cannot be read on the 1-5 scale — "
            "re-judge with --force to convert them"
        )
    unjudged = report.attempts_collected - report.attempts_judged
    if unjudged > 0:
        notes.append(f"{unjudged} collected attempt(s) not judged yet — our numbers cover {report.attempts_judged}")
    missing = report.attempts_total - report.attempts_collected
    if missing > 0:
        notes.append(f"{missing} attempt(s) have no result payload yet")
    if report.attempts_collected and report.official_attempts_available == 0:
        notes.append("no Wonderful platform verdicts — expected for direct Chat V3 campaigns")
    if report.trace_coverage < report.attempts_collected:
        notes.append(
            f"{report.attempts_collected - report.trace_coverage} attempt(s) without a trace "
            "(no system prompt or span timing for those)"
        )
    report.notes = notes
    return report


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _mcnemar_exact(b: int, c: int) -> float | None:
    """Two-sided exact McNemar p-value over the discordant pairs."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def compare_campaigns(
    base_store: CampaignStore,
    base: CampaignManifest,
    other_store: CampaignStore,
    other: CampaignManifest,
) -> dict[str, Any]:
    """Attempt-level diff of our verdicts on the scenarios both campaigns share."""
    base_map: dict[tuple[str, int], AttemptVerdict] = {}
    other_map: dict[tuple[str, int], AttemptVerdict] = {}
    for store, manifest, target in ((base_store, base, base_map), (other_store, other, other_map)):
        for ref in manifest.attempts:
            verdict = store.read_verdict(ref.scenario, ref.round)
            if verdict is not None:
                target[(ref.scenario, ref.round)] = verdict

    shared_scenarios = sorted({s for s, _ in base_map} & {s for s, _ in other_map})
    pairs = sorted(set(base_map) & set(other_map))

    fail_to_pass: list[dict[str, Any]] = []
    pass_to_fail: list[dict[str, Any]] = []
    stable = 0
    for key in pairs:
        before, after = base_map[key].passed, other_map[key].passed
        if before == after:
            stable += 1
            continue
        row = {
            "scenario": key[0],
            "short": base_map[key].short,
            "round": key[1],
            "explanation_before": base_map[key].explanation[:200],
            "explanation_after": other_map[key].explanation[:200],
        }
        (fail_to_pass if after else pass_to_fail).append(row)

    def majority(map_: dict[tuple[str, int], AttemptVerdict], scenario: str) -> bool | None:
        rows = [verdict for (s, _), verdict in map_.items() if s == scenario]
        if not rows:
            return None
        return sum(1 for verdict in rows if verdict.passed) * 2 > len(rows)

    flips = []
    for scenario in shared_scenarios:
        before, after = majority(base_map, scenario), majority(other_map, scenario)
        if before is not None and after is not None and before != after:
            flips.append(
                {
                    "scenario": scenario,
                    "before": "pass" if before else "fail",
                    "after": "pass" if after else "fail",
                }
            )

    return {
        "base": {"campaign": base.id, "label": base.label},
        "other": {"campaign": other.id, "label": other.label},
        "shared_scenarios": len(shared_scenarios),
        "shared_attempts": len(pairs),
        "base_pass_attempts": sum(1 for key in pairs if base_map[key].passed),
        "other_pass_attempts": sum(1 for key in pairs if other_map[key].passed),
        "base_majority_pass": sum(1 for s in shared_scenarios if majority(base_map, s)),
        "other_majority_pass": sum(1 for s in shared_scenarios if majority(other_map, s)),
        "fail_to_pass": fail_to_pass,
        "pass_to_fail": pass_to_fail,
        "stable": stable,
        "mcnemar_p": _mcnemar_exact(len(fail_to_pass), len(pass_to_fail)),
        "majority_flips": flips,
        "rubric_versions": {
            base.id: sorted({v.rubric_version for v in base_map.values()}),
            other.id: sorted({v.rubric_version for v in other_map.values()}),
        },
    }


def compare_to_official(store: CampaignStore, manifest: CampaignManifest) -> dict[str, Any]:
    """Where our judge and the platform judge disagree, and on what."""
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for ref in manifest.attempts:
        verdict = store.read_verdict(ref.scenario, ref.round)
        if verdict is None or verdict.official_passed is None:
            continue
        counts[verdict.agreement] += 1
        if verdict.agreement in {"ours_pass_official_fail", "ours_fail_official_pass"}:
            official_reason = ""
            result = store.read_result(ref.scenario, ref.round)
            if isinstance(result, dict):
                turns = result.get("turn_results") or []
                if turns:
                    official_reason = str(turns[0].get("failure_reason") or "")[:240]
            rows.append(
                {
                    "scenario": ref.scenario,
                    "short": ref.short,
                    "round": ref.round,
                    "agreement": verdict.agreement,
                    "ours": verdict.explanation[:240],
                    "official": official_reason,
                    "taxonomy": verdict.taxonomy,
                }
            )
    total = sum(counts.values())
    agreed = counts["both_pass"] + counts["both_fail"]
    return {
        "counts": dict(counts),
        "attempts": total,
        "agreement_rate": (agreed / total) if total else None,
        "disagreements": rows,
    }


def format_report(report: CampaignReport, *, compact: bool = False) -> str:
    """Terminal rendering — the same numbers the dashboard shows."""
    lines: list[str] = []
    lines.append(f"campaign {report.campaign} — {report.label} [{report.kind}]")
    if report.judge:
        lines.append(
            f"judge: {report.judge.model} x{report.judge.votes} votes, rubric {report.judge.rubric_version}, "
            f"required: {', '.join(report.judge.required_criteria)}"
        )
        if report.attempts_judged:
            lines.append(
                f"pass bridge: every turn mean >= {report.judge.pass_threshold:g}/5, "
                f"required criterion >= {report.judge.min_criterion_score}/5, mechanical blockers clear"
            )
    lines.append(
        f"attempts: {report.attempts_judged} judged / {report.attempts_collected} collected / "
        f"{report.attempts_total} planned · scenarios: {report.scenarios_total}"
    )
    if report.attempts_judged:
        lines.append("")
        lines.append(f"  SCORE  {report.mean_score:.2f} / 5   (mean over {report.attempts_judged} attempts)")
        spread = " ".join(f"{k}★:{v}" for k, v in report.score_distribution.items())
        lines.append(f"         {spread}")
    def ratio(passes: int, total: int) -> str:
        return f"{passes}/{total}" if total else "—"

    lines.append("")
    lines.append("                          ours   official")
    official_scenarios = report.official_scenarios_available
    lines.append(
        f"  majority pass      {ratio(report.our_majority_pass, report.scenarios_total):>9}  "
        f"{ratio(report.official_majority_pass, official_scenarios):>9}"
    )
    lines.append(
        f"  any-round pass     {ratio(report.our_any_pass, report.scenarios_total):>9}  "
        f"{ratio(report.official_any_pass, official_scenarios):>9}"
    )
    lines.append(
        f"  all-rounds pass    {ratio(report.our_all_pass, report.scenarios_total):>9}  "
        f"{ratio(report.official_all_pass, official_scenarios):>9}"
    )
    lines.append(
        f"  attempts passed    {ratio(report.our_pass_attempts, report.attempts_judged):>9}  "
        f"{ratio(report.official_pass_attempts, report.official_attempts_available):>9}"
    )

    if report.criteria:
        lines.append("")
        lines.append("criteria (mean score, and how the attempts distribute 1-5):")
        for stat in report.criteria:
            spread = " ".join(f"{k}★:{v}" for k, v in stat.distribution.items())
            lines.append(f"  {stat.name:<14} {stat.mean_score:>4.2f}/5   {spread}")

    if report.agreement:
        lines.append("")
        lines.append("agreement with the platform judge:")
        for key in ("both_pass", "both_fail", "ours_pass_official_fail", "ours_fail_official_pass", "unknown"):
            if key in report.agreement:
                lines.append(f"  {key:<26} {report.agreement[key]}")

    if report.taxonomy and not compact:
        lines.append("")
        lines.append("quality and point-loss tags (attempts):")
        for tag, count in list(report.taxonomy.items())[:12]:
            lines.append(f"  {tag:<28} {count}")

    if report.deterministic_failures and not compact:
        lines.append("")
        lines.append("deterministic check failures:")
        for name, count in report.deterministic_failures.items():
            lines.append(f"  {name:<28} {count}")

    if report.coverage:
        lines.append("")
        lines.append(
            "reference-fact coverage: "
            + ", ".join(f"{key} {value}" for key, value in sorted(report.coverage.items()))
        )

    flaky = [s for s in report.scenarios if s.stability == "flaky"]
    if flaky and not compact:
        lines.append("")
        lines.append(f"flaky scenarios ({len(flaky)}): " + ", ".join(f"{s.short} {s.our_passes}/{s.judged_rounds}" for s in flaky))

    lines.append("")
    lines.append(
        f"judge tokens: in {report.tokens.input_tokens:,} (cached {report.tokens.cached_input_tokens:,}) "
        f"out {report.tokens.output_tokens:,} (reasoning {report.tokens.reasoning_tokens:,})"
    )
    if report.agent_tokens.input_tokens:
        lines.append(
            f"agent tokens: in {report.agent_tokens.input_tokens:,} "
            f"(cached {report.agent_tokens.cached_input_tokens:,}) out {report.agent_tokens.output_tokens:,}"
        )
    lines.append(
        f"data coverage: traces {report.trace_coverage}/{report.attempts_collected}, "
        f"activities {report.activity_coverage}/{report.attempts_collected}"
    )
    for note in report.notes:
        lines.append(f"note: {note}")
    return "\n".join(lines)


def format_scenario_table(report: CampaignReport, *, limit: int | None = None) -> str:
    rows = sorted(report.scenarios, key=lambda s: (s.score if s.score is not None else 99, s.short))
    if limit:
        rows = rows[:limit]
    lines = [f"{'scenario':<12} {'score':>6} {'ours':>6} {'official':>9}  stability    top quality tags"]
    for row in rows:
        ours = f"{row.our_passes}/{row.judged_rounds}"
        official = f"{row.official_passes}/{row.official_rounds}" if row.official_rounds else "—"
        score = f"{row.score:.2f}" if row.score is not None else "—"
        tags = ", ".join(row.taxonomy[:3])
        lines.append(f"{row.short:<12} {score:>6} {ours:>6} {official:>9}  {row.stability:<12} {tags}")
    return "\n".join(lines)


def summarize_sequence(reports: Sequence[CampaignReport]) -> str:
    """Trend line across campaigns, oldest first."""
    lines = [f"{'campaign':<28} {'label':<28} {'majority':>10} {'attempts':>10}"]
    for report in reports:
        lines.append(
            f"{report.campaign:<28} {report.label[:27]:<28} "
            f"{report.our_majority_pass:>4}/{report.scenarios_total:<5} "
            f"{report.our_pass_attempts:>4}/{report.attempts_judged}"
        )
    return "\n".join(lines)
