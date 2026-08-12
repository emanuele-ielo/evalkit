"""Import eval results we already have into a campaign.

The kit's first useful act is not running anything: it is taking the attempts
already sitting on disk (or reachable by id) and turning them into a campaign you
can judge, measure and browse. Activities are backfilled while importing —
they are permanent, so even year-old attempts regain their agent-side token usage
and the commit their agent was built from. Traces usually will not come back
(short retention); when they do not, the reason is recorded on the attempt.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import AgentConfig
from .schemas import AgentTarget, AttemptRef, CampaignManifest
from .store import CampaignStore, utcnow
from .wful import WfulClient, WfulError

ProgressFn = Callable[[dict], None]

# `g001`, `s12`, … the short handle scenario families use in practice.
SHORT_RE = re.compile(r"([a-z]\d{2,})")
# `g001_2.json` → scenario g001, round 2
FILENAME_RE = re.compile(r"^(?P<short>.+?)[_-](?P<round>\d+)$")


def short_name(scenario_slug: str, filename: str | None = None) -> str:
    match = SHORT_RE.search(scenario_slug.lower())
    if match:
        return match.group(1)
    if filename:
        stem = Path(filename).stem
        parsed = FILENAME_RE.match(stem)
        if parsed:
            return parsed.group("short")
        return stem
    return scenario_slug


def _round_from_filename(filename: str) -> int | None:
    parsed = FILENAME_RE.match(Path(filename).stem)
    if not parsed:
        return None
    try:
        return int(parsed.group("round"))
    except ValueError:
        return None


def _load_result_files(source: Path) -> list[tuple[Path, dict[str, Any]]]:
    if source.is_file():
        paths = [source]
    else:
        paths = sorted(p for p in source.glob("*.json") if p.is_file())
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("scenario_slug"):
            loaded.append((path, payload))
    return loaded


def _assign_rounds(entries: list[tuple[Path, dict[str, Any]]]) -> list[tuple[Path, dict[str, Any], int]]:
    """Round number from the filename when it carries one, else by created_at."""
    by_scenario: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path, payload in entries:
        by_scenario.setdefault(payload["scenario_slug"], []).append((path, payload))

    assigned: list[tuple[Path, dict[str, Any], int]] = []
    for scenario, group in by_scenario.items():
        explicit = {path: _round_from_filename(path.name) for path, _ in group}
        if all(value is not None for value in explicit.values()) and len({*explicit.values()}) == len(group):
            for path, payload in group:
                assigned.append((path, payload, explicit[path] or 1))
            continue
        ordered = sorted(group, key=lambda item: str(item[1].get("created_at") or item[0].name))
        for index, (path, payload) in enumerate(ordered, start=1):
            assigned.append((path, payload, index))
    return assigned


async def import_results(
    store: CampaignStore,
    source: Path,
    *,
    label: str,
    agent: AgentConfig | None = None,
    client: WfulClient | None = None,
    fetch_activities: bool = True,
    fetch_traces: bool = False,
    concurrency: int = 6,
    progress: ProgressFn | None = None,
    notes: str | None = None,
) -> CampaignManifest:
    """Build a campaign from cached `wful eval result --json` payloads."""
    entries = _load_result_files(source)
    if not entries:
        raise FileNotFoundError(f"no eval result payloads found in {source}")
    assigned = _assign_rounds(entries)

    refs: list[AttemptRef] = []
    snapshots: set[str] = set()
    agent_ids: set[str] = set()
    versions: set[str] = set()

    semaphore = asyncio.Semaphore(max(1, concurrency))
    counters = {"activities": 0, "traces": 0, "trace_missing": 0}

    async def ingest(path: Path, payload: dict[str, Any], round_: int) -> AttemptRef:
        scenario = str(payload["scenario_slug"])
        short = short_name(scenario, path.name)
        store.write_result(scenario, round_, payload)

        communication_id = payload.get("communication_id")
        trace_available: bool | None = None
        meta: dict[str, Any] = {
            "imported_from": str(path),
            "imported_at": utcnow(),
            "result_id": payload.get("_result_id") or payload.get("id"),
            "communication_id": communication_id,
        }

        if client and communication_id:
            async with semaphore:
                if fetch_activities:
                    activity = await client.activity(str(communication_id))
                    if activity is not None:
                        store.write_activity(scenario, round_, activity)
                        counters["activities"] += 1
                        version = activity.get("agent_version")
                        if version:
                            versions.add(str(version))
                if fetch_traces:
                    try:
                        trace = await client.trace(str(communication_id))
                    except WfulError as exc:
                        trace = None
                        meta["trace_error"] = str(exc)
                    if trace is None:
                        store.write_trace(
                            scenario,
                            round_,
                            {"missing": "no agent trace recorded (trace retention is short)"},
                        )
                        trace_available = False
                        counters["trace_missing"] += 1
                    else:
                        store.write_trace(scenario, round_, trace)
                        trace_available = True
                        counters["traces"] += 1

        store.write_meta(scenario, round_, meta)

        if payload.get("agent_repo_snapshot_id"):
            snapshots.add(str(payload["agent_repo_snapshot_id"]))
        if payload.get("agent_id"):
            agent_ids.add(str(payload["agent_id"]))

        ref = AttemptRef(
            scenario=scenario,
            short=short,
            round=round_,
            status="collected",
            result_id=payload.get("_result_id") or payload.get("id"),
            communication_id=communication_id,
            official_passed=payload.get("passed"),
            trace_available=trace_available,
            execution_time_ms=payload.get("execution_time_ms"),
            updated_at=utcnow(),
        )
        if progress:
            progress({"event": "imported", "scenario": short, "round": round_})
        return ref

    refs = list(await asyncio.gather(*(ingest(path, payload, round_) for path, payload, round_ in assigned)))
    refs.sort(key=lambda ref: (ref.scenario, ref.round))

    scenarios = sorted({ref.scenario for ref in refs})
    manifest = CampaignManifest(
        id=store.id,
        label=label,
        kind="imported",
        created_at=utcnow(),
        target=(
            AgentTarget(
                slug=agent.slug,
                id=agent.id or (next(iter(agent_ids)) if len(agent_ids) == 1 else None),
                wful_profile=agent.wful_profile,
                workspace=agent.workspace,
                repo=agent.repo,
            )
            if agent
            else None
        ),
        snapshot_id=next(iter(snapshots)) if len(snapshots) == 1 else None,
        agent_model=None,
        agent_commit_sha=None,
        rounds=max((ref.round for ref in refs), default=1),
        scenarios=scenarios,
        source={
            "kind": "cached_eval_results",
            "path": str(source),
            "files": len(assigned),
            "snapshots": sorted(snapshots),
            "agent_versions": sorted(versions),
            "activities_fetched": counters["activities"],
            "traces_fetched": counters["traces"],
            "traces_missing": counters["trace_missing"],
        },
        notes=notes,
        attempts=refs,
    )
    store.save(manifest)
    store.append_event(
        {
            "type": "campaign_imported",
            "attempts": len(refs),
            "scenarios": len(scenarios),
            "activities": counters["activities"],
            "traces": counters["traces"],
        }
    )
    return manifest
