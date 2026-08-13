---
name: evalkit
description: Run, judge, resume, and report Wonderful Agent Studio development evaluations with the local EvalKit collector. Use when testing any Wonderful agent change, running targeted scenarios or batch regressions, inspecting snapshot-pinned chat and tool behavior, comparing campaigns, or deciding when a final Wonderful platform eval is warranted. EvalKit directly runs configured Wonderful agents and uses its own LLM judge; Wonderful eval runs are reserved for the final agent-completeness gate.
---

# EvalKit

Use EvalKit as the default development and testing loop for any repository-owned Wonderful agent. Open snapshot-pinned Wonderful Chat V3 sessions, install authored start and per-turn tool mocks, collect every turn, fetch activity/trace evidence, run deterministic checks, and judge locally.

Do not invoke `wful eval run` or `controller-cli evals run` during iterative development. Those commands execute the Wonderful platform judge; use them only for the final agent-completeness gate.

## Locate and check EvalKit

Locate the standalone EvalKit checkout configured for the workspace. Do not assume a particular agent repository, slug, or profile.

```bash
cd <evalkit-checkout>
.venv/bin/python -m evalkit.cli doctor
```

Read `evalkit.toml` to identify the configured agent profiles. Stop if the selected profile, agent repository, Wonderful login, workspace, snapshot access, or local judge credentials are wrong. Never print or copy credentials from `~/.wful/profiles` or environment files.

## Development workflow

1. Inspect the target agent worktree and preserve unrelated changes.
2. Author or update narrow scenarios and scoped tool mocks.
3. Run `wful validate` in the target agent repository.
4. Commit and push the exact revision under test, then run `wful validate --remote`.
5. Mint one snapshot and reuse its ID for the controlled comparison.
6. Select the matching EvalKit agent profile and run a one-round targeted campaign.
7. Fix the agent or scenario from transcript, deterministic, tool-contract, and local judge evidence.
8. Re-run targeted cases, then the relevant batch with multiple rounds.
9. Run one Wonderful eval only after the EvalKit development gate passes and final platform completeness validation is in scope.

The snapshot is immutable. After the target agent commit changes, mint a new snapshot and start a new campaign; never label two revisions as one comparison.

## Run and judge locally

From the target agent repository, mint the snapshot:

```bash
SNAPSHOT_ID="$(wful agents snapshot)"
```

From the standalone EvalKit checkout, run a targeted case and then the relevant regression batch:

```bash
.venv/bin/python -m evalkit.cli run \
  --agent <agent-profile> \
  --scenarios <scenario-slug> \
  --snapshot-id "$SNAPSHOT_ID" \
  --rounds 1 \
  --concurrency 1 \
  --campaign <campaign-id>

.venv/bin/python -m evalkit.cli run \
  --agent <agent-profile> \
  --batch <batch-slug> \
  --snapshot-id "$SNAPSHOT_ID" \
  --rounds 3 \
  --campaign <batch-campaign-id>
```

`evalkit run` does not call the Wonderful eval or judge APIs. `official=—` is expected. Use `ours` as the development verdict; it combines EvalKit's deterministic checks and local LLM votes.

To separate agent collection from local judging:

```bash
.venv/bin/python -m evalkit.cli run \
  --agent <agent-profile> \
  --scenarios <scenario-slug> \
  --snapshot-id "$SNAPSHOT_ID" \
  --campaign <campaign-id> \
  --no-judge

.venv/bin/python -m evalkit.cli judge <campaign-id>
```

Use collection-only mode to calibrate or change the local judge while reusing an identical transcript. A collection-only campaign is not a completed development verdict.

## Resume, inspect, and compare

```bash
.venv/bin/python -m evalkit.cli run --agent <agent-profile> --resume <campaign-id>
.venv/bin/python -m evalkit.cli report <campaign-id> --scenarios
.venv/bin/python -m evalkit.cli report <new-campaign-id> --vs <baseline-campaign-id>
```

Resume only missing attempts from the same campaign/snapshot. Create a new campaign for a new commit, scenario definition, or comparison cohort.

Inspect `data/<campaign-id>/attempts/<scenario>/<round>/` when summaries are insufficient:

- `result.json`: direct Chat V3 transcript and complete tool payloads.
- `activity.json`: permanent activity and agent token usage.
- `trace.json`: short-lived prompt, routing, model, and span evidence.
- `verdict.json`: deterministic checks and local LLM votes.
- `meta.json`: collector, snapshot, timings, and errors.

## Final Wonderful completeness gate

After the pushed revision validates remotely and the targeted plus relevant EvalKit regression campaigns pass, run the smallest Wonderful scenario or batch that represents final completeness, pinned to the same snapshot:

```bash
wful eval run <final-scenario-slug> --snapshot-id "$SNAPSHOT_ID"
# Or one representative final batch:
wful eval run <final-batch-slug> --batch --snapshot-id "$SNAPSHOT_ID"
```

Use `wful eval status <run-id>` and `wful eval result <result-id> --show-all-turns` only to inspect the final gate. If it reveals a real defect, return to EvalKit for the fix; do not turn repeated Wonderful eval runs into the debugging loop.

Report both artifacts at handoff:

- EvalKit campaign ID, target agent profile, snapshot ID, rounds, and local pass/score summary.
- Wonderful run/result ID and official verdict, when the final gate was requested or required.

## Guardrails

- Keep tool calls mocked unless the scenario intentionally declares a disabled passthrough mock and the real operation is safe.
- Never weaken an expectation merely to obtain a pass; correct stale ground truth or misplaced fields explicitly.
- Preserve multi-turn scenarios in full. EvalKit intentionally does not let a Wonderful judge failure truncate later turns.
- Do not compare campaigns from different snapshots as if they isolated one code change.
- Do not hard-code one agent's paths, slug, scenarios, batches, or profile into this skill.
- Do not claim a Wonderful official verdict for a direct campaign; `passed: null` and `official=—` are by design.
