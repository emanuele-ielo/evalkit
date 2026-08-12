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

One check derives from the corpus row flags in the tool payloads (rows carry a
`flags` object set by the ingestion pipeline):

    pcv_no_contact   any row flagged `pending_channel_validation` forbids the
                     answer from handing out contact details (phone/PEC/email)

LOB validation and provenance are deliberately not customer-facing concerns in
rubric v4: neither creates a deterministic requirement here.
"""

from __future__ import annotations

import re
from collections import Counter

from .schemas import DeterministicCheck, TurnView

# Contact details an answer must not hand out while a row is flagged
# `pending_channel_validation`: phone-looking digit runs (5+ digits, dots,
# spaces or hyphens allowed between them), the canonical 3-digit TIM service
# numbers (119, 187), and email/PEC addresses. Not contact details: dates,
# euro amounts ("16.02.2026", "0,01953 €/KB"), quantities with a unit
# ("12345 KB", "10.000 punti") and digits labelled as codes/IDs
# ("codice fiscale 12345678901") — all shapes seen in real telco answers.
PHONE_SEQ_RE = re.compile(r"\d(?:[\s.\-]?\d){4,}")
# 3-digit TIM service numbers, standalone: not glued to other digits, not a
# decimal/date fragment ("119,90", "1.119", "16.02.119" stay out).
SHORT_CONTACT_RE = re.compile(r"(?<![\d.,/\-])\b(?:119|187)\b(?![.,/\-]?\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DATE_SHAPE_RE = re.compile(r"\d{1,2}[.\s/]\d{1,2}[.\s/]\d{2,4}|\d{1,2}[.\s]\d{4}")
_PRICE_CONTEXT_RE = re.compile(r"€|euro|eur\b", re.IGNORECASE)
_PRICE_WINDOW = 12  # chars around a digit run in which €/euro marks it a price
# Thousands-separated quantities ("10.000", "1 500") are amounts, not phones.
_THOUSANDS_SHAPE_RE = re.compile(r"\d{1,3}(?:[.\s]\d{3})+")
_THOUSANDS_MAX_DIGITS = 7  # a 9-digit "800.123.456" is still a phone number
# A measurement unit right after the digits marks a quantity ("12345 KB").
_UNIT_AFTER_RE = re.compile(
    r"^\s{0,2}(?:kb|mb|gb|tb|kbps|mbps|gbps|sms|min(?:uti)?|punti|giga|mega|ore|giorni|mesi)\b",
    re.IGNORECASE,
)
# A code/ID label right before the digits marks a non-contact identifier
# ("codice fiscale 12345678901", "numero pratica: 88231").
_ID_CONTEXT_RE = re.compile(
    r"(?:codice|c\.?f\.?|fiscale|p(?:artita)?\.?\s*iva|pratica|ordine|contratto|"
    r"cliente|fattura|iccid|serial\w*|matricola|sim)"
    r"\s*(?:n(?:r|um(?:ero)?)?\.?°?\s*)?[:=]?\s*$",
    re.IGNORECASE,
)
_ID_WINDOW = 24  # chars before a digit run in which an ID label may sit


def _looks_like_compact_date(digits: str) -> bool:
    """True for 8-digit runs that read as ddmmyyyy or yyyymmdd."""
    if len(digits) != 8:
        return False
    day, month, year = int(digits[:2]), int(digits[2:4]), int(digits[4:])
    if 1 <= day <= 31 and 1 <= month <= 12 and 1990 <= year <= 2099:
        return True
    year2, month2, day2 = int(digits[:4]), int(digits[4:6]), int(digits[6:])
    return 1990 <= year2 <= 2099 and 1 <= month2 <= 12 and 1 <= day2 <= 31


def _non_contact_context(answer: str, start: int, end: int) -> bool:
    """True when surrounding text marks the digits as a price, quantity or ID."""
    price_before = answer[max(0, start - _PRICE_WINDOW) : start]
    price_after = answer[end : end + _PRICE_WINDOW]
    if _PRICE_CONTEXT_RE.search(price_before) or _PRICE_CONTEXT_RE.search(price_after):
        return True
    if _UNIT_AFTER_RE.match(answer[end : end + 12]):
        return True
    return bool(_ID_CONTEXT_RE.search(answer[max(0, start - _ID_WINDOW) : start]))


def contact_detail_hits(answer: str) -> list[str]:
    """Phone numbers and email/PEC addresses found in the answer.

    Excludes date-shaped matches, digit runs with €/euro nearby (prices),
    thousands-separated quantities, unit-suffixed quantities and labelled
    codes/IDs. Includes the standalone TIM service numbers 119 and 187.
    """
    hits: list[str] = []
    for match in PHONE_SEQ_RE.finditer(answer):
        text = match.group(0)
        if _DATE_SHAPE_RE.fullmatch(text.strip()):
            continue
        digits = re.sub(r"\D", "", text)
        if _looks_like_compact_date(digits):
            continue
        if len(digits) <= _THOUSANDS_MAX_DIGITS and _THOUSANDS_SHAPE_RE.fullmatch(text.strip()):
            continue
        if _non_contact_context(answer, match.start(), match.end()):
            continue
        hits.append(text)
    for match in SHORT_CONTACT_RE.finditer(answer):
        if not _non_contact_context(answer, match.start(), match.end()):
            hits.append(match.group(0))
    hits.extend(match.group(0) for match in EMAIL_RE.finditer(answer))
    return hits


def _count_flagged_rows(turn: TurnView, flag: str) -> int:
    """How many payload rows in this turn carry `flags.<flag> == true`."""
    count = 0

    def walk(node: object) -> None:
        nonlocal count
        if isinstance(node, dict):
            flags = node.get("flags")
            if isinstance(flags, dict) and flags.get(flag):
                count += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for call in turn.tool_calls:
        if call.output is not None and not isinstance(call.output, str):
            walk(call.output)
        elif call.output_raw:
            # unwrap_tool_output returns the raw STRING as the payload when it
            # is not parseable JSON (truncated payloads included), so a string
            # `output` gives walk() nothing to inspect — scan the raw text
            # instead, or a set flag silently skips a blocking check
            # (fail-open). Tolerate JSON-in-a-JSON-string quoting too
            # (\"flag\": true).
            count += len(
                re.findall(rf'\\?"{re.escape(flag)}\\?"\s*:\s*true', call.output_raw)
            )
    return count


def _answer_haystack(turn: TurnView) -> str:
    return turn.agent_text or ""


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
    contract = turn.expected.metadata.get("evalkit_v5") or turn.expected.metadata.get("evalkit_v4")
    if isinstance(contract, dict) and contract.get("response_mode") == "clarify":
        checks.append(
            DeterministicCheck(
                name="clarification_no_tool",
                passed=not called,
                detail=(
                    "underspecified turn correctly paused before retrieval"
                    if not called
                    else "agent searched before obtaining the missing decision slot"
                ),
                hits=sorted(set(called)),
            )
        )
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

    # EvalKit v5 makes trajectory requirements explicit instead of deriving
    # them from mocks or a non-empty allowlist.
    if isinstance(contract, dict):
        required_tools = [str(name) for name in contract.get("required_tools") or []]
        missing_tools = [name for name in required_tools if name not in called]
        if required_tools:
            checks.append(
                DeterministicCheck(
                    name="required_tools",
                    passed=not missing_tools,
                    detail="all required tools were called" if not missing_tools else "required tool(s) missing",
                    hits=missing_tools,
                )
            )

        forbidden_tools = {str(name) for name in contract.get("forbidden_tools") or []}
        forbidden_calls = sorted({name for name in called if name in forbidden_tools})
        if forbidden_tools:
            checks.append(
                DeterministicCheck(
                    name="forbidden_tools",
                    passed=not forbidden_calls,
                    detail="no forbidden tool was called" if not forbidden_calls else "forbidden tool(s) called",
                    hits=forbidden_calls,
                )
            )

        expected_sequence = [str(name) for name in contract.get("tool_sequence") or []]
        if expected_sequence:
            position = 0
            for name in called:
                if position < len(expected_sequence) and name == expected_sequence[position]:
                    position += 1
            sequence_ok = position == len(expected_sequence)
            checks.append(
                DeterministicCheck(
                    name="tool_sequence",
                    passed=sequence_ok,
                    detail=(
                        "required tool sequence observed"
                        if sequence_ok
                        else f"required ordered subsequence not observed: {' -> '.join(expected_sequence)}"
                    ),
                    hits=[] if sequence_ok else expected_sequence,
                )
            )

        cardinality = contract.get("tool_cardinality")
        if isinstance(cardinality, dict):
            counts = Counter(called)
            violations: list[str] = []
            for name, raw_bounds in cardinality.items():
                bounds = raw_bounds if isinstance(raw_bounds, dict) else {}
                minimum = int(bounds.get("min", 0))
                maximum_raw = bounds.get("max")
                maximum = int(maximum_raw) if maximum_raw is not None else None
                actual = counts[str(name)]
                if actual < minimum or (maximum is not None and actual > maximum):
                    upper = "∞" if maximum is None else str(maximum)
                    violations.append(f"{name}: {actual} call(s), expected {minimum}..{upper}")
            checks.append(
                DeterministicCheck(
                    name="tool_cardinality",
                    passed=not violations,
                    detail="tool call cardinality satisfied" if not violations else "tool call cardinality violated",
                    hits=violations,
                )
            )

    declared = [
        str(m.get("tool_name") or m.get("name"))
        for m in turn.expected.declared_mocks
        if m.get("tool_name") or m.get("name")
    ]
    if declared:
        declared_counts = Counter(declared)
        called_counts = Counter(call.name for call in turn.tool_calls if call.declared_mock)
        uncalled = [
            f"{name} x{expected - called_counts[name]}"
            for name, expected in sorted(declared_counts.items())
            if called_counts[name] < expected
        ]
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
        input_mismatches = [call.name for call in turn.tool_calls if call.declared_mock and call.matches_mock_input is False]
        checks.append(
            DeterministicCheck(
                name="mock_expected_input",
                passed=not input_mismatches,
                detail=(
                    "tool inputs match their declared mocks"
                    if not input_mismatches
                    else "tool input differs from the declared expected_input"
                ),
                hits=input_mismatches,
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
        if isinstance(contract, dict) and contract.get("response_mode") == "clarify":
            needs_tool = False
        else:
            required = contract.get("required_tools") if isinstance(contract, dict) else None
            needs_tool = bool(required or allowed or declared)
    if needs_tool:
        checks.append(
            DeterministicCheck(
                name="tool_call_present",
                passed=bool(called),
                detail="agent used its tools" if called else "agent answered without calling any tool",
                hits=[],
            )
        )

    # -- corpus row flags, checked against the answer -------------------------
    pcv_rows = _count_flagged_rows(turn, "pending_channel_validation")
    if pcv_rows:
        contact_hits = contact_detail_hits(answer)
        checks.append(
            DeterministicCheck(
                name="pcv_no_contact",
                passed=not contact_hits,
                detail=(
                    f"{pcv_rows} payload row(s) flagged pending_channel_validation and the answer "
                    "hands out no contact detail"
                    if not contact_hits
                    else f"{pcv_rows} payload row(s) flagged pending_channel_validation but the answer "
                    f"hands out {len(contact_hits)} contact detail(s)"
                ),
                hits=sorted(set(contact_hits)),
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
        elif check.name == "clarification_no_tool":
            tags.append("premature_tool_call")
        elif check.name in {"declared_mocks_called", "required_tools"}:
            tags.append("no_tool_call")
        elif check.name in {
            "tools_allowed",
            "forbidden_tools",
            "tool_sequence",
            "tool_cardinality",
            "mock_expected_input",
        }:
            tags.append("wrong_channel_or_procedure")
        elif check.name == "pcv_no_contact":
            tags.append("wrong_channel_or_procedure")
    return sorted(set(tags))
