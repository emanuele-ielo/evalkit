"""Snapshot-pinned Wonderful Chat V3 collection without platform eval judges.

EvalKit uses the platform only as the agent runtime: it opens a synthetic chat
against an immutable agent snapshot, installs the scenario's scoped tool mocks,
and records the resulting text/tool events.  Deterministic checks and LLM
judging remain local to EvalKit.
"""

from __future__ import annotations

import asyncio
import configparser
import copy
import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .config import AgentConfig
from .placeholders import render as render_placeholders


class DirectChatError(RuntimeError):
    """A direct Chat V3 collection failure safe to display to the user."""


class WfulContextClient(Protocol):
    async def whoami(self, *, timeout: float = 60.0) -> Any: ...

    async def agent_details(self, ref: str, *, timeout: float = 60.0) -> Any: ...


@dataclass(frozen=True)
class WfulCredentials:
    profile: str
    api_key: str
    base_url: str
    workspace_id: str
    tenant_id: str


def load_wful_profile(
    profile: str,
    *,
    workspace_id: str = "",
    tenant_id: str = "",
    profiles_path: str | Path | None = None,
) -> WfulCredentials:
    """Resolve the same user-build credentials as ``wful`` without logging them."""
    path = Path(profiles_path).expanduser() if profiles_path else Path.home() / ".wful" / "profiles"
    parser = configparser.RawConfigParser()
    parser.read(path)
    section = parser[profile] if parser.has_section(profile) else {}

    api_key = os.environ.get("WONDERFUL_API_KEY", "").strip() or str(section.get("api_key", "")).strip()
    base_url = os.environ.get("WONDERFUL_BASE_URL", "").strip() or str(section.get("base_url", "")).strip()
    resolved_workspace = (
        workspace_id.strip()
        or os.environ.get("WONDERFUL_WORKSPACE_ID", "").strip()
        or str(section.get("workspace_id", "")).strip()
    )
    if not api_key:
        raise DirectChatError(
            f"Wonderful profile {profile!r} has no API key; run `wful login --profile {profile}` "
            "or set WONDERFUL_API_KEY"
        )
    if not base_url:
        raise DirectChatError(f"Wonderful profile {profile!r} has no base_url")
    if not resolved_workspace:
        raise DirectChatError(f"Wonderful profile {profile!r} has no workspace id")
    if not tenant_id.strip():
        raise DirectChatError("could not resolve the selected Wonderful tenant")
    return WfulCredentials(
        profile=profile,
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        workspace_id=resolved_workspace,
        tenant_id=tenant_id.strip(),
    )


def extract_tenant_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("tenant_id") or payload.get("tenantId")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    tenants = payload.get("tenants")
    if isinstance(tenants, dict):
        selected = tenants.get("selected") or tenants.get("selected_tenant")
        if isinstance(selected, str) and selected.strip():
            return selected.strip()
        if isinstance(selected, dict):
            candidate = selected.get("id") or selected.get("tenant_id")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def extract_agent_identity(payload: Any) -> tuple[str | None, str | None]:
    """Return ``(agent_id, tenant_id)`` from the shapes emitted by wful."""
    if not isinstance(payload, dict):
        return None, None
    identity = payload.get("identity")
    candidates = [identity, payload] if isinstance(identity, dict) else [payload]
    agent_id: str | None = None
    tenant_id: str | None = None
    for candidate in candidates:
        raw_agent = candidate.get("id") or candidate.get("agent_id")
        raw_tenant = candidate.get("tenant_id") or candidate.get("tenantId")
        if agent_id is None and isinstance(raw_agent, str) and raw_agent.strip():
            agent_id = raw_agent.strip()
        if tenant_id is None and isinstance(raw_tenant, str) and raw_tenant.strip():
            tenant_id = raw_tenant.strip()
    return agent_id, tenant_id


