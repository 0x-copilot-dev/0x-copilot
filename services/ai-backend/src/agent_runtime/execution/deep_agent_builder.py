"""Concrete Deep Agents construction for the runtime factory."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningEffort,
    ModelThinkingMode,
)

WEB_EXCLUDED_DEEP_AGENT_TOOLS = frozenset(
    {
        "edit_file",
        "execute",
        "write_file",
    }
)
_WEB_HARNESS_PROFILE_KEYS = (
    "anthropic",
    "gemini",
    "google_genai",
    "openai",
)
_web_harness_profiles_registered = False


def _ensure_web_harness_profiles_registered() -> None:
    """Hide unsafe or confusing Deep Agents built-ins for web runtime runs."""

    global _web_harness_profiles_registered
    if _web_harness_profiles_registered:
        return

    profile = HarnessProfile(excluded_tools=WEB_EXCLUDED_DEEP_AGENT_TOOLS)
    for profile_key in _WEB_HARNESS_PROFILE_KEYS:
        register_harness_profile(profile_key, profile)
    _web_harness_profiles_registered = True


_ensure_web_harness_profiles_registered()


@runtime_checkable
class DeepAgentsBackend(Protocol):
    """Backend protocol accepted by Deep Agents filesystem integration."""

    memory_paths: Sequence[str]

    def download_files(self, paths: list[str]) -> dict[str, str]:
        """Download files for synchronous Deep Agents calls."""

    def upload_files(self, files: dict[str, str]) -> None:
        """Upload files for synchronous Deep Agents calls."""

    async def adownload_files(self, paths: list[str]) -> dict[str, str]:
        """Download files for asynchronous Deep Agents calls."""

    async def aupload_files(self, files: dict[str, str]) -> None:
        """Upload files for asynchronous Deep Agents calls."""


@dataclass(frozen=True)
class DeepAgentBuildRequest:
    """Resolved, authorized inputs for a concrete Deep Agents instance."""

    tools: tuple[object, ...]
    model_config: ModelConfig
    system_prompt: str
    subagents: tuple[object, ...] = ()
    memory_backend: DeepAgentsBackend | None = None
    memory_paths: tuple[str, ...] = ()
    skill_directories: tuple[str, ...] = ()

    @property
    def model_name(self) -> str:
        """Return the provider-native model name for tests and diagnostics."""

        return self.model_config.model_name


def build_deep_agent(request: DeepAgentBuildRequest) -> object:
    """Build a Deep Agents graph with an explicit, version-pinned API call."""

    return create_deep_agent(
        model=build_chat_model(request.model_config),
        tools=list(request.tools),
        system_prompt=request.system_prompt,
        subagents=list(request.subagents) or None,
        skills=list(request.skill_directories) or None,
        memory=list(request.memory_paths) or None,
        backend=request.memory_backend,
    )


def build_chat_model(model_config: ModelConfig) -> BaseChatModel:
    """Create the LangChain chat model for a runtime model profile."""

    kwargs: dict[str, object] = {"timeout": model_config.timeout_seconds}
    if model_config.reasoning is None or not model_config.reasoning.enabled:
        kwargs["temperature"] = model_config.temperature
    if model_config.provider == "openai":
        kwargs.update(_openai_model_kwargs(model_config))
    elif model_config.provider == "anthropic":
        kwargs.update(_anthropic_model_kwargs(model_config))

    return init_chat_model(
        model_config.model_name,
        model_provider=_langchain_model_provider(model_config.provider),
        **kwargs,
    )


def _langchain_model_provider(provider: str) -> str:
    if provider == "gemini":
        return "google_genai"
    return provider


def _openai_model_kwargs(model_config: ModelConfig) -> dict[str, object]:
    kwargs: dict[str, object] = {"use_responses_api": True}
    reasoning = model_config.reasoning
    if reasoning is None:
        return kwargs
    if not reasoning.enabled or reasoning.effort is ModelReasoningEffort.NONE:
        kwargs["reasoning"] = None
        return kwargs

    reasoning_payload: dict[str, object] = {}
    if reasoning.effort is not None:
        reasoning_payload["effort"] = reasoning.effort.value
    if reasoning.summary is not None:
        reasoning_payload["summary"] = reasoning.summary.value
        kwargs["output_version"] = "responses/v1"
    kwargs["reasoning"] = reasoning_payload
    if reasoning.include_encrypted_content:
        kwargs["include"] = ["reasoning.encrypted_content"]
        kwargs["output_version"] = "responses/v1"
    return kwargs


def _anthropic_model_kwargs(model_config: ModelConfig) -> dict[str, object]:
    reasoning = model_config.reasoning
    if reasoning is None or not reasoning.enabled:
        return {}

    mode = reasoning.thinking_mode
    if mode is None:
        mode = (
            ModelThinkingMode.ENABLED
            if reasoning.budget_tokens is not None
            else ModelThinkingMode.ADAPTIVE
        )
    thinking: dict[str, object] = {"type": mode.value}
    if mode is ModelThinkingMode.ENABLED and reasoning.budget_tokens is not None:
        thinking["budget_tokens"] = reasoning.budget_tokens
    if mode is ModelThinkingMode.ADAPTIVE and reasoning.display is not None:
        thinking["display"] = reasoning.display.value

    kwargs: dict[str, object] = {"thinking": thinking}
    if (
        mode is ModelThinkingMode.ADAPTIVE
        and reasoning.effort is not None
        and reasoning.effort is not ModelReasoningEffort.NONE
    ):
        kwargs["output_config"] = {"effort": reasoning.effort.value}
    return kwargs
