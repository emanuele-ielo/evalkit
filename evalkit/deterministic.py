"""Checks decided in code, before any LLM sees the attempt.

Anything mechanically verifiable belongs here: it is free, it never flakes, and
it keeps the judge's attention on the part that actually needs judgement. A
blocking check that fails means the turn fails regardless of what the LLM votes.

Supported assertion keys (the corpus only uses `must_not_contain`; the rest are
handled because scenario authors reach for them next):

    must_not_contain: [str]     none of these substrings may appear in the answer
    must_contain:     [str]     all of these substrings must appear
    must_contain_any: [str]     at least one must appear
    regex_must_match: [str]     answer must match every pattern
    regex_must_not_match: [str] answer must match none
"""

from __future__ import annotations

import re

from .schemas import DeterministicCheck, TurnView

# Source/version markers an answer may cite: `V. 16.02.2026`, `16/02/2026`,
# `Aprile 2023`, `April 2023`. Checking these literally against the payload is
# the cheap half of the "invented provenance" question — and the reliable half:
# the platform judge produced false accusations here precisely because it was
# shown a truncated payload and could not find text that was really there.
_MONTHS = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
    "|january|february|march|april|may|june|july|august|september|october|november|december"
)
PROVENANCE_RE = re.compile(
    rf"(V\.\s?\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}|(?:{_MONTHS})\s+\d{{4}})",
    re.IGNORECASE,
)


def _answer_haystack(turn: TurnView) -> str:
    return turn.agent_text or ""


def _payload_haystack(turn: TurnView) -> str:
    return "\n".join(call.output_raw for call in turn.tool_calls)


