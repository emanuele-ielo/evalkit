from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from evalkit.config import AgentConfig, Config, LLMConfig
from evalkit.direct_chat import (
    DirectChatCollector,
    V3ChatGateway,
    WfulCredentials,
    load_wful_profile,
    scoped_tool_mocks,
)
from evalkit.report import build_report, format_report
from evalkit.runner import build_manifest, run_campaign
from evalkit.store import CampaignStore
from evalkit.wful import WfulClient, WfulNotAllowed


class DirectChatHelpersTest(unittest.TestCase):
    def test_profile_loader_matches_wful_precedence_without_exposing_key(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            profiles = Path(folder) / "profiles"
            profiles.write_text(
                "[sandbox]\n"
                "api_key = file-key\n"
                "base_url = https://controller.example\n"
                "workspace_id = file-workspace\n"
            )
            with patch.dict(
                os.environ,
                {
                    "WONDERFUL_API_KEY": "env-key",
                    "WONDERFUL_BASE_URL": "https://override.example/",
                    "WONDERFUL_WORKSPACE_ID": "env-workspace",
                },
                clear=False,
            ):
                credentials = load_wful_profile(
                    "sandbox",
                    tenant_id="tenant-1",
                    profiles_path=profiles,
                )

        self.assertEqual(credentials.api_key, "env-key")
        self.assertEqual(credentials.base_url, "https://override.example")
        self.assertEqual(credentials.workspace_id, "env-workspace")
        self.assertEqual(credentials.tenant_id, "tenant-1")

    def test_tool_mocks_keep_platform_scopes_and_render_one_clock(self) -> None:
        now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        mocks = scoped_tool_mocks(
            {
                "instructions": {
                    "start_tool_mocks": [
                        {
                            "tool_name": "bootstrap",
                            "mock_output": {"at": "{{ now | format=date }}"},
                        }
                    ],
                    "turns": [
                        {
                            "user_message": "hello",
                            "tool_mocks": [
                                {
                                    "tool_name": "lookup",
                                    "mock_output": {"tomorrow": "{{ tomorrow | format=date }}"},
                                }
                            ],
                        }
                    ],
                }
            },
            now=now,
        )

        self.assertEqual([mock["mock_scope"] for mock in mocks], ["start", "turn:0"])
        self.assertEqual(mocks[0]["mock_output"], {"at": "2026-08-13"})
        self.assertEqual(mocks[1]["mock_output"], {"tomorrow": "2026-08-14"})

    def test_eval_commands_are_not_available_to_evalkit(self) -> None:
        client = WfulClient(profile="test", workspace="workspace", binary="true")

        with self.assertRaises(WfulNotAllowed):
            client._build(["eval", "run", "scenario"])


class DirectChatCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_collects_snapshot_chat_and_never_calls_eval_api(self) -> None:
        requests: list[tuple[str, str, dict | None]] = []
        poll_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal poll_count
            payload = json.loads(request.content) if request.content else None
            requests.append((request.method, request.url.path, payload))
            if request.url.path == "/api/v1/jwt/sign":
                return httpx.Response(200, json={"data": {"jwt": "signed-token"}})
            if request.url.path == "/api/workspace/workspace-1/v3/chat" and request.method == "POST":
                return httpx.Response(200, json={"data": {"session_id": "comm-1", "cursor": "0-0"}})
            if request.url.path == "/api/workspace/workspace-1/v3/chat/comm-1" and request.method == "GET":
                poll_count += 1
                if poll_count == 1:
                    return httpx.Response(
                        200,
                        json={"data": {"cursor": "1-0", "events": [], "has_more": False}},
                    )
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "cursor": "2-0",
                            "has_more": False,
                            "events": [
                                {"speaker": "customer", "data": {"type": "text", "text": "hello"}},
                                {
                                    "speaker": "system",
                                    "created_at": 1_786_620_000_000,
                                    "data": {
                                        "type": "function_call",
                                        "function_call": {
                                            "name": "lookup",
                                            "params": "{\"q\":\"x\"}",
                                            "call_id": "call-1",
                                            "status": "started",
                                        },
                                    },
                                },
                                {
                                    "speaker": "system",
                                    "created_at": 1_786_620_000_100,
                                    "data": {
                                        "type": "function_call",
                                        "function_call": {
                                            "name": "lookup",
                                            "call_id": "call-1",
                                            "status": "completed",
                                            "output": "{\"Response\":{\"result\":{\"answer\":\"ok\"}}}",
                                        },
                                    },
                                },
                                {"speaker": "agent", "data": {"type": "text", "text": "Done."}},
                                {
                                    "speaker": "system",
                                    "data": {"type": "turn_ended", "turn_type": "user"},
                                },
                            ],
                        }
                    },
                )
            if request.url.path == "/api/workspace/workspace-1/v3/chat/comm-1" and request.method == "POST":
                return httpx.Response(200, json={"data": {}})
            return httpx.Response(404, json={"message": "unexpected test route"})

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = V3ChatGateway(
            WfulCredentials(
                profile="test",
                api_key="secret",
                base_url="https://controller.example",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
            ),
            client=async_client,
        )
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            scenarios = repo / "evals" / "scenarios"
            scenarios.mkdir(parents=True)
            (scenarios / "smoke.json").write_text(
                json.dumps(
                    {
                        "name": "smoke",
                        "channel": "chat",
                        "eval_type": "chat_eval",
                        "instructions": {
                            "mode": "turn_based",
                            "turns": [
                                {
                                    "user_message": "hello",
                                    "tool_mocks": [
                                        {
                                            "tool_name": "lookup",
                                            "expected_input": {"q": "x"},
                                            "mock_output": {"answer": "ok"},
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                )
            )
            collector = DirectChatCollector(
                gateway=gateway,
                agent_id="agent-1",
                snapshot_id="snapshot-1",
                repo=repo,
            )

            result = await collector.collect("smoke", timeout=10)

        await async_client.aclose()
        self.assertEqual(result["communication_id"], "comm-1")
        self.assertIsNone(result["passed"])
        self.assertFalse(result["collector"]["platform_eval_invoked"])
        turn = result["turn_results"][0]
        self.assertIsNone(turn["evaluation_passed"])
        self.assertEqual(turn["turn_state"]["llm_judge_evaluation_results"], [])
        tool_responses = [
            response
            for response in turn["turn_state"]["agent_responses"]
            if response.get("tool_details")
        ]
        self.assertEqual(len(tool_responses), 1)
        self.assertEqual(tool_responses[0]["tool_details"]["params"], {"q": "x"})
        self.assertIn('"answer":"ok"', tool_responses[0]["tool_details"]["output"])
        create_payload = next(payload for method, path, payload in requests if path.endswith("/v3/chat"))
        self.assertEqual(create_payload["snapshot_id"], "snapshot-1")
        self.assertEqual(create_payload["metadata"]["eval_tool_mocks"][0]["mock_scope"], "turn:0")
        self.assertFalse(any("/eval" in path for _, path, _ in requests))


class RunCampaignCollectorContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_uses_injected_direct_collector_only(self) -> None:
        class FakeCollector:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def collect(self, scenario: str, *, timeout: float) -> dict:
                self.calls.append(scenario)
                return {
                    "id": "local-result",
                    "scenario_name": scenario,
                    "scenario_definition": {
                        "name": scenario,
                        "channel": "chat",
                        "instructions": {"turns": [{"user_message": "hi"}]},
                    },
                    "communication_id": None,
                    "run_status": "completed",
                    "passed": None,
                    "execution_time_ms": 10,
                    "turn_results": [],
                }

            async def aclose(self) -> None:
                raise AssertionError("injected collector must not be closed by run_campaign")

        class ReadOnlyClient:
            pass

        agent = AgentConfig(
            name="test",
            slug="agent",
            id="agent-id",
            wful_profile="test",
            workspace="workspace",
            repo="/tmp/repo",
            concurrency=1,
            rounds=1,
        )
        config = Config(
            data_dir=Path("/tmp/unused"),
            llm=LLMConfig(),
            required_criteria=[],
            agents={"test": agent},
        )
        manifest = build_manifest(
            campaign_id="campaign",
            label="campaign",
            agent=agent,
            scenarios=["scenario"],
            rounds=1,
            concurrency=1,
            snapshot_id="snapshot",
            commit_sha=None,
            model=None,
            batch=None,
        )
        collector = FakeCollector()
        with tempfile.TemporaryDirectory() as folder:
            store = CampaignStore(Path(folder) / "campaign")
            store.save(manifest)
            await run_campaign(
                store,
                manifest,
                config,
                agent,
                client=ReadOnlyClient(),  # type: ignore[arg-type]
                collector=collector,  # type: ignore[arg-type]
                judge_inline=False,
            )

            stored = store.read_result("scenario", 1)
            report = build_report(store, manifest)

        self.assertEqual(collector.calls, ["scenario"])
        self.assertEqual(stored["id"], "local-result")
        self.assertIsNone(manifest.attempts[0].official_passed)
        self.assertIsNone(manifest.attempts[0].run_id)
        self.assertEqual(report.official_attempts_available, 0)
        self.assertEqual(report.official_scenarios_available, 0)
        self.assertIsNone(report.scenarios[0].official_majority)
        self.assertIn("no Wonderful platform verdicts", " ".join(report.notes))
        self.assertIn("—", format_report(report))


if __name__ == "__main__":
    unittest.main()
