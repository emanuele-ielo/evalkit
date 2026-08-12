"""Configuration: `evalkit.toml` (+ optional `evalkit.local.toml` overlay).

Secrets never live in config. The judge's credentials are read at call time from
the env file named in `[llm].env_file` (or from the process environment, which
wins), and are never logged, echoed, or written to disk.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "evalkit.toml"
LOCAL_CONFIG = REPO_ROOT / "evalkit.local.toml"


class ConfigError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    model: str = "claude-sonnet-5"
    reasoning_effort: str = "medium"
    votes: int = 3
    concurrency: int = 6
    max_retries: int = 3
    requests_per_second: float = 3.0
    max_output_tokens: int = 16000
    # Files searched, in order, for the provider credentials the judge needs
    # (OPENAI_API_KEY / ANTHROPIC_API_KEY). Read at call time, never logged.
    env_files: tuple[str, ...] = (
        "~/GIT/wonderful/wonderful/env_files/agents.env",
        "~/GIT/wonderful/wonderful/env_files/controller.env",
    )


@dataclass
class AgentConfig:
    name: str
    slug: str
    id: str | None = None
    wful_profile: str = "default"
    workspace: str = ""
    repo: str | None = None
    batch: str | None = None
    concurrency: int = 4
    rounds: int = 3


@dataclass
class Config:
    data_dir: Path
    llm: LLMConfig
    required_criteria: list[str]
    # A scored verdict still needs a binary read to compare with the platform
    # judge: an attempt "passes" at mean >= pass_threshold with no criterion
    # below min_criterion_score.
    pass_threshold: float = 4.0
    min_criterion_score: int = 3
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    source_files: list[Path] = field(default_factory=list)

    def agent(self, name: str) -> AgentConfig:
        try:
            return self.agents[name]
        except KeyError:
            known = ", ".join(sorted(self.agents)) or "none configured"
            raise ConfigError(f"unknown agent profile {name!r} (known: {known})") from None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None, *, data_dir: str | Path | None = None) -> Config:
    """Read config from `path` (default `evalkit.toml`), overlaid with
    `evalkit.local.toml` when present."""
    main = Path(path).expanduser() if path else DEFAULT_CONFIG
    if not main.exists():
        raise ConfigError(f"config file not found: {main}")
    sources = [main]
    raw = tomllib.loads(main.read_text())
    if not path and LOCAL_CONFIG.exists():
        raw = _deep_merge(raw, tomllib.loads(LOCAL_CONFIG.read_text()))
        sources.append(LOCAL_CONFIG)

    llm_raw = raw.get("llm", {})
    configured_files = llm_raw.get("env_files")
    if configured_files is None:
        single = llm_raw.get("env_file")
        configured_files = [single] if single else list(LLMConfig.env_files)
    llm = LLMConfig(
        model=llm_raw.get("model", LLMConfig.model),
        reasoning_effort=llm_raw.get("reasoning_effort", LLMConfig.reasoning_effort),
        votes=int(llm_raw.get("votes", LLMConfig.votes)),
        concurrency=int(llm_raw.get("concurrency", LLMConfig.concurrency)),
        max_retries=int(llm_raw.get("max_retries", LLMConfig.max_retries)),
        requests_per_second=float(llm_raw.get("requests_per_second", LLMConfig.requests_per_second)),
        max_output_tokens=int(llm_raw.get("max_output_tokens", LLMConfig.max_output_tokens)),
        env_files=tuple(str(path) for path in configured_files),
    )

    agents: dict[str, AgentConfig] = {}
    for name, cfg in (raw.get("agents") or {}).items():
        if "slug" not in cfg:
            raise ConfigError(f"[agents.{name}] is missing 'slug'")
        agents[name] = AgentConfig(
            name=name,
            slug=cfg["slug"],
            id=cfg.get("id"),
            wful_profile=cfg.get("wful_profile", "default"),
            workspace=cfg.get("workspace", ""),
            repo=cfg.get("repo"),
            batch=cfg.get("batch"),
            concurrency=int(cfg.get("concurrency", 4)),
            rounds=int(cfg.get("rounds", 3)),
        )

    resolved_data = Path(data_dir).expanduser() if data_dir else None
    if resolved_data is None:
        configured = (raw.get("paths") or {}).get("data_dir", "data")
        resolved_data = Path(configured).expanduser()
        if not resolved_data.is_absolute():
            resolved_data = REPO_ROOT / resolved_data

    return Config(
        data_dir=resolved_data,
        llm=llm,
        required_criteria=list((raw.get("rubric") or {}).get("required", ["grounding", "completeness", "clauses", "customer_care"])),
        pass_threshold=float((raw.get("rubric") or {}).get("pass_threshold", 4.0)),
        min_criterion_score=int((raw.get("rubric") or {}).get("min_criterion_score", 3)),
        agents=agents,
        source_files=sources,
    )


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a `KEY=value` env file. Values may be single/double quoted.

    Returns the parsed mapping — callers must not log it: it holds credentials.
    """
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return {}
    out: dict[str, str] = {}
    for line in resolved.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


@dataclass
class LLMCredentials:
    provider: str
    api_key: str
    base_url: str | None

    def redacted(self) -> str:
        """A safe one-liner for logs: provider, host and key length — never the key."""
        host = self.base_url or ("https://api.anthropic.com" if self.provider == "anthropic" else "https://api.openai.com")
        return f"{self.provider} @ {host} (key len {len(self.api_key)})"


def provider_for_model(model: str) -> str:
    """Which vendor a model id belongs to."""
    lowered = model.lower()
    if lowered.startswith("claude") or lowered.startswith("anthropic"):
        return "anthropic"
    return "openai"


def load_llm_credentials(llm: LLMConfig, model: str | None = None) -> LLMCredentials:
    """Resolve judge credentials for the model's provider.

    The process environment wins; otherwise every file in `[llm].env_files` is
    searched in order. Raises naming the files consulted — never their contents.
    """
    provider = provider_for_model(model or llm.model)
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    url_name = "ANTHROPIC_BASE_URL" if provider == "anthropic" else "OPENAI_BASE_URL"

    api_key = os.environ.get(key_name, "").strip()
    base_url = os.environ.get(url_name, "").strip() or None
    if not api_key:
        for candidate in llm.env_files:
            parsed = parse_env_file(candidate)
            if parsed.get(key_name, "").strip():
                api_key = parsed[key_name].strip()
                base_url = base_url or (parsed.get(url_name, "").strip() or None)
                break
    if not api_key:
        searched = ", ".join(str(Path(path).expanduser()) for path in llm.env_files)
        raise ConfigError(
            f"no {key_name} found for judge model {model or llm.model!r}: "
            f"set it in the environment or in one of [llm].env_files ({searched})"
        )
    return LLMCredentials(provider=provider, api_key=api_key, base_url=base_url)
