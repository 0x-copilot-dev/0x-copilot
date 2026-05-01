from __future__ import annotations

from dataclasses import dataclass, field

from deepagents import HarnessProfile
import pytest

from agent_runtime.execution import deep_agent_builder as builder_module
from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
    ModelThinkingMode,
)
from agent_runtime.execution.deep_agent_builder import (
    DeepAgentBuildRequest,
    build_deep_agent,
)
from tests.unit.agent_runtime.agent.helpers import FakeDeepAgentsModule


@dataclass
class CapturedChatModel:
    model: str
    model_provider: str | None
    kwargs: dict[str, object]


@dataclass
class CapturingChatModelFactory:
    calls: list[CapturedChatModel] = field(default_factory=list)

    def __call__(
        self,
        model: str,
        *,
        model_provider: str | None = None,
        **kwargs: object,
    ) -> CapturedChatModel:
        call = CapturedChatModel(
            model=model,
            model_provider=model_provider,
            kwargs=kwargs,
        )
        self.calls.append(call)
        return call


def test_web_harness_profile_excludes_write_and_execute_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, frozenset[str]]] = []

    def capture_profile(profile_key: str, profile: HarnessProfile) -> None:
        calls.append((profile_key, profile.excluded_tools))

    monkeypatch.setattr(builder_module, "register_harness_profile", capture_profile)
    monkeypatch.setattr(builder_module, "_web_harness_profiles_registered", False)

    builder_module._ensure_web_harness_profiles_registered()
    builder_module._ensure_web_harness_profiles_registered()

    assert calls == [
        ("anthropic", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
        ("gemini", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
        ("google_genai", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
        ("openai", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
    ]


def test_deep_agent_builder_configures_openai_responses_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    agent = build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="openai",
                model_name="gpt-5.4-mini",
                max_input_tokens=128_000,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    effort=ModelReasoningEffort.MEDIUM,
                    summary=ModelReasoningSummary.AUTO,
                    include_encrypted_content=True,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    assert agent == {"agent": "fake"}
    call = chat_models.calls[0]
    assert call.model == "gpt-5.4-mini"
    assert call.model_provider == "openai"
    assert call.kwargs["use_responses_api"] is True
    assert call.kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert call.kwargs["include"] == ["reasoning.encrypted_content"]
    assert call.kwargs["output_version"] == "responses/v1"
    assert "temperature" not in call.kwargs
    assert fake_deepagents.calls[0]["model"] == call


def test_deep_agent_builder_configures_claude_opus_47_adaptive_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="anthropic",
                model_name="claude-opus-4-7",
                max_input_tokens=200_000,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    effort=ModelReasoningEffort.MEDIUM,
                    display=ModelReasoningDisplay.SUMMARIZED,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    call = chat_models.calls[0]
    assert call.model == "claude-opus-4-7"
    assert call.model_provider == "anthropic"
    assert call.kwargs["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert call.kwargs["output_config"] == {"effort": "medium"}
    assert "temperature" not in call.kwargs


def test_deep_agent_builder_configures_claude_budgeted_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="anthropic",
                model_name="claude-opus-4-6",
                max_input_tokens=200_000,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    budget_tokens=10_000,
                    thinking_mode=ModelThinkingMode.ENABLED,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    assert chat_models.calls[0].kwargs["thinking"] == {
        "type": "enabled",
        "budget_tokens": 10_000,
    }
