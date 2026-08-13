"""Raw platform payloads → one normalized `AttemptView`.

Every parsing rule the platform imposes on us lives here, so the judge, the
report and the dashboard all see the same conversation:

* `result.json` (`wful eval result --json`) is the source of truth for the
  transcript and for **complete** tool payloads: `agent_responses[].tool_details`
  carries `params` (the call arguments) and `output` (the JSON the tool returned,
  wrapped as `{"Response": {"result": …}}`). Observed up to 31 KB, untruncated.
* `trace.json` (`wful traces call --json`) adds what the result lacks: the agent's
  system prompt (`wonderful.turn.base_prompt`, complete), the model that actually
  answered, skill routing, per-span timings, and the tool call as the model saw
  it — but its `gen_ai.tool.call.result` is capped around 16 KB, so payload
  comparisons must use the result, never the trace.
* `activity.json` (`wful activities get --json`) is permanent (traces are not) and
  is the only place with agent-side token usage, including cached input, plus
  `agent_version` (with commit) and the tool mocks actually applied at runtime.

Anything missing degrades to a warning on the view — never an exception.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schemas import (
    AttemptView,
    ExpectedTurnView,
    LLMCallView,
    MessageView,
    OfficialJudgeView,
    TokenUsage,
    ToolCallView,
    TurnView,
)

# `gen_ai.tool.call.result` in traces is capped by the platform around 16 KB.
TRACE_RESULT_CAP = 16384


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def canonical(value: Any) -> str:
    """Stable JSON for structural comparison (mock vs delivered payload)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def unwrap_tool_output(raw: Any) -> tuple[Any, str | None]:
    """Unwrap `{"Response": {"result": …}}` and parse JSON strings.

    Returns `(payload, parse_error)`.
    """
    value = raw
    error: str | None = None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            return raw, f"not JSON: {exc.msg}"
    if isinstance(value, dict) and set(value) == {"Response"} and isinstance(value["Response"], dict):
        inner = value["Response"]
        value = inner.get("result", inner)
    return value, error


def _tool_call_view(index: int, details: dict[str, Any]) -> ToolCallView:
    raw_output = details.get("output")
    output_raw = raw_output if isinstance(raw_output, str) else canonical(raw_output) if raw_output is not None else ""
    payload, parse_error = unwrap_tool_output(raw_output)
    args = details.get("params")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    return ToolCallView(
        index=index,
        name=str(details.get("function_name") or details.get("name") or "?"),
        description=details.get("description"),
        args=args if isinstance(args, dict) else None,
        output=payload,
        output_raw=output_raw,
        output_bytes=len(output_raw),
        output_parse_error=parse_error,
        truncated=bool(output_raw) and (output_raw.rstrip().endswith("…") or "[truncated" in output_raw[-40:]),
        call_id=details.get("call_id"),
        duration_ms=details.get("duration_ms"),
        call_source=(
            str(details["call_source"])
            if details.get("call_source") is not None
            else None
        ),
        trigger_type=(
            str(details["trigger_type"])
            if details.get("trigger_type") is not None
            else None
        ),
    )


