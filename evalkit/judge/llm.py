"""LLM transport for the judge, on LangChain — OpenAI and Anthropic.

LangChain earns its place here by making the provider a config value: structured
output with schema validation and retry, usage metadata without hand-rolled
accounting, and one rate limiter, all behind the same call. Switching the judge
from `gpt-5` to `claude-sonnet-5` is a string in `evalkit.toml`.

Per-provider transport rules, which are the parts that actually differ:

* **Anthropic** (`claude-*`): `max_tokens` is required and caps thinking **plus**
  response text, so it is set generously. Non-default `temperature` is rejected
  on Claude 5 models — never sent. Thinking is adaptive by default on Sonnet 5,
  which is what a grader wants. Structured output uses Claude's dedicated
  `json_schema` feature, not LangChain's default forced tool calling — the latter
  does not enforce the schema and silently drops required fields.
  Prompt caching is deliberately **not** used: an attempt's N votes fire
  concurrently with an identical prompt, so none can read what the others are
  still writing, and setting a breakpoint would only add N cache-write premiums.
* **OpenAI reasoning models** (`gpt-5*`, `o*`): driven through the Responses API
  with `reasoning={effort, summary}` and no temperature — the same transport the
  platform judge uses. Structured output uses strict `json_schema`.
* **Other OpenAI models**: chat completions at temperature 0.

Credentials arrive as `LLMCredentials` read at call time; nothing here logs them,
and `describe()` only ever reports provider, host and key length.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Type, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from pydantic import BaseModel

from ..config import LLMCredentials
from ..schemas import TokenUsage

T = TypeVar("T", bound=BaseModel)

REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# LangChain's `include_raw=True` envelope declares `parsed: None`, so pydantic
# warns every time a parsed model is put there. Nothing actionable for us.
warnings.filterwarnings("ignore", message=".*serialized value may not be as expected.*")


class JudgeLLMError(RuntimeError):
    pass


def is_reasoning_model(model: str) -> bool:
    lowered = model.lower()
    return any(lowered.startswith(prefix) for prefix in REASONING_PREFIXES)


@dataclass
class VoteResult:
    value: Any
    usage: TokenUsage
    duration_ms: float


@dataclass
class JudgeLLM:
    """One configured judge model, shared across votes and attempts."""

    credentials: LLMCredentials
    model: str = "claude-sonnet-5"
    reasoning_effort: str = "medium"
    max_retries: int = 3
    requests_per_second: float = 3.0
    max_output_tokens: int = 16000
    timeout: float = 600.0
    _client: BaseChatModel = field(init=False, repr=False)
    _structured_kwargs: dict[str, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        limiter = None
        if self.requests_per_second > 0:
            limiter = InMemoryRateLimiter(
                requests_per_second=self.requests_per_second,
                check_every_n_seconds=0.1,
                max_bucket_size=max(1.0, self.requests_per_second),
            )
        if self.credentials.provider == "anthropic":
            self._client = self._build_anthropic(limiter)
            # Claude's dedicated structured-output feature. The default here is
            # forced tool calling, which does NOT enforce the schema — it drops
            # required fields (observed: a vote missing `grounding_passed`).
            self._structured_kwargs = {"method": "json_schema", "include_raw": True}
        else:
            self._client = self._build_openai(limiter)
            self._structured_kwargs = {"method": "json_schema", "strict": True, "include_raw": True}

    def _build_anthropic(self, limiter: InMemoryRateLimiter | None) -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": self.credentials.api_key,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            # Required by the API, and it caps thinking + text together.
            "max_tokens": self.max_output_tokens,
        }
        if self.credentials.base_url:
            kwargs["base_url"] = self.credentials.base_url
        if limiter is not None:
            kwargs["rate_limiter"] = limiter
        return ChatAnthropic(**kwargs)

    def _build_openai(self, limiter: InMemoryRateLimiter | None) -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": self.credentials.api_key,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
        }
        if self.credentials.base_url:
            kwargs["base_url"] = self.credentials.base_url
        if limiter is not None:
            kwargs["rate_limiter"] = limiter
        if is_reasoning_model(self.model):
            kwargs["use_responses_api"] = True
            kwargs["reasoning"] = {"effort": self.reasoning_effort, "summary": "auto"}
        else:
            kwargs["temperature"] = 0.0
        return ChatOpenAI(**kwargs)

    def describe(self) -> str:
        if self.credentials.provider == "anthropic":
            transport = f"messages/adaptive-thinking, max_tokens {self.max_output_tokens}"
        elif is_reasoning_model(self.model):
            transport = f"responses+reasoning={self.reasoning_effort}"
        else:
            transport = "chat/temperature=0"
        return f"{self.model} via {self.credentials.redacted()} [{transport}]"

    async def vote(self, system_prompt: str, user_prompt: str, schema: Type[T]) -> VoteResult:
        """One structured judgement. Raises `JudgeLLMError` if it cannot be parsed."""
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        started = time.monotonic()
        try:
            structured = self._client.with_structured_output(schema, **self._structured_kwargs)
            envelope = await structured.ainvoke(messages)
        except Exception as exc:  # unsupported method, transport error, …
            try:
                structured = self._client.with_structured_output(schema, include_raw=True)
                envelope = await structured.ainvoke(messages)
            except Exception as fallback_exc:
                raise JudgeLLMError(f"{type(exc).__name__}: {exc}; fallback failed: {fallback_exc}") from fallback_exc

        duration_ms = (time.monotonic() - started) * 1000
        if not isinstance(envelope, dict):
            return VoteResult(value=envelope, usage=TokenUsage(model=self.model), duration_ms=duration_ms)
        parsed = envelope.get("parsed")
        if parsed is None:
            error = envelope.get("parsing_error")
            raw = envelope.get("raw")
            hint = ""
            # A truncated response is the likeliest cause on Anthropic: thinking
            # plus the verdict overran max_tokens.
            if getattr(raw, "response_metadata", None):
                stop = raw.response_metadata.get("stop_reason") or raw.response_metadata.get("finish_reason")
                if stop in {"max_tokens", "length"}:
                    hint = f" (response hit the {self.max_output_tokens}-token cap — raise [llm].max_output_tokens)"
            raise JudgeLLMError(f"structured output did not parse: {error}{hint}")
        return VoteResult(value=parsed, usage=self._usage(envelope.get("raw")), duration_ms=duration_ms)

    def _usage(self, raw: Any) -> TokenUsage:
        usage = getattr(raw, "usage_metadata", None) or {}
        input_details = usage.get("input_token_details") or {}
        output_details = usage.get("output_token_details") or {}
        return TokenUsage(
            provider=self.credentials.provider,
            model=self.model,
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cache_read") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(output_details.get("reasoning") or 0),
        )
