"""Thin, allowlisted wrapper around the `wful` CLI.

Design rules, all enforced here rather than trusted to callers:

1. **Allowlist.** Only context/provenance reads and snapshot creation are
   available. Platform ``eval run`` is deliberately forbidden: iterative
   execution goes through the direct Chat V3 collector and judging is local.
2. **Explicit context.** Every call carries `--profile`, `--workspace` and
   `--agent` (plus `--json --no-interactive`), so a shell's active profile can
   never silently retarget a run.
3. **Strict payloads.** A missing/unparseable JSON payload is a failure. Exit
   code 2 means the CLI was invoked wrong and the payload is help text on
   stderr.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import Any, Sequence

# (command, subcommand) pairs evalkit may run. Everything else is refused.
ALLOWED_COMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("agents", "snapshot"),
        ("agents", "get"),
        ("agents", "list"),
        ("traces", "call"),
        ("traces", "exists"),
        ("activities", "get"),
        ("whoami", ""),
    }
)

# Commands that reject --agent (verified against `wful <cmd> --help`).
# `agents snapshot` infers the agent from the clone it runs in.
NO_AGENT_FLAG: frozenset[tuple[str, str]] = frozenset(
    {
        ("agents", "list"),
        ("agents", "snapshot"),
        ("traces", "call"),
        ("traces", "exists"),
        ("activities", "get"),
        ("whoami", ""),
    }
)


class WfulError(RuntimeError):
    """A wful invocation that produced no usable payload."""

    def __init__(self, message: str, *, args_: Sequence[str], returncode: int, stderr: str = "") -> None:
        super().__init__(message)
        self.args_ = list(args_)
        self.returncode = returncode
        self.stderr = stderr


class WfulNotAllowed(RuntimeError):
    pass


@dataclass
class WfulClient:
    """Runs wful for one agent in one workspace."""

    profile: str
    workspace: str
    agent: str | None = None
    cwd: str | None = None
    binary: str = "wful"

    def __post_init__(self) -> None:
        if shutil.which(self.binary) is None:
            raise WfulError(
                f"{self.binary!r} not found on PATH — install the Wonderful CLI first",
                args_=[],
                returncode=127,
            )

    def _build(self, args: Sequence[str], *, agent: str | None = None) -> list[str]:
        if not args:
            raise WfulNotAllowed("no wful command given")
        command = args[0]
        sub = args[1] if len(args) > 1 and not args[1].startswith("-") else ""
        if (command, sub) not in ALLOWED_COMMANDS:
            raise WfulNotAllowed(
                f"wful {command} {sub}".strip()
                + " is not allowed by evalkit (allowed: "
                + ", ".join(sorted(f"{c} {s}".strip() for c, s in ALLOWED_COMMANDS))
                + ")"
            )
        cmd = [self.binary, *args, "--profile", self.profile]
        if self.workspace:
            cmd += ["--workspace", self.workspace]
        target = agent or self.agent
        if target and (command, sub) not in NO_AGENT_FLAG:
            cmd += ["--agent", target]
        cmd += ["--json", "--no-interactive"]
        return cmd

    async def run_json(
        self,
        args: Sequence[str],
        *,
        agent: str | None = None,
        timeout: float | None = 300.0,
        allow_exit_one: bool = False,
    ) -> Any:
        """Run a wful command and return its parsed JSON payload."""
        cmd = self._build(args, agent=agent)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise WfulError(
                f"wful {' '.join(args)} timed out after {timeout}s",
                args_=args,
                returncode=-1,
            ) from None

        out = (out_b or b"").decode("utf-8", "replace").strip()
        err = (err_b or b"").decode("utf-8", "replace").strip()
        code = proc.returncode or 0

        payload = _extract_json(out)
        if payload is not None and (code == 0 or (code == 1 and allow_exit_one)):
            return payload
        if code == 2:
            raise WfulError(
                f"wful {' '.join(args)} was invoked wrong (exit 2); CLI help follows",
                args_=args,
                returncode=code,
                stderr=err,
            )
        raise WfulError(
            f"wful {' '.join(args)} failed (exit {code}) with no JSON payload",
            args_=args,
            returncode=code,
            stderr=err or out,
        )

    async def probe(self, args: Sequence[str], *, agent: str | None = None, timeout: float = 60.0) -> tuple[int, str, str]:
        """Run a command and hand back (exit code, stdout, stderr) unparsed.

        Used for commands whose "no" answer is a plain sentence, e.g.
        `traces exists`.
        """
        cmd = self._build(args, agent=agent)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            (out_b or b"").decode("utf-8", "replace").strip(),
            (err_b or b"").decode("utf-8", "replace").strip(),
        )

    # -- convenience wrappers ------------------------------------------------

    async def snapshot(self, *, timeout: float = 120.0) -> Any:
        return await self.run_json(["agents", "snapshot"], timeout=timeout)

    async def whoami(self, *, timeout: float = 60.0) -> Any:
        return await self.run_json(["whoami"], timeout=timeout)

    async def agent_details(self, ref: str, *, timeout: float = 60.0) -> Any:
        return await self.run_json(["agents", "get"], agent=ref, timeout=timeout)

    async def activity(self, communication_id: str, *, timeout: float = 120.0) -> Any | None:
        """Fetch the communication record: agent-side token usage (incl. cache),
        `agent_version` with its commit, and the tool mocks actually applied.

        Unlike traces, activities are permanent — this works on old attempts too.
        """
        try:
            return await self.run_json(["activities", "get", communication_id], timeout=timeout)
        except WfulError:
            return None

    async def trace(self, communication_id: str, *, timeout: float = 120.0) -> Any | None:
        """Fetch a trace, or return None when the platform has none recorded.

        Trace retention is short, so a missing trace is an expected outcome, not
        an error: the caller records the reason and moves on.
        """
        code, out, err = await self.probe(["traces", "call", communication_id], timeout=timeout)
        payload = _extract_json(out)
        if payload is not None:
            return payload
        message = err or out
        if "no agent trace recorded" in message.lower():
            return None
        raise WfulError(
            f"wful traces call {communication_id} failed (exit {code})",
            args_=["traces", "call", communication_id],
            returncode=code,
            stderr=message,
        )


def _extract_json(text: str) -> Any | None:
    """Parse stdout as JSON, tolerating leading log lines before the payload."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