def scoped_tool_mocks(scenario: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Serialize start and per-turn mocks with the controller's scope contract."""
    captured_now = now or datetime.now(timezone.utc)
    instructions = scenario.get("instructions") if isinstance(scenario.get("instructions"), dict) else {}
    serialized: list[dict[str, Any]] = []

    def append(raw: Any, scope: str) -> None:
        if not isinstance(raw, dict):
            raise DirectChatError(f"invalid tool mock in {scope}: expected an object")
        item = copy.deepcopy(raw)
        if "mock_output" in item:
            item["mock_output"] = render_placeholders(item["mock_output"], now=captured_now)
        item["mock_scope"] = scope
        serialized.append(item)

    for mock in instructions.get("start_tool_mocks") or []:
        append(mock, "start")
    for index, turn in enumerate(instructions.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        for mock in turn.get("tool_mocks") or []:
            append(mock, f"turn:{index}")
    return serialized


def _unwrap_data(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _public_http_error(response: httpx.Response) -> str:
    message = ""
    with suppress(Exception):
        payload = response.json()
        if isinstance(payload, dict):
            data = payload.get("data")
            candidates = [payload, data] if isinstance(data, dict) else [payload]
            for candidate in candidates:
                for key in ("message", "detail", "error"):
                    value = candidate.get(key)
                    if isinstance(value, str) and value.strip():
                        message = value.strip()
                        break
                if message:
                    break
    suffix = f": {message[:300]}" if message else ""
    return f"HTTP {response.status_code}{suffix}"


class V3ChatGateway:
    """Shared authenticated HTTP gateway for all attempts in one campaign."""

    def __init__(self, credentials: WfulCredentials, *, client: httpx.AsyncClient | None = None) -> None:
        self.credentials = credentials
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._signed_token: str | None = None
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _authorization_headers(self) -> dict[str, str]:
        if self._signed_token is None:
            async with self._token_lock:
                if self._signed_token is None:
                    self._signed_token = await self._sign_token()
        return {
            "Authorization": f"Bearer {self._signed_token}",
            "X-Tenant-ID": self.credentials.tenant_id,
            "Accept": "application/json",
        }

    async def _sign_token(self) -> str:
        url = f"{self.credentials.base_url}/api/v1/jwt/sign"
        response = await self._client.post(
            url,
            headers={
                "X-Tenant-ID": self.credentials.tenant_id,
                "x-api-key": self.credentials.api_key,
                "X-Agent-Runner-Service-Key": self.credentials.api_key,
                "Accept": "application/json",
            },
            json={
                "entityId": str(uuid.uuid4()),
                "expiresIn": 3600,
                "metadata": {"source": "evalkit", "channel": "chat"},
            },
            timeout=30,
        )
        if response.is_error:
            raise DirectChatError(f"Chat V3 token signing failed ({_public_http_error(response)})")
        data = _unwrap_data(response.json())
        token = str(data.get("jwt") or data.get("JWT") or "").strip()
        if not token:
            raise DirectChatError("Chat V3 token signing returned no jwt")
        return token

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(2):
            headers = {**(kwargs.pop("headers", {}) or {}), **(await self._authorization_headers())}
            response = await self._client.request(method, url, headers=headers, **kwargs)
            if response.status_code != 401 or attempt == 1:
                if response.is_error:
                    raise DirectChatError(f"Chat V3 request failed ({_public_http_error(response)})")
                return response
            self._signed_token = None
        raise DirectChatError("Chat V3 authentication failed")


class V3ChatSession:
    def __init__(
        self,
        gateway: V3ChatGateway,
        *,
        agent_id: str,
        snapshot_id: str,
        channel: str,
    ) -> None:
        self.gateway = gateway
        self.agent_id = agent_id
        self.snapshot_id = snapshot_id
        self.channel = channel
        self.session_id: str | None = None
        self.cursor = "0-0"

    @property
    def create_url(self) -> str:
        credentials = self.gateway.credentials
        workspace = quote(credentials.workspace_id, safe="")
        return f"{credentials.base_url}/api/workspace/{workspace}/v3/chat"

    @property
    def session_url(self) -> str:
        if self.session_id is None:
            raise DirectChatError("Chat V3 session is not initialized")
        return f"{self.create_url}/{quote(self.session_id, safe='')}"

    async def start(self, *, metadata: dict[str, Any]) -> None:
        response = await self.gateway.request(
            "POST",
            self.create_url,
            json={
                "agent_id": self.agent_id,
                "snapshot_id": self.snapshot_id,
                "send_internal_events": True,
                "comm_type": self.channel,
                "channel": "internal",
                "metadata": {
                    "is_test": True,
                    "prompt_evaluation": True,
                    "synthetic_channel": self.channel,
                    **metadata,
                },
            },
            timeout=30,
        )
        data = _unwrap_data(response.json())
        session_id = str(data.get("session_id") or "").strip()
        if not session_id:
            raise DirectChatError("Chat V3 session creation returned no session_id")
        self.session_id = session_id
        self.cursor = str(data.get("cursor") or "0-0")

    async def send_text(self, text: str, *, mock_scope: str) -> None:
        await self.gateway.request(
            "POST",
            self.session_url,
            json={
                "data": {"type": "text", "text": text},
                "metadata": {"eval_tool_mock_scope": mock_scope},
            },
            timeout=30,
        )

    async def update_metadata(self, metadata: dict[str, Any]) -> None:
        await self.gateway.request("POST", self.session_url, json={"metadata": metadata}, timeout=30)

    async def poll(self, timeout: float) -> tuple[list[dict[str, Any]], bool]:
        block_seconds = max(1, min(int(timeout), 30)) if timeout > 0 else 0
        params: dict[str, str] = {"cursor": self.cursor, "limit": "100"}
        if block_seconds:
            params["block"] = str(block_seconds)
        response = await self.gateway.request(
            "GET",
            self.session_url,
            params=params,
            timeout=block_seconds + 10 if block_seconds else 30,
        )
        data = _unwrap_data(response.json())
        if data.get("cursor"):
            self.cursor = str(data["cursor"])
        events = data.get("events")
        return (
            [event for event in events if isinstance(event, dict)] if isinstance(events, list) else [],
            bool(data.get("has_more")),
        )

    async def drain_startup(self, timeout: float = 2.0) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return events
            batch, has_more = await self.poll(min(remaining, 1.0))
            events.extend(batch)
            if has_more:
                continue
            if not batch and self.cursor != "0-0":
                return events

    async def close(self) -> None:
        if self.session_id is None:
            return
        await self.gateway.request(
            "POST",
            self.session_url,
            json={"data": {"type": "action", "action": {"type": "end"}}},
            timeout=15,
        )


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        with suppress(json.JSONDecodeError):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
    return None


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    raw_data = event.get("data")
    parsed_data = _json_object(raw_data)
    payload = dict(parsed_data) if parsed_data is not None else dict(event)
    for key in ("type", "event", "speaker", "session_id", "created_at", "timestamp", "internal_id", "id"):
        if key in event and key not in payload:
            payload[key] = event[key]
    nested = _json_object(payload.get("data"))
    if nested is not None:
        for key in (
            "type",
            "event",
            "text",
            "action",
            "function_call",
            "functionCall",
            "tool_details",
            "toolDetails",
            "turn_type",
            "run_marker_index",
        ):
            if key in nested and key not in payload:
                payload[key] = nested[key]
    return payload


def _event_type(event: dict[str, Any]) -> str:
    payload = _event_payload(event)
    return str(payload.get("type") or payload.get("event") or "").strip().lower()


def _speaker(event: dict[str, Any]) -> str:
    return str(_event_payload(event).get("speaker") or "").strip().lower()


def _is_customer_text(event: dict[str, Any]) -> bool:
    payload = _event_payload(event)
    return _speaker(event) in {"customer", "user"} and _event_type(event) == "text" and "text" in payload


def _timestamp(event: dict[str, Any]) -> str:
    payload = _event_payload(event)
    value = payload.get("created_at")
    if isinstance(value, (int, float)) and value > 0:
        with suppress(OSError, ValueError, OverflowError):
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
    raw = payload.get("timestamp")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return datetime.now(timezone.utc).isoformat()


def _alias(details: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in details and details[name] is not None:
            return details[name]
    return None


def _tool_details(event: dict[str, Any]) -> dict[str, Any] | None:
    payload = _event_payload(event)
    raw = _alias(payload, "tool_details", "toolDetails", "function_call", "functionCall")
    details = _json_object(raw)
    if details is None:
        return None
    params = _alias(details, "params", "arguments", "input")
    parsed_params = _json_object(params)
    if parsed_params is not None:
        params = parsed_params
    normalized = {
        "function_name": str(_alias(details, "function_name", "functionName", "name") or ""),
        "description": details.get("description"),
        "output": _alias(details, "output", "result"),
        "params": params,
        "is_error": bool(_alias(details, "is_error", "error") or False),
        "call_id": _alias(details, "call_id", "callId", "tool_call_id", "toolCallId", "id"),
        "internal_id": _alias(details, "internal_id", "internalId") or payload.get("internal_id"),
        "transcript_id": _alias(details, "transcript_id", "transcriptId") or payload.get("id"),
        "status": _alias(details, "status", "state", "phase"),
        "call_source": _alias(details, "call_source", "callSource"),
        "trigger_type": _alias(details, "trigger_type", "triggerType"),
        "duration_ms": _alias(details, "duration_ms", "durationMs"),
    }
    return normalized if normalized["function_name"] else None


class _ResponseAccumulator:
    def __init__(self) -> None:
        self.responses: list[dict[str, Any]] = []
        self._identity_to_index: dict[str, int] = {}
        self._tool_started_at: dict[int, datetime] = {}

    @staticmethod
    def _identities(details: dict[str, Any]) -> list[str]:
        return [
            f"{key}:{value}"
            for key in ("call_id", "internal_id", "transcript_id")
            if (value := details.get(key)) is not None and str(value).strip()
        ]

    @staticmethod
    def _is_terminal(details: dict[str, Any]) -> bool:
        status = str(details.get("status") or "").strip().lower()
        return status in {
            "completed",
            "done",
            "error",
            "errored",
            "failed",
            "finished",
            "succeeded",
            "success",
        }

    def add(self, event: dict[str, Any]) -> bool:
        speaker = _speaker(event)
        if speaker not in {"agent", "assistant", "system"}:
            return False
        payload = _event_payload(event)
        details = _tool_details(event)
        event_timestamp = _timestamp(event)
        if details is not None:
            self._add_tool(speaker, str(payload.get("text") or ""), details, event_timestamp)
            return True
        text = str(payload.get("text") or "").strip()
        if not text:
            return False
        self.responses.append(
            {
                "speaker": "agent" if speaker == "assistant" else speaker,
                "text": text,
                "timestamp": event_timestamp,
                "source": "evalkit_chat_v3",
            }
        )
        return True

    def _add_tool(self, speaker: str, text: str, details: dict[str, Any], timestamp: str) -> None:
        identities = self._identities(details)
        index = next((self._identity_to_index[value] for value in identities if value in self._identity_to_index), None)
        if index is None:
            index = next(
                (
                    candidate
                    for candidate in range(len(self.responses) - 1, -1, -1)
                    if isinstance(self.responses[candidate].get("tool_details"), dict)
                    and self.responses[candidate]["tool_details"].get("function_name") == details["function_name"]
                    and self.responses[candidate]["tool_details"].get("output") is None
                    and not self._is_terminal(self.responses[candidate]["tool_details"])
                ),
                None,
            )
        if index is None:
            index = len(self.responses)
            self.responses.append(
                {
                    "speaker": "agent" if speaker == "assistant" else speaker,
                    "text": text,
                    "tool_details": details,
                    "timestamp": timestamp,
                    "source": "evalkit_chat_v3",
                }
            )
            with suppress(ValueError):
                self._tool_started_at[index] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            existing = self.responses[index]["tool_details"]
            for key, value in details.items():
                if value is not None and (not isinstance(value, str) or value.strip()):
                    existing[key] = value
            if text:
                self.responses[index]["text"] = text
            if existing.get("duration_ms") is None and index in self._tool_started_at:
                with suppress(ValueError):
                    finished = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    existing["duration_ms"] = round(
                        max((finished - self._tool_started_at[index]).total_seconds() * 1000, 0),
                        2,
                    )
        for identity in self._identities(self.responses[index]["tool_details"]):
            self._identity_to_index[identity] = index


class DirectChatCollector:
    """Collect authored chat scenarios directly from a snapshot-pinned agent."""

    def __init__(
        self,
        *,
        gateway: V3ChatGateway,
        agent_id: str,
        snapshot_id: str,
        repo: Path,
    ) -> None:
        self.gateway = gateway
        self.agent_id = agent_id
        self.snapshot_id = snapshot_id
        self.repo = repo

    @classmethod
    async def create(
        cls,
        agent: AgentConfig,
        client: WfulContextClient,
        *,
        snapshot_id: str,
    ) -> "DirectChatCollector":
        if not agent.repo:
            raise DirectChatError("direct collection requires [agents.*].repo")
        if not snapshot_id.strip():
            raise DirectChatError("direct collection requires an agent snapshot id")
        whoami = await client.whoami()
        tenant_id = extract_tenant_id(whoami)
        agent_id = agent.id
        details: Any = None
        if not agent_id or not tenant_id:
            details = await client.agent_details(agent.slug)
            details_agent_id, details_tenant_id = extract_agent_identity(details)
            agent_id = agent_id or details_agent_id
            tenant_id = tenant_id or details_tenant_id
        if not agent_id:
            raise DirectChatError(f"could not resolve id for agent {agent.slug!r}")
        credentials = load_wful_profile(
            agent.wful_profile,
            workspace_id=agent.workspace,
            tenant_id=tenant_id or "",
        )
        return cls(
            gateway=V3ChatGateway(credentials),
            agent_id=agent_id,
            snapshot_id=snapshot_id,
            repo=Path(agent.repo).expanduser(),
        )

    async def aclose(self) -> None:
        await self.gateway.aclose()

    async def collect(self, scenario_slug: str, *, timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            raise DirectChatError("scenario timeout must be greater than zero")
        path = self.repo / "evals" / "scenarios" / f"{scenario_slug}.json"
        if not path.exists():
            raise DirectChatError(f"scenario file not found: {path}")
        try:
            scenario = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DirectChatError(f"invalid scenario JSON at {path}: {exc}") from exc
        if not isinstance(scenario, dict):
            raise DirectChatError(f"scenario must be a JSON object: {path}")
        try:
            async with asyncio.timeout(timeout):
                return await self._collect(scenario_slug, scenario, timeout=timeout)
        except TimeoutError as exc:
            raise DirectChatError(f"scenario {scenario_slug} timed out after {timeout:g}s") from exc

    async def _collect(self, scenario_slug: str, scenario: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        channel = str(scenario.get("channel") or "chat").lower()
        eval_type = str(scenario.get("eval_type") or "chat_eval").lower()
        if channel != "chat" or eval_type not in {"chat", "chat_eval"}:
            raise DirectChatError(
                f"direct collection currently supports chat/chat_eval only, got {channel}/{eval_type}"
            )
        instructions = scenario.get("instructions")
        if not isinstance(instructions, dict):
            raise DirectChatError(f"scenario {scenario_slug} has no instructions object")
        turns = instructions.get("turns")
        if not isinstance(turns, list) or not turns:
            raise DirectChatError(f"scenario {scenario_slug} has no authored turns")

        session = V3ChatSession(
            self.gateway,
            agent_id=self.agent_id,
            snapshot_id=self.snapshot_id,
            channel=channel,
        )
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        mocks = scoped_tool_mocks(scenario, now=started_at)
        await session.start(
            metadata={
                "eval_tool_mocks": mocks,
                "eval_tool_mock_scope": "start",
                "evalkit_direct_collection": True,
            }
        )
        startup_events = await session.drain_startup()
        startup = _ResponseAccumulator()
        for event in startup_events:
            startup.add(event)

        turn_results: list[dict[str, Any]] = []
        try:
            for index, raw_turn in enumerate(turns):
                if not isinstance(raw_turn, dict):
                    raise DirectChatError(f"scenario {scenario_slug} turn {index} is not an object")
                wait_before = raw_turn.get("wait_before_seconds")
                if isinstance(wait_before, (int, float)) and wait_before > 0:
                    await asyncio.sleep(float(wait_before))
                turn_started = datetime.now(timezone.utc)
                accumulator = _ResponseAccumulator()
                if index == 0:
                    for response in startup.responses:
                        accumulator.responses.append(copy.deepcopy(response))
                boundary = await self._collect_turn(
                    session,
                    raw_turn,
                    index=index,
                    accumulator=accumulator,
                    timeout=min(timeout, 60.0 if not str(raw_turn.get("user_message") or "") else 300.0),
                )
                finished = datetime.now(timezone.utc)
                unique_tool_calls = sum(1 for response in accumulator.responses if response.get("tool_details"))
                turn_results.append(
                    {
                        "evaluation_turn": raw_turn,
                        "no_user_message": not bool(str(raw_turn.get("user_message") or "")),
                        "evaluation_passed": None,
                        "failing_evaluation": None,
                        "failure_reason": None,
                        "turn_judge_prompt": raw_turn.get("expected_output") or None,
                        "turn_state": {
                            "start_time": turn_started.isoformat(),
                            "last_message_time": finished.isoformat() if accumulator.responses else None,
                            "last_function_call_time": finished.isoformat() if unique_tool_calls else None,
                            "message_count": len(accumulator.responses),
                            "function_called_count": unique_tool_calls,
                            "agent_responses": accumulator.responses,
                            "heard_user_transcripts": [],
                            "llm_judge_evaluation_results": [],
                            "turn_end_metadata": {
                                "boundary_reason": boundary,
                                "collector": "evalkit_chat_v3_direct",
                                "platform_judge_invoked": False,
                            },
                            "turn_end_time": finished.isoformat(),
                        },
                    }
                )
        finally:
            with suppress(Exception):
                await session.close()

        execution_time_ms = int((time.monotonic() - started_monotonic) * 1000)
        return {
            "id": str(uuid.uuid4()),
            "scenario_id": scenario.get("id"),
            "scenario_name": scenario.get("name") or scenario_slug,
            "scenario_slug": scenario_slug,
            "scenario_definition": scenario,
            "agent_id": self.agent_id,
            "agent_repo_snapshot_id": self.snapshot_id,
            "communication_id": session.session_id,
            "run_status": "completed",
            "passed": None,
            "failed_at_turn": None,
            "execution_time_ms": execution_time_ms,
            "turn_results": turn_results,
            "eval_type": scenario.get("eval_type") or "chat_eval",
            "conversation_mode": instructions.get("mode") or "turn_based",
            "conversation_judge_result": None,
            "channel": channel,
            "created_at": started_at.isoformat(),
            "collector": {
                "backend": "wonderful_chat_v3_direct",
                "platform_eval_invoked": False,
                "platform_judge_invoked": False,
            },
        }

    async def _collect_turn(
        self,
        session: V3ChatSession,
        turn: dict[str, Any],
        *,
        index: int,
        accumulator: _ResponseAccumulator,
        timeout: float,
    ) -> str:
        user_message = str(turn.get("user_message") or "")
        scope = f"turn:{index}"
        awaiting_customer_echo = bool(user_message)
        expected_turn_type = "user" if awaiting_customer_echo else "trigger"
        if awaiting_customer_echo:
            await session.send_text(user_message, mock_scope=scope)
        else:
            await session.update_metadata({"eval_tool_mock_scope": scope})

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DirectChatError(f"turn {index + 1} timed out after {timeout:g}s")
            events, has_more = await session.poll(min(remaining, 30.0))
            for event in events:
                if awaiting_customer_echo:
                    if _is_customer_text(event):
                        awaiting_customer_echo = False
                    continue
                if _event_type(event) == "turn_ended":
                    payload = _event_payload(event)
                    turn_type = str(payload.get("turn_type") or "user").strip().lower()
                    if turn_type == expected_turn_type:
                        return "turn_ended"
                    continue
                accumulator.add(event)
            if has_more:
                continue


__all__ = [
    "DirectChatCollector",
    "DirectChatError",
    "V3ChatGateway",
    "V3ChatSession",
    "WfulCredentials",
    "extract_agent_identity",
    "extract_tenant_id",
    "load_wful_profile",
    "scoped_tool_mocks",
]
