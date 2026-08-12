"""Local dashboard server: a JSON API over `data/`, plus the built SPA.

Read-only by construction — it never runs evals and never calls the platform, so
leaving it open in a tab while a campaign runs is safe. Progress arrives over SSE
by tailing each campaign's `events.jsonl`, which is also what makes the live view
work without a database.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Config
from .judge.runner import load_view
from .report import build_report, compare_campaigns, compare_to_official
from .store import CampaignStore, DataRoot

DASHBOARD_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"

PLACEHOLDER = """<!doctype html>
<html><head><meta charset="utf-8"><title>evalkit</title>
<style>body{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:46rem;margin:12vh auto;padding:0 1.5rem;color:#111}
code{background:#f4f4f5;padding:.15rem .35rem;border-radius:.25rem}</style></head>
<body><h1>evalkit</h1>
<p>The API is running, but the dashboard bundle has not been built yet:</p>
<pre><code>cd dashboard && pnpm install && pnpm build</code></pre>
<p>Then reload this page. The JSON API is available meanwhile at <code>/api/campaigns</code>.</p>
</body></html>
"""


def _campaign_summary(store: CampaignStore, manifest: Any) -> dict[str, Any]:
    report = store.read_report()
    counts = {"judged": 0, "collected": 0, "running": 0, "error": 0, "pending": 0}
    for ref in manifest.attempts:
        counts[ref.status] = counts.get(ref.status, 0) + 1
    return {
        "id": manifest.id,
        "label": manifest.label,
        "kind": manifest.kind,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "agent": manifest.target.slug if manifest.target else None,
        "agent_model": manifest.agent_model,
        "snapshot_id": manifest.snapshot_id,
        "rounds": manifest.rounds,
        "scenarios": len(manifest.scenarios) or len({ref.scenario for ref in manifest.attempts}),
        "attempts": len(manifest.attempts),
        "status_counts": counts,
        "judge": manifest.judge.model_dump() if manifest.judge else None,
        "report": (
            {
                "mean_score": report.mean_score,
                "our_majority_pass": report.our_majority_pass,
                "official_majority_pass": report.official_majority_pass,
                "scenarios_total": report.scenarios_total,
                "our_pass_attempts": report.our_pass_attempts,
                "attempts_judged": report.attempts_judged,
                "generated_at": report.generated_at,
            }
            if report
            else None
        ),
    }


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="evalkit", version="0.1.0", docs_url="/api/docs")
    root = DataRoot(config.data_dir)

    def campaign_or_404(campaign_id: str) -> tuple[CampaignStore, Any]:
        store = root.campaign(campaign_id)
        try:
            return store, store.load()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"unknown campaign {campaign_id}") from None

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "data_dir": str(config.data_dir), "campaigns": len(root.list_campaigns())}

    @app.get("/api/campaigns")
    def list_campaigns() -> list[dict[str, Any]]:
        return [_campaign_summary(store, manifest) for store, manifest in root.iter_manifests()]

    @app.get("/api/campaigns/{campaign_id}")
    def get_campaign(campaign_id: str, refresh: bool = Query(default=False)) -> dict[str, Any]:
        store, manifest = campaign_or_404(campaign_id)
        report = store.read_report()
        if report is None or refresh:
            report = build_report(store, manifest)
            store.write_report(report)
        rounds = sorted({ref.round for ref in manifest.attempts})
        matrix: dict[str, dict[str, Any]] = {}
        for ref in manifest.attempts:
            row = matrix.setdefault(
                ref.scenario,
                {"scenario": ref.scenario, "short": ref.short, "cells": {}},
            )
            row["cells"][str(ref.round)] = {
                "status": ref.status,
                "score": ref.our_score,
                "ours": ref.our_passed,
                "official": ref.official_passed,
                "error": ref.error,
                "trace": ref.trace_available,
                "ms": ref.execution_time_ms,
            }
        return {
            "manifest": manifest.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
            "rounds": rounds,
            "matrix": sorted(matrix.values(), key=lambda row: row["short"]),
        }

    @app.get("/api/campaigns/{campaign_id}/attempts/{scenario}/{round_}")
    def get_attempt(campaign_id: str, scenario: str, round_: int) -> dict[str, Any]:
        store, manifest = campaign_or_404(campaign_id)
        ref = manifest.attempt(scenario, round_)
        if ref is None:
            raise HTTPException(status_code=404, detail="unknown attempt")
        view = load_view(store, ref, manifest.id)
        if view is None:
            raise HTTPException(status_code=404, detail="attempt has no result payload yet")
        verdict = store.read_verdict(scenario, round_)
        paths = store.attempt_paths(scenario, round_)
        siblings = sorted(
            (
                {
                    "scenario": other.scenario,
                    "short": other.short,
                    "round": other.round,
                    "ours": other.our_passed,
                    "score": other.our_score,
                    "official": other.official_passed,
                    "status": other.status,
                }
                for other in manifest.attempts
            ),
            key=lambda item: (item["short"], item["round"]),
        )
        return {
            "view": view.model_dump(mode="json"),
            "verdict": verdict.model_dump(mode="json") if verdict else None,
            "legacy_verdict": store.read_legacy_verdict(scenario, round_) if verdict is None else None,
            "meta": store.read_meta(scenario, round_),
            "raw": {
                "result": paths.result.exists(),
                "trace": paths.trace.exists(),
                "activity": paths.activity.exists(),
                "verdict": paths.verdict.exists(),
            },
            "siblings": siblings,
            "campaign": {"id": manifest.id, "label": manifest.label, "rounds": manifest.rounds},
        }

    @app.get("/api/campaigns/{campaign_id}/attempts/{scenario}/{round_}/raw/{kind}")
    def get_raw(campaign_id: str, scenario: str, round_: int, kind: str) -> JSONResponse:
        store, _ = campaign_or_404(campaign_id)
        paths = store.attempt_paths(scenario, round_)
        target = {"result": paths.result, "trace": paths.trace, "activity": paths.activity, "verdict": paths.verdict}.get(kind)
        if target is None or not target.exists():
            raise HTTPException(status_code=404, detail=f"no {kind} payload for this attempt")
        return JSONResponse(content=json.loads(target.read_text(encoding="utf-8")))

    @app.get("/api/campaigns/{campaign_id}/official-diff")
    def official_diff(campaign_id: str) -> dict[str, Any]:
        store, manifest = campaign_or_404(campaign_id)
        return compare_to_official(store, manifest)

    @app.get("/api/compare")
    def compare(base: str, other: str) -> dict[str, Any]:
        base_store, base_manifest = campaign_or_404(base)
        other_store, other_manifest = campaign_or_404(other)
        return compare_campaigns(base_store, base_manifest, other_store, other_manifest)

    @app.get("/api/campaigns/{campaign_id}/events")
    async def events(campaign_id: str) -> StreamingResponse:
        store, _ = campaign_or_404(campaign_id)

        async def stream():
            events_seen, offset = store.read_events()
            for event in events_seen[-50:]:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            idle = 0
            while True:
                new_events, offset_next = store.read_events(offset=offset)
                offset = offset_next
                if new_events:
                    idle = 0
                    for event in new_events:
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    idle += 1
                    if idle % 15 == 0:  # keep proxies and browsers from timing out
                        yield ": keepalive\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if DASHBOARD_DIST.exists():
        app.mount("/assets", StaticFiles(directory=DASHBOARD_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):  # SPA routing: every non-API path serves index.html
            candidate = DASHBOARD_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            # index.html must never be cached: it is the only file whose name
            # does not change between builds, so a cached copy pins the browser
            # to a bundle that no longer exists on disk.
            return FileResponse(
                DASHBOARD_DIST / "index.html",
                headers={"Cache-Control": "no-store, must-revalidate"},
            )

    else:

        @app.get("/{full_path:path}")
        def placeholder(full_path: str):
            from fastapi.responses import HTMLResponse

            return HTMLResponse(PLACEHOLDER)

    return app


def serve(config: Config, *, host: str = "127.0.0.1", port: int = 4747, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
