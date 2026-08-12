# evalkit

Eval kit for Wonderful agents. `wful` is the engine — it runs scenarios, reads
traces, fetches results and activity records. Everything else here is ours:
collection, our own 1–5 rubric with an LLM panel, metrics, and a local dashboard
that drills from a campaign down to a single conversation.

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
cp evalkit.example.toml evalkit.toml    # then point it at your agent
.venv/bin/python -m evalkit.cli doctor          # check wiring, credentials, wful
.venv/bin/python -m evalkit.cli import <dir> --agent vera   # cached results → campaign
.venv/bin/python -m evalkit.cli run --agent vera --rounds 3 # live campaign
.venv/bin/python -m evalkit.cli judge <campaign>            # grade with our rubric
.venv/bin/python -m evalkit.cli reaggregate <campaign>      # recompute pass from stored votes, no LLM calls
.venv/bin/python -m evalkit.cli report <campaign> --vs <other>
.venv/bin/python -m evalkit.cli serve                       # dashboard on :4747
cd dashboard && pnpm install && pnpm build                  # build the UI
```

See `STATE.md` for the current state of the work, the numbers measured so far,
and the open threads. Design notes live at the top of each module.

`data/` holds collected eval data (customer content) and is never committed.

`evalkit.toml` names the agent, the wful profile and the workspace, and both are
passed explicitly on every platform command so a run cannot land in the wrong
tenant. The platform is read-only except `eval run`, enforced by an allowlist in
`evalkit/wful.py`.

The binary pass is only a bridge for comparisons: by default every turn must
average at least 4/5, with no blocking deterministic failure and no catastrophic
1/5 criterion. A 2/5 is still highlighted and lowers the mean, but does not veto
an otherwise strong answer.
