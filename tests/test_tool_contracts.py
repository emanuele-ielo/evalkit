from __future__ import annotations

import unittest

from evalkit.deterministic import blocking_failures, check_turn, taxonomy_from_checks
from evalkit.judge.rubric import build_vote_prompt
from evalkit.normalize import _mark_mocks, _tool_call_view, normalize_attempt
from evalkit.schemas import ExpectedTurnView, ToolCallView, TurnView


def call(name: str, *, args: dict | None = None, output: object = None) -> ToolCallView:
    return ToolCallView(index=0, name=name, args=args, output=output)


def turn(calls: list[ToolCallView], contract: dict, *, tools_allowed: list[str] | None = None) -> TurnView:
    return TurnView(
        index=0,
        agent_text="Risposta presente.",
        tool_calls=calls,
        expected=ExpectedTurnView(
            tools_allowed=tools_allowed or [],
            metadata={"evalkit_v5": contract},
        ),
    )


def by_name(checks):
    return {check.name: check for check in checks}


class ToolContractsTest(unittest.TestCase):
    @staticmethod
    def startup_result() -> dict:
        mock = {
            "tool_name": "resolve_customer_context",
            "expected_input": {},
            "mock_output": {"success": True, "segment": "interforze"},
        }
        return {
            "scenario_definition": {
                "name": "startup-trigger",
                "instructions": {
                    "start_tool_mocks": [mock],
                    "turns": [
                        {
                            "user_message": "Domanda incompleta",
                            "tools_allowed": [],
                            "metadata": {
                                "evalkit_v5": {
                                    "response_mode": "clarify",
                                }
                            },
                        }
                    ],
                },
            },
            "turn_results": [
                {
                    "evaluation_turn": {"user_message": "Domanda incompleta"},
                    "turn_state": {
                        "agent_responses": [
                            {"speaker": "agent", "text": "Quale dettaglio ti serve?"}
                        ]
                    },
                }
            ],
        }

    def test_normalizer_recovers_drained_on_start_mock_from_activity(self) -> None:
        activity = {
            "transcriptions": [
                {
                    "speaker": "system",
                    "sequence": 20,
                    "internal_id": "activity-call-1",
                    "tool_details": {
                        "function_name": "resolve_customer_context",
                        "params": {},
                        "output": {"success": True, "segment": "interforze"},
                        "call_source": "trigger",
                        "trigger_type": "on_start",
                    },
                }
            ]
        }
        view = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=self.startup_result(),
            activity=activity,
        )

        self.assertEqual(len(view.turns[0].tool_calls), 1)
        self.assertTrue(view.turns[0].tool_calls[0].is_trigger)
        self.assertEqual(view.turns[0].tool_calls[0].call_id, "activity-call-1")
        self.assertEqual(view.turns[0].tool_calls[0].declared_mock_index, 0)
        self.assertTrue(view.turns[0].tool_calls[0].matches_mock)
        checks = by_name(check_turn(view.turns[0]))
        self.assertTrue(checks["declared_mocks_called"].passed)
        self.assertTrue(checks["clarification_no_tool"].passed)

    def test_normalizer_uses_trace_fallback_and_deduplicates_activity(self) -> None:
        details = {
            "function_name": "resolve_customer_context",
            "params": {},
            "output": {"success": True, "segment": "interforze"},
            "call_source": "trigger",
            "trigger_type": "on_start",
        }
        activity = {"transcriptions": [{"tool_details": details}]}
        trace = {
            "spans": [
                {
                    "name": "tool.call",
                    "attributes": {
                        "gen_ai.tool.name": "resolve_customer_context",
                        "gen_ai.tool.call.arguments": "{}",
                        "gen_ai.tool.call.result": '{"success":true,"segment":"interforze"}',
                        "wonderful.tool.trigger_type": "on_start",
                        "wonderful.tool.on_start": True,
                    },
                }
            ]
        }
        with_both = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=self.startup_result(),
            activity=activity,
            trace=trace,
        )
        trace_only = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=self.startup_result(),
            trace=trace,
        )

        self.assertEqual(len(with_both.turns[0].tool_calls), 1)
        self.assertEqual(len(trace_only.turns[0].tool_calls), 1)
        self.assertTrue(by_name(check_turn(trace_only.turns[0]))["declared_mocks_called"].passed)

    def test_visible_result_trigger_wins_and_is_not_duplicated(self) -> None:
        result = self.startup_result()
        result["turn_results"][0]["turn_state"]["agent_responses"].insert(
            0,
            {
                "speaker": "system",
                "tool_details": {
                    "function_name": "resolve_customer_context",
                    "params": {},
                    "output": {"success": True, "segment": "interforze"},
                    "call_source": "trigger",
                    "trigger_type": "on_start",
                    "call_id": "visible-result-call",
                },
            },
        )
        activity = {
            "transcriptions": [
                {
                    "internal_id": "activity-copy",
                    "tool_details": {
                        "function_name": "resolve_customer_context",
                        "params": {},
                        "output": {"success": True, "segment": "interforze"},
                        "call_source": "trigger",
                        "trigger_type": "on_start",
                    },
                }
            ]
        }
        view = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=result,
            activity=activity,
        )

        self.assertEqual(len(view.turns[0].tool_calls), 1)
        self.assertEqual(view.turns[0].tool_calls[0].call_id, "visible-result-call")

    def test_missing_activity_and_trace_keeps_start_mock_failure_blocking(self) -> None:
        view = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=self.startup_result(),
        )
        check = by_name(check_turn(view.turns[0]))["declared_mocks_called"]

        self.assertFalse(check.passed)
        self.assertTrue(check.blocking)

    def test_activity_imports_only_startup_triggers_not_business_calls(self) -> None:
        result = self.startup_result()
        result["turn_results"][0]["turn_state"]["agent_responses"].insert(
            0,
            {
                "speaker": "system",
                "tool_details": {
                    "function_name": "search_vera",
                    "params": {"query": "fatture"},
                    "output": {"answer": "result copy"},
                    "call_id": "visible-search",
                },
            },
        )
        activity = {
            "transcriptions": [
                {
                    "tool_details": {
                        "function_name": "search_vera",
                        "params": {"query": "fatture"},
                        "output": {"answer": "activity duplicate"},
                    }
                },
                {
                    "tool_details": {
                        "function_name": "resolve_customer_context",
                        "params": {},
                        "output": {"success": True, "segment": "interforze"},
                        "call_source": "trigger",
                        "trigger_type": "on_start",
                    }
                },
            ]
        }
        view = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=result,
            activity=activity,
        )

        self.assertEqual(
            [item.name for item in view.turns[0].tool_calls],
            ["resolve_customer_context", "search_vera"],
        )
        self.assertEqual(view.turns[0].tool_calls[1].output, {"answer": "result copy"})

    def test_judge_prompt_excludes_trigger_payload_but_keeps_business_payload(self) -> None:
        result = self.startup_result()
        result["scenario_definition"]["instructions"]["start_tool_mocks"][0]["mock_output"] = {
            "success": True,
            "bootstrap": "BOOTSTRAP_SENTINEL",
        }
        result["turn_results"][0]["turn_state"]["agent_responses"].insert(
            0,
            {
                "speaker": "system",
                "tool_details": {
                    "function_name": "search_vera",
                    "params": {"query": "fatture"},
                    "output": {"answer": "BUSINESS_SENTINEL"},
                },
            },
        )
        activity = {
            "transcriptions": [
                {
                    "tool_details": {
                        "function_name": "resolve_customer_context",
                        "params": {},
                        "output": {"success": True, "bootstrap": "BOOTSTRAP_SENTINEL"},
                        "call_source": "trigger",
                        "trigger_type": "on_start",
                    }
                }
            ]
        }
        view = normalize_attempt(
            campaign="test",
            scenario="startup",
            short="startup",
            round_=1,
            result=result,
            activity=activity,
        )
        _, prompt = build_vote_prompt(view, view.turns[0], check_turn(view.turns[0]))

        self.assertIn("BUSINESS_SENTINEL", prompt)
        self.assertNotIn("BOOTSTRAP_SENTINEL", prompt)

    def test_normalizer_preserves_lifecycle_trigger_metadata(self) -> None:
        view = _tool_call_view(
            0,
            {
                "function_name": "resolve_customer_context",
                "params": {},
                "output": {"success": True},
                "call_source": "trigger",
                "trigger_type": "on_start",
            },
        )

        self.assertEqual(view.call_source, "trigger")
        self.assertEqual(view.trigger_type, "on_start")
        self.assertTrue(view.is_trigger)

    def test_trigger_is_observed_but_not_a_business_call_on_clarification(self) -> None:
        trigger = ToolCallView(
            index=0,
            name="resolve_customer_context",
            args={},
            output={"success": True},
            call_source="trigger",
            trigger_type="on_start",
        )
        mocks = [
            {
                "tool_name": "resolve_customer_context",
                "expected_input": {},
                "mock_output": {"success": True},
            }
        ]
        _mark_mocks([trigger], mocks)
        target = turn(
            [trigger],
            {"response_mode": "clarify"},
            tools_allowed=["search_vera"],
        )
        target.expected.declared_mocks = mocks

        checks = by_name(check_turn(target))

        self.assertTrue(checks["clarification_no_tool"].passed)
        self.assertTrue(checks["tools_allowed"].passed)
        self.assertTrue(checks["declared_mocks_called"].passed)

    def test_trigger_does_not_satisfy_business_tool_call_present(self) -> None:
        trigger = ToolCallView(
            index=0,
            name="resolve_customer_context",
            call_source="trigger",
            trigger_type="on_start",
        )
        checks = by_name(
            check_turn(
                turn(
                    [trigger],
                    {"response_mode": "answer"},
                    tools_allowed=["search_vera"],
                )
            )
        )

        self.assertTrue(checks["tools_allowed"].passed)
        self.assertFalse(checks["tool_call_present"].passed)

    def test_tool_allowlist_reports_only_the_unauthorized_business_call(self) -> None:
        trigger = ToolCallView(
            index=0,
            name="resolve_customer_context",
            call_source="trigger",
            trigger_type="on_start",
        )
        unauthorized = ToolCallView(index=1, name="get_line_context")
        checks = by_name(
            check_turn(
                turn(
                    [trigger, unauthorized],
                    {"response_mode": "answer"},
                    tools_allowed=["search_vera"],
                )
            )
        )

        self.assertFalse(checks["tools_allowed"].passed)
        self.assertEqual(checks["tools_allowed"].hits, ["get_line_context"])

    def test_v5_accepts_required_ordered_single_calls(self) -> None:
        checks = check_turn(
            turn(
                [call("get_line_usage"), call("lookup_tim_info")],
                {
                    "response_mode": "answer",
                    "required_tools": ["get_line_usage", "lookup_tim_info"],
                    "forbidden_tools": ["get_line_context"],
                    "tool_sequence": ["get_line_usage", "lookup_tim_info"],
                    "tool_cardinality": {
                        "get_line_usage": {"min": 1, "max": 1},
                        "lookup_tim_info": {"min": 1, "max": 1},
                    },
                },
            )
        )
        indexed = by_name(checks)

        self.assertTrue(indexed["required_tools"].passed)
        self.assertTrue(indexed["forbidden_tools"].passed)
        self.assertTrue(indexed["tool_sequence"].passed)
        self.assertTrue(indexed["tool_cardinality"].passed)
        self.assertFalse(blocking_failures(checks))

    def test_v5_reports_missing_forbidden_reversed_and_duplicate_calls(self) -> None:
        checks = check_turn(
            turn(
                [call("lookup_tim_info"), call("get_line_usage"), call("get_line_usage"), call("get_line_context")],
                {
                    "required_tools": ["get_line_usage", "search_vera"],
                    "forbidden_tools": ["get_line_context"],
                    "tool_sequence": ["get_line_usage", "lookup_tim_info"],
                    "tool_cardinality": {"get_line_usage": {"min": 1, "max": 1}},
                },
            )
        )
        indexed = by_name(checks)

        self.assertEqual(indexed["required_tools"].hits, ["search_vera"])
        self.assertEqual(indexed["forbidden_tools"].hits, ["get_line_context"])
        self.assertFalse(indexed["tool_sequence"].passed)
        self.assertEqual(indexed["tool_cardinality"].hits, ["get_line_usage: 2 call(s), expected 1..1"])
        self.assertIn("no_tool_call", taxonomy_from_checks(checks))
        self.assertIn("wrong_channel_or_procedure", taxonomy_from_checks(checks))

    def test_repeated_mocks_match_and_consume_by_expected_input(self) -> None:
        calls = [
            call("lookup", args={"kind": "second"}, output={"value": 2}),
            call("lookup", args={"kind": "first"}, output={"value": 1}),
        ]
        mocks = [
            {
                "tool_name": "lookup",
                "expected_input": {"kind": "first"},
                "mock_output": {"value": 1},
            },
            {
                "tool_name": "lookup",
                "expected_input": {"kind": "second"},
                "mock_output": {"value": 2},
            },
        ]

        _mark_mocks(calls, mocks)

        self.assertEqual([item.declared_mock_index for item in calls], [1, 0])
        self.assertTrue(all(item.matches_mock_input for item in calls))
        self.assertTrue(all(item.matches_mock for item in calls))

        check_target = turn(calls, {"required_tools": ["lookup"]})
        check_target.expected.declared_mocks = mocks
        checks = by_name(check_turn(check_target))
        self.assertTrue(checks["declared_mocks_called"].passed)
        self.assertTrue(checks["mock_expected_input"].passed)

    def test_disabled_mock_is_passthrough_expectation_not_mocked_payload(self) -> None:
        calls = [call("lookup", args={"kind": "real"}, output={"live": True})]
        mocks = [
            {
                "tool_name": "lookup",
                "enabled": False,
                "expected_input": {"kind": "real"},
                "mock_output": {},
            }
        ]

        _mark_mocks(calls, mocks)

        self.assertTrue(calls[0].declared_mock)
        self.assertFalse(calls[0].runtime_mocked)
        self.assertTrue(calls[0].matches_mock_input)
        self.assertIsNone(calls[0].matches_mock)

        target = turn(calls, {"required_tools": ["lookup"]})
        target.expected.declared_mocks = mocks
        checks = by_name(check_turn(target))
        self.assertNotIn("declared_mocks_called", checks)
        self.assertTrue(checks["mock_expected_input"].passed)
        self.assertTrue(checks["mock_payload_matches"].passed)

        view = normalize_attempt(
            campaign="test",
            scenario="passthrough",
            short="passthrough",
            round_=1,
            result={
                "scenario_definition": {
                    "name": "passthrough",
                    "instructions": {
                        "turns": [
                            {
                                "user_message": "Cerca",
                                "tool_mocks": mocks,
                                "metadata": {"evalkit_v5": {"required_tools": ["lookup"]}},
                            }
                        ]
                    },
                },
                "turn_results": [
                    {
                        "evaluation_turn": {"user_message": "Cerca"},
                        "turn_state": {
                            "agent_responses": [
                                {
                                    "tool_details": {
                                        "function_name": "lookup",
                                        "params": {"kind": "real"},
                                        "output": {"live": True},
                                    }
                                },
                                {"speaker": "agent", "text": "Fatto."},
                            ]
                        },
                    }
                ],
            },
        )
        _, prompt = build_vote_prompt(view, view.turns[0], check_turn(view.turns[0]))
        self.assertNotIn("payload was mocked by the scenario", prompt)
        self.assertIn('"required_tools"', prompt)

    def test_disabled_mock_still_checks_real_tool_input(self) -> None:
        calls = [call("lookup", args={"kind": "wrong"}, output={"live": True})]
        mocks = [
            {
                "tool_name": "lookup",
                "enabled": False,
                "expected_input": {"kind": "right"},
                "mock_output": {},
            }
        ]

        _mark_mocks(calls, mocks)
        target = turn(calls, {"required_tools": ["lookup"]})
        target.expected.declared_mocks = mocks
        checks = by_name(check_turn(target))

        self.assertFalse(calls[0].runtime_mocked)
        self.assertFalse(checks["mock_expected_input"].passed)
        self.assertNotIn("declared_mocks_called", checks)

    def test_mock_input_mismatch_is_blocking_and_visible(self) -> None:
        calls = [call("get_line_usage", args={"number": "3331234567"}, output={"success": True})]
        mocks = [{"tool_name": "get_line_usage", "expected_input": {}, "mock_output": {"success": True}}]
        _mark_mocks(calls, mocks)
        target = turn(calls, {"required_tools": ["get_line_usage"]})
        target.expected.declared_mocks = mocks

        checks = by_name(check_turn(target))

        self.assertFalse(checks["mock_expected_input"].passed)
        self.assertTrue(checks["mock_expected_input"].blocking)
        self.assertEqual(checks["mock_expected_input"].hits, ["get_line_usage"])
        self.assertFalse(checks["declared_mocks_called"].passed)

    def test_wrong_repeated_input_does_not_consume_a_later_exact_mock(self) -> None:
        calls = [
            call("lookup", args={"kind": "wrong"}, output={"error": "MOCK_INPUT_MISMATCH"}),
            call("lookup", args={"kind": "first"}, output={"value": 1}),
            call("lookup", args={"kind": "second"}, output={"value": 2}),
        ]
        mocks = [
            {"tool_name": "lookup", "expected_input": {"kind": "first"}, "mock_output": {"value": 1}},
            {"tool_name": "lookup", "expected_input": {"kind": "second"}, "mock_output": {"value": 2}},
        ]

        _mark_mocks(calls, mocks)

        self.assertEqual([item.declared_mock_index for item in calls], [None, 0, 1])
        self.assertEqual([item.matches_mock_input for item in calls], [False, True, True])
        target = turn(calls, {"required_tools": ["lookup"]})
        target.expected.declared_mocks = mocks
        checks = by_name(check_turn(target))
        self.assertTrue(checks["declared_mocks_called"].passed)
        self.assertFalse(checks["mock_expected_input"].passed)

    def test_missing_duplicate_mock_is_counted_not_collapsed_by_name(self) -> None:
        calls = [call("lookup", args={"n": 1}, output={"n": 1})]
        mocks = [
            {"tool_name": "lookup", "expected_input": {"n": 1}, "mock_output": {"n": 1}},
            {"tool_name": "lookup", "expected_input": {"n": 2}, "mock_output": {"n": 2}},
        ]
        _mark_mocks(calls, mocks)
        target = turn(calls, {"required_tools": ["lookup"]})
        target.expected.declared_mocks = mocks

        checks = by_name(check_turn(target))

        self.assertFalse(checks["declared_mocks_called"].passed)
        self.assertEqual(checks["declared_mocks_called"].hits, ["lookup x1"])


if __name__ == "__main__":
    unittest.main()
