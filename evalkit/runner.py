"""Live campaigns collected directly from snapshot-pinned Chat V3 sessions.

Why orchestrate per scenario instead of running the platform batch:

* **Concurrency is ours.** N scenarios in flight, configurable, with rounds
  interleaved so a slow scenario does not serialise a whole round.
* **Nothing is lost.** Each attempt is persisted the moment it finishes — result,
  trace (fetched immediately, since retention is short), activity record — so a
  crashed or cancelled campaign resumes instead of restarting.
* **Judging is local.** The Wonderful eval/judge APIs are not invoked during
  development runs, so they cannot add cost or truncate a multi-turn scenario.
* **Failures stay scoped.** A scenario that errors never takes the campaign
  down with it.

The platform supplies the agent runtime, immutable snapshot and traces. EvalKit
supplies orchestration, deterministic checks and the LLM judge.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import AgentConfig, Config
from .direct_chat import DirectChatCollector
from .judge.llm import JudgeLLM
from .judge.runner import judge_attempt, load_view
from .schemas import AgentTarget, AttemptRef, CampaignManifest
from .store import CampaignStore, utcnow
from .wful import WfulClient, WfulError

ProgressFn = Callable[[dict], None]


# --------------------------------------------------------------------------
# Scenario discovery (from the agent repo, no platform calls)
# --------------------------------------------------------------------------


def scenarios_from_batch(repo: Path, batch: str) -> list[str]:
    path = repo / "evals" / "batches" / f"{batch}.json"
    if not path.exists():
        raise FileNotFoundError(f"batch file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    slugs = payload.get("scenario_slugs") or payload.get("scenarios") or []
    if not slugs:
        raise ValueError(f"batch {batch} lists no scenario slugs")
    return [str(slug) for slug in slugs]


def scenarios_from_repo(repo: Path) -> list[str]:
    folder = repo / "evals" / "scenarios"
    return sorted(path.stem for path in folder.glob("*.json"))


def resolve_scenarios(agent: AgentConfig, *, explicit: Sequence[str] | None, batch: str | None) -> list[str]:
    repo = Path(agent.repo).expanduser() if agent.repo else None
    if explicit:
        if len(explicit) == 1 and explicit[0] in {"all", "*"}:
            if not repo:
                raise ValueError("cannot expand 'all' without [agents.*].repo")
            return scenarios_from_repo(repo)
        if repo:
            known = set(scenarios_from_repo(repo))
            resolved = []
            for name in explicit:
                if name in known:
                    resolved.append(name)
                    continue
                matches = sorted(slug for slug in known if name in slug)
                if not matches:
                    raise ValueError(f"no scenario in {repo} matches {name!r}")
                resolved.extend(matches)
            return list(dict.fromkeys(resolved))
        return list(explicit)
    chosen_batch = batch or agent.batch
    if chosen_batch and repo:
        return scenarios_from_batch(repo, chosen_batch)
    if repo:
        return scenarios_from_repo(repo)
    raise ValueError("nothing to run: pass --scenarios or configure a batch and repo")


# --------------------------------------------------------------------------
# Snapshot / agent provenance
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def model_at_commit(repo: Path, sha: str | None) -> str | None:
    """Read the agent's chat model from config.json *as of the snapshot commit*.

    The working tree may have moved on since the snapshot was minted, so the
    commit is the only honest source.
    """
    if not sha:
        return None
    raw = _git(repo, "show", f"{sha}:config.json")
    if not raw:
        return None
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return None
    node: Any = config
    for key in ("ai", "llm", "chat", "providers", "openai", "model"):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None


def _extract_snapshot(payload: Any) -> tuple[str | None, str | None]:
    """Pull (snapshot id, commit sha) out of `agents snapshot --json`."""
    if isinstance(payload, str):
        return payload.strip() or None, None
    if not isinstance(payload, dict):
        return None, None
    snapshot_id = None
    for key in ("agent_repo_snapshot_id", "snapshot_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            snapshot_id = value
            break
    sha = None
    for key in ("agent_commit_sha", "commit_sha", "commit", "sha"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            sha = value
            break
    nested = payload.get("snapshot")
    if isinstance(nested, dict) and (snapshot_id is None or sha is None):
        nested_id, nested_sha = _extract_snapshot(nested)
        snapshot_id = snapshot_id or nested_id
        sha = sha or nested_sha
    return snapshot_id, sha


async def mint_snapshot(client: WfulClient, repo: Path | None) -> tuple[str, str | None, Any]:
    payload = await client.snapshot()
    snapshot_id, sha = _extract_snapshot(payload)
    if not snapshot_id:
        raise WfulError(
            f"could not read a snapshot id out of `wful agents snapshot --json`: {payload!r}",
            args_=["agents", "snapshot"],
            returncode=0,
        )
    if not sha and repo:
        sha = _git(repo, "rev-parse", "HEAD")
    return snapshot_id, sha, payload


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------


def build_manifest(
    *,
    campaign_id: str,
    label: str,
    agent: AgentConfig,
    scenarios: Sequence[str],
    rounds: int,
    concurrency: int,
    snapshot_id: str,
    commit_sha: str | None,
    model: str | None,
    batch: str | None,
) -> CampaignManifest:
    from .importers import short_name

    attempts = [
        AttemptRef(scenario=scenario, short=short_name(scenario), round=round_, status="pending")
        for round_ in range(1, rounds + 1)
        for scenario in scenarios
    ]
    return CampaignManifest(
        id=campaign_id,
        label=label,
        kind="live",
        created_at=utcnow(),
        target=AgentTarget(
            slug=agent.slug,
            id=agent.id,
            wful_profile=agent.wful_profile,
            workspace=agent.workspace,
            repo=agent.repo,
        ),
        snapshot_id=snapshot_id,
        agent_commit_sha=commit_sha,
        agent_model=model,
        batch=batch,
        rounds=rounds,
        concurrency=concurrency,
        scenarios=list(scenarios),
        source={
            "kind": "live_run",
            "collector": "wonderful_chat_v3_direct",
            "platform_eval_invoked": False,
            "platform_judge_invoked": False,
        },
        attempts=attempts,
    )


async def run_campaign(
    store: CampaignStore,
    manifest: CampaignManifest,
    config: Config,
    agent: AgentConfig,
    *,
    client: WfulClient,
    collector: DirectChatCollector | None = None,
    judge_inline: bool = True,
    resume: bool = True,
    scenario_timeout: float = 900.0,
    votes: int | None = None,
    progress: ProgressFn | None = None,
) -> CampaignManifest:
    """Execute every pending attempt through Chat V3, persisting as it goes."""
    concurrency = manifest.concurrency or agent.concurrency
    semaphore = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()

    llm: JudgeLLM | None = None
    llm_semaphore: asyncio.Semaphore | None = None
    judge_votes = votes or config.llm.votes
    if judge_inline:
        from .config import load_llm_credentials

        llm = JudgeLLM(
            credentials=load_llm_credentials(config.llm, config.llm.model),
            model=config.llm.model,
            reasoning_effort=config.llm.reasoning_effort,
            max_retries=config.llm.max_retries,
            requests_per_second=config.llm.requests_per_second,
            max_output_tokens=config.llm.max_output_tokens,
        )
        llm_semaphore = asyncio.Semaphore(max(1, config.llm.concurrency))

    todo = [
        ref
        for ref in manifest.attempts
        if not (resume and store.attempt_paths(ref.scenario, ref.round).exists())
    ]
    owns_collector = collector is None
    if collector is None and todo:
        collector = await DirectChatCollector.create(
            agent,
            client,
            snapshot_id=manifest.snapshot_id or "",
        )
    state = {"done": 0, "errors": 0, "passed": 0}
    store.append_event(
        {
            "type": "campaign_started",
            "attempts": len(todo),
            "total": len(manifest.attempts),
            "concurrency": concurrency,
            "snapshot_id": manifest.snapshot_id,
            "judge_inline": judge_inline,
            "collector": "wonderful_chat_v3_direct",
            "platform_judge_invoked": False,
        }
    )
    if progress:
        progress({"event": "campaign_started", "attempts": len(todo), "concurrency": concurrency})

    async def execute(ref: AttemptRef) -> None:
        async with semaphore:
            async with lock:
                ref.status = "running"
                store.update_attempt(manifest, ref)
                store.append_event(
                    {"type": "attempt_started", "scenario": ref.scenario, "short": ref.short, "round": ref.round}
                )
            if progress:
                progress({"event": "attempt_started", "scenario": ref.short, "round": ref.round})

            started = utcnow()
            meta: dict[str, Any] = {
                "started_at": started,
                "snapshot_id": manifest.snapshot_id,
                "collector": "wonderful_chat_v3_direct",
                "platform_eval_invoked": False,
                "platform_judge_invoked": False,
            }
            try:
                if collector is None:
                    raise RuntimeError("direct chat collector is not initialized")
                result = await collector.collect(ref.scenario, timeout=scenario_timeout)
                store.write_result(ref.scenario, ref.round, result)
                ref.result_id = result.get("_result_id") or result.get("id")
                ref.run_id = None
                ref.communication_id = result.get("communication_id")
                ref.official_passed = None
                ref.execution_time_ms = result.get("execution_time_ms")
                ref.status = "collected"

                # Traces expire: fetch now or never.
                if ref.communication_id:
                    try:
                        trace = await client.trace(str(ref.communication_id))
                    except WfulError as exc:
                        trace = None
                        meta["trace_error"] = str(exc)
                    if trace is None:
                        store.write_trace(
                            ref.scenario,
                            ref.round,
                            {"missing": "no agent trace recorded at fetch time"},
                        )
                        ref.trace_available = False
                    else:
                        store.write_trace(ref.scenario, ref.round, trace)
                        ref.trace_available = True
                    activity = await client.activity(str(ref.communication_id))
                    if activity is not None:
                        store.write_activity(ref.scenario, ref.round, activity)

                meta["finished_at"] = utcnow()
                store.write_meta(ref.scenario, ref.round, meta)

                if llm is not None:
                    view = load_view(store, ref, manifest.id)
                    if view is not None:
                        verdict = await judge_attempt(
                            view,
                            llm,
                            votes=judge_votes,
                            required=config.required_criteria,
                            reasoning_effort=config.llm.reasoning_effort,
                            llm_semaphore=llm_semaphore,
                            pass_threshold=config.pass_threshold,
                            min_criterion_score=config.min_criterion_score,
                        )
                        store.write_verdict(verdict)
                        ref.our_passed = verdict.passed
                        ref.our_score = verdict.score
                        ref.status = "judged"
            except Exception as exc:  # keep the campaign alive; the attempt records why
                ref.status = "error"
                ref.error = f"{type(exc).__name__}: {exc}"[:400]
                meta["error"] = ref.error
                store.write_meta(ref.scenario, ref.round, meta)

            async with lock:
                store.update_attempt(manifest, ref)
                state["done"] += 1
                state["errors"] += 1 if ref.status == "error" else 0
                state["passed"] += 1 if ref.our_passed else 0
                store.append_event(
                    {
                        "type": "attempt_finished",
                        "scenario": ref.scenario,
                        "short": ref.short,
                        "round": ref.round,
                        "status": ref.status,
                        "official_passed": ref.official_passed,
                        "our_passed": ref.our_passed,
                        "error": ref.error,
                        "done": state["done"],
                        "total": len(todo),
                    }
                )
            if progress:
                progress(
                    {
                        "event": "attempt_finished",
                        "scenario": ref.short,
                        "round": ref.round,
                        "status": ref.status,
                        "official": ref.official_passed,
                        "ours": ref.our_passed,
                        "error": ref.error,
                        "done": state["done"],
                        "total": len(todo),
                    }
                )

    try:
        await asyncio.gather(*(execute(ref) for ref in todo), return_exceptions=True)
    finally:
        if owns_collector and collector is not None:
            await collector.aclose()
    store.append_event({"type": "campaign_finished", **state})
    if progress:
        progress({"event": "campaign_finished", **state})
    return manifest
