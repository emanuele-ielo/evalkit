"""`evalkit` command line.

    evalkit doctor                              check wiring without touching anything
    evalkit import <source> --label …           cached eval results → a campaign
    evalkit run --agent vera --rounds 3         run scenarios live (the only writing command)
    evalkit judge <campaign>                    grade (or re-grade) with our rubric
    evalkit reaggregate <campaign>              recompute pass from stored votes, no LLM calls
    evalkit report <campaign> [--vs other]      the numbers, and the diff between runs
    evalkit serve                               local dashboard
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import Config, ConfigError, load_config, load_llm_credentials
from .importers import import_results
from .judge.rubric import RUBRIC_VERSION
from .report import (
    build_report,
    compare_campaigns,
    compare_to_official,
    format_report,
    format_scenario_table,
)
from .runner import build_manifest, mint_snapshot, model_at_commit, resolve_scenarios, run_campaign
from .store import DataRoot, utcnow
from .wful import WfulClient, WfulError, WfulNotAllowed


def _timestamp_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _client_for(config: Config, agent_name: str) -> tuple[WfulClient, Any]:
    agent = config.agent(agent_name)
    repo = str(Path(agent.repo).expanduser()) if agent.repo else None
    client = WfulClient(
        profile=agent.wful_profile,
        workspace=agent.workspace,
        agent=agent.slug,
        cwd=repo,
    )
    return client, agent


def _print_progress(payload: dict[str, Any]) -> None:
    event = payload.get("event")
    if event == "campaign_started":
        print(f"· running {payload['attempts']} attempt(s), concurrency {payload['concurrency']}")
    elif event == "judge_started":
        print(f"· judging {payload['attempts']} attempt(s) ({payload['skipped']} already judged) — {payload['judge']}")
    elif event == "attempt_started":
        print(f"  → {payload['scenario']} r{payload['round']} started")
    elif event == "attempt_finished":
        mark = {"judged": "✓", "collected": "·", "error": "✗"}.get(str(payload.get("status")), "?")
        ours = "pass" if payload.get("ours") else "fail" if payload.get("ours") is not None else "—"
        official = "pass" if payload.get("official") else "fail" if payload.get("official") is not None else "—"
        suffix = f" {payload['error']}" if payload.get("error") else ""
        print(
            f"  {mark} {payload['scenario']} r{payload['round']}  ours={ours} official={official}"
            f"  [{payload['done']}/{payload['total']}]{suffix}"
        )
    elif event == "attempt_judged":
        mark = "✓" if payload["passed"] else "✗"
        official = "pass" if payload.get("official") else "fail" if payload.get("official") is not None else "—"
        print(
            f"  {mark} {payload['scenario']} r{payload['round']}  official={official}"
            f"  [{payload['done']}/{payload['total']}]  {payload['explanation']}"
        )
    elif event == "campaign_finished":
        print(f"· done: {payload['done']} attempt(s), {payload['errors']} error(s)")
    elif event == "imported":
        pass


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    print(f"config:      {', '.join(str(path) for path in config.source_files)}")
    print(f"data dir:    {config.data_dir} ({'exists' if config.data_dir.exists() else 'will be created'})")
    print(f"rubric:      {RUBRIC_VERSION}, required criteria: {', '.join(config.required_criteria)}")
    print(f"judge model: {config.llm.model} (votes {config.llm.votes}, effort {config.llm.reasoning_effort})")
    try:
        credentials = load_llm_credentials(config.llm)
        print(f"credentials: OK — {credentials.redacted()}")
    except ConfigError as exc:
        print(f"credentials: MISSING — {exc}")

    for name, agent in config.agents.items():
        repo = Path(agent.repo).expanduser() if agent.repo else None
        repo_state = "not configured"
        if repo:
            repo_state = "present" if repo.exists() else f"missing ({repo})"
        print(f"agent {name}: {agent.slug} profile={agent.wful_profile} workspace={agent.workspace[:8]}… repo={repo_state}")
        try:
            client = WfulClient(profile=agent.wful_profile, workspace=agent.workspace, agent=agent.slug, cwd=str(repo) if repo else None)
        except WfulError as exc:
            print(f"  wful: {exc}")
            continue
        try:
            who = asyncio.run(client.run_json(["whoami"], timeout=60))
            email = who.get("email") or who.get("user", {}).get("email") if isinstance(who, dict) else None
            print(f"  wful: OK{f' as {email}' if email else ''}")
        except (WfulError, WfulNotAllowed) as exc:
            print(f"  wful: {type(exc).__name__} — {exc}")

    campaigns = DataRoot(config.data_dir).list_campaigns()
    print(f"campaigns:   {len(campaigns)}")
    for store in campaigns[:10]:
        try:
            manifest = store.load()
        except (FileNotFoundError, ValueError):
            continue
        judged = sum(1 for ref in manifest.attempts if ref.status == "judged")
        print(f"  {manifest.id:<28} {manifest.label[:34]:<34} {judged}/{len(manifest.attempts)} judged")
    return 0


def cmd_import(args: argparse.Namespace, config: Config) -> int:
    source = Path(args.source).expanduser()
    if not source.exists():
        print(f"source not found: {source}", file=sys.stderr)
        return 1
    campaign_id = args.campaign or _timestamp_id("imported")
    root = DataRoot(config.data_dir)
    if root.exists(campaign_id) and not args.force:
        print(f"campaign {campaign_id} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    store = root.create(campaign_id)

    client = None
    agent = None
    if args.agent:
        client, agent = _client_for(config, args.agent)

    print(f"importing {source} → campaign {campaign_id}")
    manifest = asyncio.run(
        import_results(
            store,
            source,
            label=args.label or campaign_id,
            agent=agent,
            client=client,
            fetch_activities=not args.no_activities and client is not None,
            fetch_traces=args.traces and client is not None,
            concurrency=args.concurrency,
            notes=args.notes,
        )
    )
    print(
        f"· {len(manifest.attempts)} attempt(s), {len(manifest.scenarios)} scenario(s), "
        f"{manifest.rounds} round(s)"
    )
    source_info = manifest.source
    print(
        f"· activities fetched: {source_info.get('activities_fetched', 0)}, "
        f"traces fetched: {source_info.get('traces_fetched', 0)} "
        f"(missing {source_info.get('traces_missing', 0)})"
    )
    if source_info.get("agent_versions"):
        print(f"· agent versions seen: {', '.join(source_info['agent_versions'])}")
    report = build_report(store, manifest)
    store.write_report(report)
    print(f"\nnext: evalkit judge {campaign_id}")
    return 0


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    client, agent = _client_for(config, args.agent)
    repo = Path(agent.repo).expanduser() if agent.repo else None
    try:
        scenarios = resolve_scenarios(agent, explicit=args.scenarios, batch=args.batch)
    except (FileNotFoundError, ValueError) as exc:
        print(f"cannot resolve scenarios: {exc}", file=sys.stderr)
        return 1
    if args.limit:
        scenarios = scenarios[: args.limit]

    rounds = args.rounds or agent.rounds
    concurrency = args.concurrency or agent.concurrency
    root = DataRoot(config.data_dir)

    if args.resume:
        campaign_id = args.resume
        if not root.exists(campaign_id):
            print(f"cannot resume unknown campaign {campaign_id}", file=sys.stderr)
            return 1
        store = root.campaign(campaign_id)
        manifest = store.load()
        print(f"resuming campaign {campaign_id} — {manifest.label}")
    else:
        campaign_id = args.campaign or _timestamp_id("run")
        if root.exists(campaign_id):
            print(f"campaign {campaign_id} already exists (use --resume or a different --campaign)", file=sys.stderr)
            return 1
        snapshot_id = args.snapshot_id
        commit_sha = None
        if not snapshot_id:
            print("minting a snapshot from the clone's current commit…")
            try:
                snapshot_id, commit_sha, _ = asyncio.run(mint_snapshot(client, repo))
            except (WfulError, WfulNotAllowed) as exc:
                print(f"snapshot failed: {exc}", file=sys.stderr)
                return 1
        model = model_at_commit(repo, commit_sha) if repo else None
        print(
            f"snapshot {snapshot_id}"
            + (f" (commit {commit_sha[:7]})" if commit_sha else "")
            + (f", model {model}" if model else "")
        )
        store = root.create(campaign_id)
        manifest = build_manifest(
            campaign_id=campaign_id,
            label=args.label or campaign_id,
            agent=agent,
            scenarios=scenarios,
            rounds=rounds,
            concurrency=concurrency,
            snapshot_id=snapshot_id,
            commit_sha=commit_sha,
            model=model,
            batch=args.batch or agent.batch,
        )
        store.save(manifest)

    print(
        f"campaign {campaign_id}: {len(scenarios)} scenario(s) x {rounds} round(s) = "
        f"{len(manifest.attempts)} attempt(s), concurrency {concurrency}"
        + (", judging inline" if not args.no_judge else ", no inline judging")
    )
    if args.dry_run:
        for scenario in scenarios:
            print(f"  would run {scenario}")
        return 0

    manifest = asyncio.run(
        run_campaign(
            store,
            manifest,
            config,
            agent,
            client=client,
            judge_inline=not args.no_judge,
            resume=True,
            scenario_timeout=args.timeout,
            votes=args.votes,
            progress=_print_progress,
        )
    )
    report = build_report(store, manifest)
    store.write_report(report)
    print()
    print(format_report(report, compact=True))
    return 0


def cmd_judge(args: argparse.Namespace, config: Config) -> int:
    root = DataRoot(config.data_dir)
    if not root.exists(args.campaign):
        print(f"unknown campaign {args.campaign}", file=sys.stderr)
        return 1
    store = root.campaign(args.campaign)
    manifest = store.load()
    if args.concurrency:
        config.llm.concurrency = args.concurrency
    if args.rps is not None:
        config.llm.requests_per_second = args.rps
    try:
        summary = asyncio.run(
            judge_campaign_entry(
                store,
                manifest,
                config,
                scenarios=args.scenarios,
                rounds=args.rounds,
                votes=args.votes,
                model=args.model,
                force=args.force,
                limit=args.limit,
            )
        )
    except ConfigError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    print()
    print(
        f"judged {summary['judged']} attempt(s): {summary['passed']} pass, {summary['failed']} fail"
        + (f", {summary['with_errors']} with vote errors" if summary["with_errors"] else "")
        + (f", {summary['skipped']} skipped" if summary["skipped"] else "")
    )
    tokens = summary.get("tokens")
    if tokens:
        print(
            f"judge tokens: in {tokens['input_tokens']:,} (cached {tokens['cached_input_tokens']:,}) "
            f"out {tokens['output_tokens']:,} (reasoning {tokens['reasoning_tokens']:,})"
        )
    manifest = store.load()
    report = build_report(store, manifest)
    store.write_report(report)
    print()
    print(format_report(report))
    return 0


def cmd_reaggregate(args: argparse.Namespace, config: Config) -> int:
    """Apply the current score/pass policy to existing structured votes."""
    from .judge.vote import reaggregate_verdict

    root = DataRoot(config.data_dir)
    if not root.exists(args.campaign):
        print(f"unknown campaign {args.campaign}", file=sys.stderr)
        return 1
    store = root.campaign(args.campaign)
    manifest = store.load()
    candidates = []
    skipped = 0
    incompatible = 0
    for ref in manifest.attempts:
        verdict = store.read_verdict(ref.scenario, ref.round)
        if verdict is None:
            if store.read_legacy_verdict(ref.scenario, ref.round) is not None:
                incompatible += 1
            else:
                skipped += 1
            continue
        if verdict.rubric_version != RUBRIC_VERSION:
            incompatible += 1
            continue
        candidates.append((ref, verdict))

    # One campaign must describe one aggregation policy. Abort before writing
    # if any existing verdict cannot be converted campaign-wide.
    if incompatible:
        print(
            f"cannot reaggregate campaign-wide: {incompatible} verdict(s) use a different rubric; "
            "re-judge that campaign instead",
            file=sys.stderr,
        )
        return 1
    if not candidates:
        print("no current-rubric verdicts to reaggregate", file=sys.stderr)
        return 1

    # Validate and compute every replacement before the first disk write. This
    # avoids leaving a campaign half-converted if a stored v4 verdict is
    # malformed or otherwise cannot be reaggregated.
    updates = []
    for ref, verdict in candidates:
        try:
            updated = reaggregate_verdict(
                verdict,
                required=config.required_criteria,
                pass_threshold=config.pass_threshold,
                min_criterion_score=config.min_criterion_score,
            )
        except Exception as exc:
            print(
                f"cannot reaggregate campaign-wide: {ref.key} is incompatible ({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            return 1
        updates.append((ref, verdict, updated))

    changed = 0
    pass_flips = 0
    latest_settings = None
    for ref, verdict, updated in updates:
        store.write_verdict(updated)
        if verdict.passed != updated.passed:
            pass_flips += 1
        ref.our_passed = updated.passed
        ref.our_score = updated.score
        ref.status = "judged"
        ref.error = None if not updated.errors else "; ".join(updated.errors[:2])
        ref.updated_at = utcnow()
        latest_settings = updated.judge
        changed += 1

    if latest_settings is not None:
        manifest.judge = latest_settings
    store.save(manifest)
    store.append_event(
        {
            "type": "campaign_reaggregated",
            "changed": changed,
            "skipped": skipped,
            "incompatible": incompatible,
            "pass_flips": pass_flips,
            "pass_threshold": config.pass_threshold,
            "min_criterion_score": config.min_criterion_score,
        }
    )
    report = build_report(store, manifest)
    store.write_report(report)
    print(
        f"reaggregated {changed} attempt(s) with mean >= {config.pass_threshold:g} "
        f"and criterion floor {config.min_criterion_score}/5; {pass_flips} pass flip(s), {skipped} unjudged skipped"
    )
    print()
    print(format_report(report))
    return 0


async def judge_campaign_entry(store, manifest, config, **kwargs) -> dict[str, Any]:
    from .judge.runner import judge_campaign

    return await judge_campaign(store, manifest, config, progress=_print_progress, **kwargs)


def cmd_report(args: argparse.Namespace, config: Config) -> int:
    root = DataRoot(config.data_dir)
    if not root.exists(args.campaign):
        print(f"unknown campaign {args.campaign}", file=sys.stderr)
        return 1
    store = root.campaign(args.campaign)
    manifest = store.load()
    report = build_report(store, manifest)
    store.write_report(report)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=1))
        return 0

    print(format_report(report))
    if args.scenarios:
        print()
        print(format_scenario_table(report))
    if args.official_diff:
        diff = compare_to_official(store, manifest)
        print()
        rate = diff["agreement_rate"]
        print(f"judge agreement: {rate:.0%} over {diff['attempts']} attempt(s)" if rate is not None else "no comparable attempts")
        for row in diff["disagreements"][: args.limit or 15]:
            print(f"\n  {row['short']} r{row['round']} — {row['agreement']}")
            print(f"    ours:     {row['ours']}")
            print(f"    official: {row['official']}")
    if args.vs:
        if not root.exists(args.vs):
            print(f"unknown comparison campaign {args.vs}", file=sys.stderr)
            return 1
        other_store = root.campaign(args.vs)
        other_manifest = other_store.load()
        comparison = compare_campaigns(store, manifest, other_store, other_manifest)
        print()
        print(f"comparison {args.campaign} → {args.vs}")
        print(
            f"  shared: {comparison['shared_attempts']} attempt(s) over {comparison['shared_scenarios']} scenario(s)"
        )
        print(
            f"  attempts passing: {comparison['base_pass_attempts']} → {comparison['other_pass_attempts']}"
        )
        print(
            f"  majority scenarios: {comparison['base_majority_pass']} → {comparison['other_majority_pass']}"
        )
        print(
            f"  transitions: {len(comparison['fail_to_pass'])} fail→pass, "
            f"{len(comparison['pass_to_fail'])} pass→fail, {comparison['stable']} stable"
        )
        if comparison["mcnemar_p"] is not None:
            print(f"  McNemar exact p = {comparison['mcnemar_p']:.3g}")
        for row in comparison["majority_flips"]:
            print(f"    flip {row['scenario']}: {row['before']} → {row['after']}")
    return 0


def cmd_serve(args: argparse.Namespace, config: Config) -> int:
    from .server import DASHBOARD_DIST, serve

    if not DASHBOARD_DIST.exists():
        print(f"note: dashboard bundle not built ({DASHBOARD_DIST}) — serving the API and a placeholder page")
    print(f"evalkit dashboard on http://{args.host}:{args.port}  (data: {config.data_dir})")
    serve(config, host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to evalkit.toml")
    parser.add_argument("--data", help="override the data directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check config, credentials, wful and existing campaigns")
    doctor.set_defaults(func=cmd_doctor)

    importer = subparsers.add_parser("import", help="import cached `wful eval result --json` payloads")
    importer.add_argument("source", help="file or directory of eval result payloads")
    importer.add_argument("--label", help="human label for the campaign")
    importer.add_argument("--campaign", help="campaign id (default: imported-<timestamp>)")
    importer.add_argument("--agent", help="agent profile to enrich from (activities, traces)")
    importer.add_argument("--no-activities", action="store_true", help="skip agent-side token backfill")
    importer.add_argument("--traces", action="store_true", help="also try to fetch traces (usually expired)")
    importer.add_argument("--concurrency", type=int, default=6)
    importer.add_argument("--notes")
    importer.add_argument("--force", action="store_true", help="overwrite an existing campaign id")
    importer.set_defaults(func=cmd_import)

    runner = subparsers.add_parser("run", help="run scenarios live against a snapshot")
    runner.add_argument("--agent", required=True, help="agent profile from evalkit.toml")
    runner.add_argument("--scenarios", nargs="*", help="scenario slugs or substrings; 'all' for the whole repo")
    runner.add_argument("--batch", help="batch slug in the agent repo (default: the profile's batch)")
    runner.add_argument("--rounds", type=int, help="attempts per scenario")
    runner.add_argument("--concurrency", type=int)
    runner.add_argument("--snapshot-id", help="skip minting and use this snapshot")
    runner.add_argument("--campaign", help="campaign id (default: run-<timestamp>)")
    runner.add_argument("--label")
    runner.add_argument("--limit", type=int, help="only the first N scenarios (smoke runs)")
    runner.add_argument("--votes", type=int, help="judge votes per turn when judging inline")
    runner.add_argument("--no-judge", action="store_true", help="collect only, judge later")
    runner.add_argument("--timeout", type=float, default=900.0, help="per-scenario timeout in seconds")
    runner.add_argument("--resume", help="resume an existing campaign id")
    runner.add_argument("--dry-run", action="store_true")
    runner.set_defaults(func=cmd_run)

    judge = subparsers.add_parser("judge", help="grade a campaign with our rubric")
    judge.add_argument("campaign")
    judge.add_argument("--scenarios", nargs="*", help="only these scenarios (slug or short name)")
    judge.add_argument("--rounds", nargs="*", type=int, help="only these rounds")
    judge.add_argument("--votes", type=int)
    judge.add_argument("--model", help="override the judge model")
    judge.add_argument("--limit", type=int, help="judge at most N attempts (calibration runs)")
    judge.add_argument("--concurrency", type=int, help="concurrent judge LLM calls")
    judge.add_argument("--rps", type=float, help="judge requests per second ceiling (0 disables)")
    judge.add_argument("--force", action="store_true", help="re-judge attempts that already have a verdict")
    judge.set_defaults(func=cmd_judge)

    reaggregate = subparsers.add_parser(
        "reaggregate",
        help="recompute score/pass campaign-wide from stored votes, without calling an LLM",
    )
    reaggregate.add_argument("campaign")
    reaggregate.set_defaults(func=cmd_reaggregate)

    report = subparsers.add_parser("report", help="print a campaign's numbers")
    report.add_argument("campaign")
    report.add_argument("--vs", help="compare against another campaign")
    report.add_argument("--scenarios", action="store_true", help="also print the per-scenario table")
    report.add_argument("--official-diff", action="store_true", help="show where we disagree with the platform judge")
    report.add_argument("--limit", type=int, help="rows to show in the diff")
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    serve_cmd = subparsers.add_parser("serve", help="run the local dashboard")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=4747)
    serve_cmd.set_defaults(func=cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config, data_dir=args.data)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    try:
        return int(args.func(args, config) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted — progress is on disk; re-run with --resume", file=sys.stderr)
        return 130
    except (WfulError, WfulNotAllowed, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
