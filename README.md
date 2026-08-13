<div align="center">

# EvalKit

**Run, judge, compare, and inspect evaluations for Wonderful agents.**

A local-first evaluation loop powered by `wful`, deterministic tool checks,
a 1–5 LLM judging panel, and a dashboard that drills from campaign trends down
to individual conversations.

<p>
  <code>Python 3.12+</code> · <code>FastAPI</code> · <code>React</code> · <code>wful</code>
</p>

</div>

<p align="center">
  <img src="docs/assets/evalkit-dashboard.png" alt="EvalKit campaign dashboard with scores, judging progress, criteria, and failure tags" width="1200">
  <br>
  <sub><strong>Full-suite campaign:</strong> live judging progress, criteria scores, and failure taxonomy.</sub>
</p>

## What it does

- Runs live scenario campaigns through `wful` or imports cached results.
- Collects traces, result payloads, activity records, and token usage.
- Enforces deterministic tool contracts before an LLM judge runs.
- Grades each attempt with a multi-criterion 1–5 rubric and stores every vote.
- Compares campaigns and the platform evaluator without letting either overwrite
  the underlying evidence.
- Serves a live, read-only dashboard for campaign and conversation-level analysis.

## Quick start

Create the environment and configure the agent, workspace, and `wful` profile:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp evalkit.example.toml evalkit.toml
.venv/bin/python -m evalkit.cli doctor
```

Run a live campaign, or turn cached platform results into one:

```bash
.venv/bin/python -m evalkit.cli run --agent vera --rounds 3
.venv/bin/python -m evalkit.cli import <dir> --agent vera
```

Judge, reaggregate, and compare campaigns:

```bash
.venv/bin/python -m evalkit.cli judge <campaign>
.venv/bin/python -m evalkit.cli reaggregate <campaign>
.venv/bin/python -m evalkit.cli report <campaign> --vs <other>
```

Build and launch the dashboard at [http://localhost:4747](http://localhost:4747):

```bash
cd dashboard && pnpm install && pnpm build && cd ..
.venv/bin/python -m evalkit.cli serve
```

## How scoring works

The binary pass is a bridge for comparisons, not the source of truth. By default,
every turn must average at least 4/5, with no blocking deterministic failure and
no catastrophic 1/5 criterion. A 2/5 is still highlighted and lowers the mean,
but does not veto an otherwise strong answer.

`evalkit.toml` names the agent, `wful` profile, and workspace. EvalKit passes them
explicitly on every platform command so a run cannot land in the wrong tenant.
The platform is read-only except for `eval run`, enforced by an allowlist in
`evalkit/wful.py`.

## Deterministic tool contracts (v5)

Scenarios may add `metadata.evalkit_v5` to a turn. EvalKit enforces these fields
before the LLM judge runs:

```json
{
  "response_mode": "answer",
  "required_tools": ["get_line_usage", "lookup_tim_info"],
  "forbidden_tools": ["get_line_context"],
  "tool_sequence": ["get_line_usage", "lookup_tim_info"],
  "tool_cardinality": {
    "get_line_usage": { "min": 1, "max": 1 },
    "lookup_tim_info": { "min": 1, "max": 1 }
  }
}
```

`expected_input` on a tool mock is checked structurally (`{}` therefore means
the tool must receive no arguments). Repeated mocks for the same tool are
matched and consumed independently, so order and multiplicity are observable.

<p align="center">
  <img src="docs/assets/evalkit-api-gate-dashboard.png" alt="EvalKit API regression gate dashboard showing 16 passing scenarios across three rounds" width="1200">
  <br>
  <sub><strong>API regression gate:</strong> 16 scenarios across 3 rounds, with deterministic checks and per-scenario drill-down.</sub>
</p>

For Vera the default is the full 115-scenario suite (70 knowledge/customer-care
+ 45 API scenarios), 3 rounds, execution concurrency 24 and judge concurrency
64 under the configured rate limiter. The platform is used only to execute the
agent and collect artifacts; its evaluator verdict is retained for comparison
but never determines EvalKit's score.

## Repository notes

- `STATE.md` tracks measured results, current work, and open threads.
- `data/` contains collected customer data and is never committed.
- Design notes live at the top of each module.