def _normalized_trigger_type(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _is_on_start_call(call: ToolCallView) -> bool:
    return call.is_trigger and _normalized_trigger_type(call.trigger_type) == "onstart"


def _startup_call_signature(call: ToolCallView) -> tuple[str, str, str, str]:
    """Fallback identity when different persistence layers expose different call ids."""
    return (
        call.name,
        canonical(call.args or {}),
        canonical(call.output),
        _normalized_trigger_type(call.trigger_type),
    )


def _same_startup_call(left: ToolCallView, right: ToolCallView) -> bool:
    if left.call_id and right.call_id and left.call_id == right.call_id:
        return True
    return _startup_call_signature(left) == _startup_call_signature(right)


def _activity_startup_calls(activity: Any) -> list[ToolCallView]:
    """Recover startup calls that the V3 scripted controller drains before turn 1.

    Activity transcriptions are permanent and retain complete tool details, including
    lifecycle provenance and mock output. They are therefore the primary source for
    calls that are absent from ``result.turn_results``.
    """
    if not isinstance(activity, dict):
        return []
    transcriptions = activity.get("transcriptions")
    if not isinstance(transcriptions, list):
        return []
    calls: list[ToolCallView] = []
    def sequence_key(item: dict[str, Any]) -> tuple[int, int | str]:
        value = item.get("sequence")
        try:
            return (0, int(value))
        except (TypeError, ValueError):
            return (1, str(value or ""))

    ordered = sorted(
        (item for item in transcriptions if isinstance(item, dict)),
        key=sequence_key,
    )
    for transcription in ordered:
        details = transcription.get("tool_details")
        if not isinstance(details, dict) or not details:
            continue
        complete_details = dict(details)
        if not complete_details.get("call_id") and transcription.get("internal_id"):
            complete_details["call_id"] = transcription["internal_id"]
        call = _tool_call_view(len(calls), complete_details)
        if _is_on_start_call(call):
            calls.append(call)
    return calls


def _startup_calls(activity: Any, parsed_trace: dict[str, Any]) -> list[ToolCallView]:
    """Merge permanent activity evidence with trace fallback, without double-counting."""
    candidates = _activity_startup_calls(activity)
    for traced in parsed_trace.get("trace_tool_calls") or []:
        details = traced.get("details") if isinstance(traced, dict) else None
        if not isinstance(details, dict):
            continue
        call = _tool_call_view(len(candidates), details)
        if _is_on_start_call(call):
            candidates.append(call)

    unique: list[ToolCallView] = []
    for call in candidates:
        if any(_same_startup_call(existing, call) for existing in unique):
            continue
        unique.append(call.model_copy(update={"index": len(unique)}))
    return unique


def _prepend_missing_startup_calls(
    visible: list[ToolCallView],
    messages: list[MessageView],
    recovered: list[ToolCallView],
) -> tuple[list[ToolCallView], list[MessageView]]:
    """Keep visible result calls authoritative; prepend only evidence it omitted."""
    missing = [
        call
        for call in recovered
        if not any(_same_startup_call(existing, call) for existing in visible)
    ]
    if not missing:
        return (
            [call.model_copy(update={"index": index}) for index, call in enumerate(visible)],
            [message.model_copy(update={"index": index}) for index, message in enumerate(messages)],
        )

    shift = len(missing)
    calls = [*missing, *visible]
    calls = [call.model_copy(update={"index": index}) for index, call in enumerate(calls)]
    startup_messages = [
        MessageView(index=index, role="tool", tool_index=index)
        for index in range(shift)
    ]
    shifted = [
        message.model_copy(
            update={
                "index": index + shift,
                "tool_index": (
                    message.tool_index + shift
                    if message.tool_index is not None
                    else None
                ),
            }
        )
        for index, message in enumerate(messages)
    ]
    return calls, [*startup_messages, *shifted]


def _official_view(turn_state: dict[str, Any], failure_reason: str | None) -> OfficialJudgeView | None:
    evaluations = turn_state.get("llm_judge_evaluation_results") or []
    if not evaluations:
        return None
    first = evaluations[0] or {}
    prompt = first.get("prompt")
    return OfficialJudgeView(
        model=first.get("model"),
        verdict=first.get("verdict"),
        passed=first.get("passed"),
        score=first.get("score"),
        explanation=first.get("explanation"),
        suggestions=first.get("suggestions"),
        failure_reason=failure_reason,
        prompt=prompt,
        prompt_hash=sha8(prompt) if isinstance(prompt, str) else None,
        token_usage=first.get("token_usage"),
        # The blind-judge bug: a prompt with no `output=` never showed the judge
        # what the tools returned, so grounding could only be guessed.
        saw_tool_output=("output=" in prompt) if isinstance(prompt, str) else None,
    )


def _expected_view(
    scenario_turn: dict[str, Any],
    default_prompt: str | None,
    start_mocks: list[dict[str, Any]],
    is_first_turn: bool,
) -> ExpectedTurnView:
    mocks = list(scenario_turn.get("tool_mocks") or [])
    if is_first_turn:
        mocks = [*start_mocks, *mocks]
    return ExpectedTurnView(
        user_message=scenario_turn.get("user_message"),
        reference_response=scenario_turn.get("reference_response"),
        expected_output=scenario_turn.get("expected_output"),
        metadata=scenario_turn.get("metadata") if isinstance(scenario_turn.get("metadata"), dict) else {},
        tools_allowed=list(scenario_turn.get("tools_allowed") or []),
        assertions=list(scenario_turn.get("assertions") or []),
        judge_model=scenario_turn.get("judge_model"),
        turn_prompt=scenario_turn.get("turn_prompt") or default_prompt,
        declared_mocks=mocks,
    )


def _expected_input_matches(expected: Any, actual: dict[str, Any] | None) -> bool:
    """Wonderful treats a missing expected_input as a wildcard, `{}` as exact."""
    if expected is None:
        return True
    if not isinstance(expected, dict):
        return False
    return canonical(expected) == canonical(actual or {})


def _mark_mocks(tool_calls: list[ToolCallView], mocks: list[dict[str, Any]]) -> None:
    """Match calls to individual mock declarations, including repeated tools.

    The old name->payload map silently collapsed two mocks for the same tool.
    Here every declaration is consumable once. Exact expected-input matches
    win. A same-name call with the wrong input is marked as a mismatch but does
    not consume a declaration that a later, correct call may satisfy.
    """
    indexed = [
        (index, mock, str(mock.get("tool_name") or mock.get("name") or ""))
        for index, mock in enumerate(mocks)
        if mock.get("tool_name") or mock.get("name")
    ]
    consumed: set[int] = set()
    for call in tool_calls:
        candidates = [entry for entry in indexed if entry[0] not in consumed and entry[2] == call.name]
        if not candidates:
            continue
        chosen = next(
            (
                entry
                for entry in candidates
                if _expected_input_matches(entry[1].get("expected_input"), call.args)
            ),
            None,
        )
        if chosen is None:
            call.declared_mock = True
            call.matches_mock_input = False
            continue
        index, mock, _ = chosen
        consumed.add(index)
        call.declared_mock = True
        call.declared_mock_index = index
        call.matches_mock_input = True
        expected_output = mock.get("mock_output")
        if expected_output is not None:
            call.matches_mock = canonical(expected_output) == canonical(call.output)


def _parse_trace(trace: Any) -> dict[str, Any]:
    """Pull system prompt, model, routing, spans and per-tool trace views."""
    out: dict[str, Any] = {
        "available": False,
        "note": None,
        "spans": [],
        "span_count": None,
        "system_prompt": None,
        "model": None,
        "active_skill": None,
        "decision": None,
        "tools": [],
        "llm_calls": [],
        "trace_tool_calls": [],
    }
    if not isinstance(trace, dict):
        out["note"] = "no trace payload"
        return out
    if "missing" in trace:
        out["note"] = str(trace.get("missing"))
        return out

    spans = trace.get("spans")
    if not isinstance(spans, list):
        out["note"] = "trace payload has no spans"
        return out

    out["available"] = True
    out["span_count"] = len(spans)
    for span in spans:
        attrs = span.get("attributes") or {}
        name = str(span.get("name") or "")
        out["spans"].append(
            {
                "span_id": span.get("span_id"),
                "parent_span_id": span.get("parent_span_id"),
                "name": name,
                "kind": span.get("kind"),
                "start_offset_us": span.get("start_offset_us"),
                "duration_us": span.get("duration_us"),
                "status_code": span.get("status_code"),
                "status_message": span.get("status_message"),
                "attributes": attrs,
            }
        )
        if name == "agent.turn":
            out["system_prompt"] = out["system_prompt"] or attrs.get("wonderful.turn.base_prompt")
            out["model"] = out["model"] or attrs.get("gen_ai.request.model")
            out["active_skill"] = out["active_skill"] or attrs.get("wonderful.turn.active_skill")
            out["decision"] = out["decision"] or attrs.get("wonderful.turn.decision")
            tools = attrs.get("wonderful.turn.tools")
            if isinstance(tools, str) and not out["tools"]:
                out["tools"] = [t.strip(" '\"[]") for t in tools.split(",") if t.strip(" '\"[]")]
            elif isinstance(tools, list) and not out["tools"]:
                out["tools"] = [str(t) for t in tools]
            out["llm_calls"].append(
                LLMCallView(
                    source="trace",
                    name="agent.turn",
                    model=attrs.get("gen_ai.request.model"),
                    duration_ms=(span.get("duration_us") or 0) / 1000.0,
                    provider=attrs.get("gen_ai.system"),
                )
            )
        elif name in {"llm.completion", "llm.stream"}:
            out["llm_calls"].append(
                LLMCallView(
                    source="trace",
                    name=name,
                    model=attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model"),
                    input_tokens=_as_int(attrs.get("gen_ai.usage.input_tokens")),
                    cached_input_tokens=_as_int(attrs.get("gen_ai.usage.cached_input_tokens")),
                    output_tokens=_as_int(attrs.get("gen_ai.usage.output_tokens")),
                    reasoning_tokens=_as_int(attrs.get("gen_ai.usage.reasoning_tokens")),
                    duration_ms=(span.get("duration_us") or 0) / 1000.0,
                    provider=attrs.get("wonderful.provider"),
                )
            )
        elif name == "tool.call":
            result = attrs.get("gen_ai.tool.call.result")
            trigger_type = attrs.get("wonderful.tool.trigger_type")
            on_start = attrs.get("wonderful.tool.on_start") is True or (
                _normalized_trigger_type(trigger_type) == "onstart"
            )
            out["trace_tool_calls"].append(
                {
                    "name": attrs.get("gen_ai.tool.name"),
                    "arguments": attrs.get("gen_ai.tool.call.arguments"),
                    "result_bytes": len(result) if isinstance(result, str) else None,
                    "result_capped": isinstance(result, str) and len(result) >= TRACE_RESULT_CAP,
                    "duration_ms": (span.get("duration_us") or 0) / 1000.0,
                    "outcome": attrs.get("wonderful.outcome"),
                    "kind": attrs.get("wonderful.tool.kind"),
                    "details": {
                        "function_name": attrs.get("gen_ai.tool.name"),
                        "params": attrs.get("gen_ai.tool.call.arguments"),
                        "output": result,
                        "call_source": "trigger" if on_start else None,
                        "trigger_type": trigger_type,
                        "call_id": span.get("span_id"),
                    },
                }
            )
    return out


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _activity_tokens(activity: Any) -> TokenUsage | None:
    if not isinstance(activity, dict):
        return None
    usage = activity.get("tokens_usage")
    if not isinstance(usage, dict):
        return None
    return TokenUsage(
        provider="wonderful",
        model=None,
        input_tokens=_as_int(usage.get("total_input_tokens")) or 0,
        cached_input_tokens=_as_int(usage.get("total_cached_input_tokens")) or 0,
        output_tokens=_as_int(usage.get("total_output_tokens")) or 0,
    )


def official_judge_usage(official: OfficialJudgeView | None) -> LLMCallView | None:
    if official is None or not isinstance(official.token_usage, dict):
        return None
    usage = official.token_usage
    return LLMCallView(
        source="judge_official",
        name="official judge",
        model=usage.get("model") or official.model,
        input_tokens=_as_int(usage.get("total_input_tokens")),
        cached_input_tokens=_as_int(usage.get("total_cached_input_tokens")),
        output_tokens=_as_int(usage.get("total_output_tokens")),
        reasoning_tokens=_as_int(usage.get("total_thought_tokens")),
        provider=usage.get("provider"),
    )


def normalize_attempt(
    *,
    campaign: str,
    scenario: str,
    short: str,
    round_: int,
    result: dict[str, Any],
    trace: Any = None,
    activity: Any = None,
) -> AttemptView:
    """Build the view the judge and the dashboard both read."""
    warnings: list[str] = []
    definition = result.get("scenario_definition") or {}
    instructions = definition.get("instructions") or {}
    scenario_turns = list(instructions.get("turns") or [])
    start_mocks = list(instructions.get("start_tool_mocks") or [])
    default_prompt = instructions.get("default_turn_prompt")

    parsed_trace = _parse_trace(trace)
    system_prompt = parsed_trace["system_prompt"]
    startup_calls = _startup_calls(activity, parsed_trace)

    turns: list[TurnView] = []
    llm_calls: list[LLMCallView] = list(parsed_trace["llm_calls"])

    for turn_index, turn_result in enumerate(result.get("turn_results") or []):
        turn_state = turn_result.get("turn_state") or {}
        responses = turn_state.get("agent_responses") or []
        scenario_turn = scenario_turns[turn_index] if turn_index < len(scenario_turns) else {}

        messages: list[MessageView] = []
        tool_calls: list[ToolCallView] = []
        agent_chunks: list[str] = []

        evaluation_turn = turn_result.get("evaluation_turn") or {}
        user_message = evaluation_turn.get("user_message") or scenario_turn.get("user_message")
        if user_message:
            messages.append(MessageView(index=len(messages), role="user", text=user_message))

        for response in responses:
            details = response.get("tool_details")
            if isinstance(details, dict) and details:
                call = _tool_call_view(len(tool_calls), details)
                tool_calls.append(call)
                messages.append(
                    MessageView(
                        index=len(messages),
                        role="tool",
                        tool_index=call.index,
                        timestamp=response.get("timestamp"),
                    )
                )
                continue
            text = response.get("text") or ""
            speaker = str(response.get("speaker") or "agent")
            if not text.strip():
                continue
            role = "agent" if speaker == "agent" else "system" if speaker == "system" else "user"
            if role == "agent":
                agent_chunks.append(text)
            messages.append(
                MessageView(
                    index=len(messages),
                    role=role,  # type: ignore[arg-type]
                    text=text,
                    timestamp=response.get("timestamp"),
                )
                )

        if turn_index == 0:
            tool_calls, messages = _prepend_missing_startup_calls(
                tool_calls,
                messages,
                startup_calls,
            )

        expected = _expected_view(scenario_turn, default_prompt, start_mocks, turn_index == 0)
        _mark_mocks(tool_calls, expected.declared_mocks)
        official = _official_view(turn_state, turn_result.get("failure_reason"))
        official_call = official_judge_usage(official)
        if official_call:
            llm_calls.append(official_call)

        turns.append(
            TurnView(
                index=turn_index,
                user_message=user_message,
                agent_text="\n\n".join(agent_chunks).strip(),
                messages=messages,
                tool_calls=tool_calls,
                expected=expected,
                official=official,
                passed=turn_result.get("evaluation_passed"),
                failure_reason=turn_result.get("failure_reason"),
            )
        )

    if not turns:
        warnings.append("result payload carries no turn_results")

    activity_tokens = _activity_tokens(activity)
    if activity_tokens:
        llm_calls.append(
            LLMCallView(
                source="activity",
                name="agent conversation total",
                model=parsed_trace["model"],
                input_tokens=activity_tokens.input_tokens,
                cached_input_tokens=activity_tokens.cached_input_tokens,
                output_tokens=activity_tokens.output_tokens,
                provider="wonderful",
            )
        )
    if not parsed_trace["available"]:
        warnings.append(f"trace unavailable ({parsed_trace['note'] or 'not fetched'}) — no system prompt or span timings")
    if activity is None:
        warnings.append("activity record not fetched — no agent-side token usage")
    for capped in [t for t in parsed_trace["trace_tool_calls"] if t.get("result_capped")]:
        warnings.append(
            f"trace tool result for {capped['name']} hit the ~16 KB cap; payloads shown come from the eval result"
        )

    activity_dict = activity if isinstance(activity, dict) else {}
    return AttemptView(
        campaign=campaign,
        scenario=scenario,
        short=short,
        round=round_,
        result_id=result.get("_result_id") or result.get("id"),
        run_id=result.get("_run_id"),
        communication_id=result.get("communication_id"),
        run_status=result.get("run_status"),
        official_passed=result.get("passed"),
        failed_at_turn=result.get("failed_at_turn"),
        execution_time_ms=result.get("execution_time_ms") or activity_dict.get("duration"),
        created_at=result.get("created_at"),
        channel=result.get("channel") or definition.get("channel"),
        agent_id=result.get("agent_id"),
        agent_model=parsed_trace["model"],
        snapshot_id=result.get("agent_repo_snapshot_id") or activity_dict.get("agent_snapshot_id"),
        scenario_name=result.get("scenario_name") or definition.get("name"),
        scenario_description=definition.get("description"),
        agent_version=activity_dict.get("agent_version"),
        turns=turns,
        llm_calls=llm_calls,
        system_prompt=system_prompt,
        system_prompt_hash=sha8(system_prompt) if isinstance(system_prompt, str) else None,
        system_prompt_source="trace:wonderful.turn.base_prompt" if system_prompt else None,
        active_skill=parsed_trace["active_skill"],
        routing_decision=parsed_trace["decision"],
        tools_available=parsed_trace["tools"],
        trace_available=parsed_trace["available"],
        trace_note=parsed_trace["note"],
        trace_spans=parsed_trace["span_count"],
        activity_available=isinstance(activity, dict),
        agent_tokens=activity_tokens,
        applied_mocks=list((activity_dict.get("metadata") or {}).get("eval_tool_mocks") or []),
        spans=parsed_trace["spans"],
        warnings=warnings,
    )