def check_turn(turn: TurnView, *, require_tool_call: bool | None = None) -> list[DeterministicCheck]:
    """Run every mechanical check for one turn."""
    checks: list[DeterministicCheck] = []
    answer = _answer_haystack(turn)
    lowered = answer.lower()

    # -- assertions authored on the scenario ---------------------------------
    forbidden_hits: list[str] = []
    missing_required: list[str] = []
    any_of_missing: list[str] = []
    regex_missing: list[str] = []
    regex_hits: list[str] = []

    for assertion in turn.expected.assertions:
        for needle in assertion.get("must_not_contain") or []:
            if str(needle).lower() in lowered:
                forbidden_hits.append(str(needle))
        for needle in assertion.get("must_contain") or []:
            if str(needle).lower() not in lowered:
                missing_required.append(str(needle))
        any_of = assertion.get("must_contain_any") or []
        if any_of and not any(str(n).lower() in lowered for n in any_of):
            any_of_missing.extend(str(n) for n in any_of)
        for pattern in assertion.get("regex_must_match") or []:
            if not re.search(str(pattern), answer, re.IGNORECASE | re.DOTALL):
                regex_missing.append(str(pattern))
        for pattern in assertion.get("regex_must_not_match") or []:
            if re.search(str(pattern), answer, re.IGNORECASE | re.DOTALL):
                regex_hits.append(str(pattern))

    if any(a.get("must_not_contain") for a in turn.expected.assertions):
        checks.append(
            DeterministicCheck(
                name="must_not_contain",
                passed=not forbidden_hits,
                detail=(
                    "no forbidden substring in the answer"
                    if not forbidden_hits
                    else f"answer leaks {len(forbidden_hits)} forbidden substring(s)"
                ),
                hits=sorted(set(forbidden_hits)),
            )
        )
    if any(a.get("must_contain") for a in turn.expected.assertions):
        checks.append(
            DeterministicCheck(
                name="must_contain",
                passed=not missing_required,
                detail="all required substrings present" if not missing_required else "required substrings missing",
                hits=sorted(set(missing_required)),
            )
        )
    if any(a.get("must_contain_any") for a in turn.expected.assertions):
        checks.append(
            DeterministicCheck(
                name="must_contain_any",
                passed=not any_of_missing,
                detail="at least one alternative present" if not any_of_missing else "no alternative present",
                hits=sorted(set(any_of_missing)),
            )
        )
    if any(a.get("regex_must_match") for a in turn.expected.assertions):
        checks.append(
            DeterministicCheck(
                name="regex_must_match",
                passed=not regex_missing,
                detail="all patterns matched" if not regex_missing else "patterns unmatched",
                hits=sorted(set(regex_missing)),
            )
        )
    if any(a.get("regex_must_not_match") for a in turn.expected.assertions):
        checks.append(
            DeterministicCheck(
                name="regex_must_not_match",
                passed=not regex_hits,
                detail="no forbidden pattern matched" if not regex_hits else "forbidden patterns matched",
                hits=sorted(set(regex_hits)),
            )
        )

    # -- tool discipline -----------------------------------------------------
    called = [call.name for call in turn.tool_calls]
    allowed = set(turn.expected.tools_allowed)
    if allowed:
        outside = sorted({name for name in called if name not in allowed})
        checks.append(
            DeterministicCheck(
                name="tools_allowed",
                passed=not outside,
                detail=(
                    f"only allowed tools used ({', '.join(sorted(allowed))})"
                    if not outside
                    else "tools used outside the allowlist"
                ),
                hits=outside,
            )
        )

    declared = sorted({str(m.get("tool_name") or m.get("name")) for m in turn.expected.declared_mocks if m.get("tool_name") or m.get("name")})
    if declared:
        uncalled = [name for name in declared if name not in called]
        checks.append(
            DeterministicCheck(
                name="declared_mocks_called",
                passed=not uncalled,
                detail=(
                    "every declared mock was called"
                    if not uncalled
                    else "declared mock(s) never called — the platform runner fails these too"
                ),
                hits=uncalled,
            )
        )
        mismatched = [call.name for call in turn.tool_calls if call.declared_mock and call.matches_mock is False]
        checks.append(
            DeterministicCheck(
                name="mock_payload_matches",
                passed=not mismatched,
                detail=(
                    "delivered payloads match the frozen mocks"
                    if not mismatched
                    else "delivered payload differs from the frozen mock — mock drift, not an agent fault"
                ),
                hits=sorted(set(mismatched)),
                blocking=False,
            )
        )

    needs_tool = require_tool_call
    if needs_tool is None:
        needs_tool = bool(allowed or declared)
    if needs_tool:
        checks.append(
            DeterministicCheck(
                name="tool_call_present",
                passed=bool(called),
                detail="agent used its tools" if called else "agent answered without calling any tool",
                hits=[],
            )
        )

    # -- provenance markers, checked literally against the payload -----------
    cited = {match.strip() for match in PROVENANCE_RE.findall(answer)}
    if cited:
        payload = _payload_haystack(turn)
        payload_lower = payload.lower()
        unbacked = sorted(marker for marker in cited if marker.lower() not in payload_lower)
        checks.append(
            DeterministicCheck(
                name="provenance_literals",
                passed=not unbacked,
                detail=(
                    f"every cited date/version marker appears in the payload ({', '.join(sorted(cited))})"
                    if not unbacked
                    else "cited date/version marker(s) do not appear anywhere in the payload"
                ),
                hits=unbacked,
                blocking=False,
            )
        )

    if not answer.strip():
        checks.append(
            DeterministicCheck(
                name="answer_present",
                passed=False,
                detail="the agent produced no user-facing answer",
                hits=[],
            )
        )

    return checks


def blocking_failures(checks: list[DeterministicCheck]) -> list[DeterministicCheck]:
    return [check for check in checks if check.blocking and not check.passed]


def taxonomy_from_checks(checks: list[DeterministicCheck]) -> list[str]:
    """Map failed mechanical checks onto the shared failure taxonomy."""
    tags: list[str] = []
    for check in checks:
        if check.passed:
            continue
        if check.name == "must_not_contain":
            tags.append("internal_field_leak")
        elif check.name in {"must_contain", "must_contain_any", "regex_must_match"}:
            tags.append("missing_required_fact")
        elif check.name == "tool_call_present":
            tags.append("no_tool_call")
        elif check.name == "declared_mocks_called":
            tags.append("no_tool_call")
        elif check.name == "tools_allowed":
            tags.append("wrong_channel_or_procedure")
        elif check.name == "provenance_literals":
            tags.append("invented_provenance")
    return sorted(set(tags))
