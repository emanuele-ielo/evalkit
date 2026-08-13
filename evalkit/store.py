"""On-disk layout and the only place that reads or writes it.

    data/<campaign>/campaign.json                     manifest + attempt index
    data/<campaign>/report.json                       last computed report
    data/<campaign>/events.jsonl                      append-only progress log (SSE)
    data/<campaign>/attempts/<scenario>/<round>/
        result.json    direct Chat V3 result, or imported `wful eval result`
        trace.json     raw `wful traces call --json`, or {"missing": "reason"}
        verdict.json   our AttemptVerdict
        meta.json      timings, ids, prompt hashes

Writes are atomic (temp file + replace) so the dashboard can read the tree while
a campaign is running.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .schemas import AttemptRef, AttemptVerdict, CampaignManifest, CampaignReport


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


class AttemptPaths:
    """Where one attempt's four files live."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def result(self) -> Path:
        return self.root / "result.json"

    @property
    def trace(self) -> Path:
        return self.root / "trace.json"

    @property
    def activity(self) -> Path:
        return self.root / "activity.json"

    @property
    def verdict(self) -> Path:
        return self.root / "verdict.json"

    @property
    def meta(self) -> Path:
        return self.root / "meta.json"

    def exists(self) -> bool:
        return self.result.exists()


class CampaignStore:
    """Read/write access to one campaign directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.id = root.name

    # -- paths ---------------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self.root / "campaign.json"

    @property
    def report_path(self) -> Path:
        return self.root / "report.json"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    def attempt_paths(self, scenario: str, round_: int) -> AttemptPaths:
        return AttemptPaths(self.root / "attempts" / scenario / str(round_))

    # -- manifest ------------------------------------------------------------

    def load(self) -> CampaignManifest:
        raw = read_json(self.manifest_path)
        if raw is None:
            raise FileNotFoundError(f"no campaign manifest at {self.manifest_path}")
        return CampaignManifest.model_validate(raw)

    def save(self, manifest: CampaignManifest) -> None:
        manifest.updated_at = utcnow()
        write_json_atomic(self.manifest_path, manifest.model_dump(mode="json"))

    def update_attempt(self, manifest: CampaignManifest, ref: AttemptRef) -> CampaignManifest:
        """Replace an attempt entry in the manifest and persist."""
        ref.updated_at = utcnow()
        for index, existing in enumerate(manifest.attempts):
            if existing.scenario == ref.scenario and existing.round == ref.round:
                manifest.attempts[index] = ref
                break
        else:
            manifest.attempts.append(ref)
        self.save(manifest)
        return manifest

    # -- attempt payloads ----------------------------------------------------

    def write_result(self, scenario: str, round_: int, payload: Any) -> None:
        write_json_atomic(self.attempt_paths(scenario, round_).result, payload)

    def write_trace(self, scenario: str, round_: int, payload: Any) -> None:
        write_json_atomic(self.attempt_paths(scenario, round_).trace, payload)

    def write_activity(self, scenario: str, round_: int, payload: Any) -> None:
        write_json_atomic(self.attempt_paths(scenario, round_).activity, payload)

    def write_meta(self, scenario: str, round_: int, payload: dict[str, Any]) -> None:
        write_json_atomic(self.attempt_paths(scenario, round_).meta, payload)

    def write_verdict(self, verdict: AttemptVerdict) -> None:
        write_json_atomic(
            self.attempt_paths(verdict.scenario, verdict.round).verdict,
            verdict.model_dump(mode="json"),
        )

    def read_result(self, scenario: str, round_: int) -> Any | None:
        return read_json(self.attempt_paths(scenario, round_).result)

    def read_trace(self, scenario: str, round_: int) -> Any | None:
        return read_json(self.attempt_paths(scenario, round_).trace)

    def read_activity(self, scenario: str, round_: int) -> Any | None:
        return read_json(self.attempt_paths(scenario, round_).activity)

    def read_meta(self, scenario: str, round_: int) -> dict[str, Any] | None:
        return read_json(self.attempt_paths(scenario, round_).meta)

    def read_verdict(self, scenario: str, round_: int) -> AttemptVerdict | None:
        raw = read_json(self.attempt_paths(scenario, round_).verdict)
        if raw is None:
            return None
        try:
            return AttemptVerdict.model_validate(raw)
        except Exception:  # a verdict written by an older rubric must not break the UI
            return None

    def read_legacy_verdict(self, scenario: str, round_: int) -> dict[str, Any] | None:
        """A verdict the current schema cannot parse — an older rubric version.

        Returned as a small dict so callers can say so out loud instead of
        reporting the attempt as unjudged.
        """
        raw = read_json(self.attempt_paths(scenario, round_).verdict)
        if raw is None:
            return None
        try:
            AttemptVerdict.model_validate(raw)
            return None
        except Exception:
            return {
                "rubric_version": raw.get("rubric_version"),
                "judge_model": (raw.get("judge") or {}).get("model"),
                "passed": raw.get("passed"),
                "explanation": raw.get("explanation"),
                "taxonomy": raw.get("taxonomy") or [],
            }

    # -- report --------------------------------------------------------------

    def write_report(self, report: CampaignReport) -> None:
        write_json_atomic(self.report_path, report.model_dump(mode="json"))

    def read_report(self) -> CampaignReport | None:
        raw = read_json(self.report_path)
        if raw is None:
            return None
        try:
            return CampaignReport.model_validate(raw)
        except Exception:
            return None

    # -- events (progress stream) -------------------------------------------

    def append_event(self, event: dict[str, Any]) -> None:
        event = {"ts": utcnow(), **event}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, *, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Return events after byte `offset`, plus the new offset (for SSE)."""
        if not self.events_path.exists():
            return [], offset
        with self.events_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            lines = handle.readlines()
            new_offset = handle.tell()
        events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events, new_offset


class DataRoot:
    """The `data/` directory: a collection of campaigns."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def campaign(self, campaign_id: str) -> CampaignStore:
        return CampaignStore(self.path / campaign_id)

    def create(self, campaign_id: str) -> CampaignStore:
        store = self.campaign(campaign_id)
        store.root.mkdir(parents=True, exist_ok=True)
        return store

    def exists(self, campaign_id: str) -> bool:
        return (self.path / campaign_id / "campaign.json").exists()

    def list_campaigns(self) -> list[CampaignStore]:
        if not self.path.exists():
            return []
        stores = [
            CampaignStore(child)
            for child in sorted(self.path.iterdir())
            if child.is_dir() and (child / "campaign.json").exists()
        ]
        stores.sort(key=lambda s: (s.manifest_path.stat().st_mtime), reverse=True)
        return stores

    def iter_manifests(self) -> Iterator[tuple[CampaignStore, CampaignManifest]]:
        for store in self.list_campaigns():
            try:
                yield store, store.load()
            except (FileNotFoundError, ValueError):
                continue
