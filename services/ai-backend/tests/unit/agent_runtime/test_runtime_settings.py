from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.execution.contracts import (
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.settings import RuntimeSettings


def test_runtime_settings_loads_template_env_and_process_overrides(
    tmp_path: Path,
) -> None:
    template = tmp_path / "env_example"
    env_file = tmp_path / ".env"
    template.write_text(
        "\n".join(
            (
                "RUNTIME_DEFAULT_PROVIDER=openai",
                "RUNTIME_DEFAULT_MODEL=gpt-4.1-mini",
                "RUNTIME_DEFAULT_TEMPERATURE=0",
                "RUNTIME_DEFAULT_TIMEOUT_SECONDS=60",
                "RUNTIME_MAX_RETRIES=2",
                "RUNTIME_MAX_PARALLEL_RUNS=4",
                "RUNTIME_MAX_PARALLEL_SUBAGENTS=4",
            )
        ),
        encoding="utf-8",
    )
    env_file.write_text(
        "\n".join(
            (
                "RUNTIME_DEFAULT_MODEL=gpt-4.1",
                "RUNTIME_MAX_PARALLEL_RUNS=8",
            )
        ),
        encoding="utf-8",
    )

    settings = RuntimeSettings.load(
        template_file=template,
        env_file=env_file,
        environ={"OPENAI_API_KEY": "sk-test", "RUNTIME_MAX_PARALLEL_SUBAGENTS": "6"},
    )

    assert settings.default_model.provider == "openai"
    assert settings.default_model.model_name == "gpt-4.1"
    assert settings.execution.max_retries == 2
    assert settings.execution.max_parallel_runs == 8
    assert settings.execution.max_parallel_subagents == 6
    assert settings.execution.allow_empty_capabilities is False
    assert settings.openai.is_configured
    assert "sk-test" not in repr(settings)
    assert "api_key" not in settings.model_dump()["openai"]


def test_runtime_settings_loads_default_reasoning_config() -> None:
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4",
            "RUNTIME_DEFAULT_REASONING_EFFORT": "medium",
            "RUNTIME_DEFAULT_REASONING_SUMMARY": "auto",
            "RUNTIME_DEFAULT_REASONING_INCLUDE_ENCRYPTED_CONTENT": "true",
        }
    )

    reasoning = settings.default_model.reasoning
    assert reasoning is not None
    assert reasoning.effort is ModelReasoningEffort.MEDIUM
    assert reasoning.summary is ModelReasoningSummary.AUTO
    assert reasoning.include_encrypted_content is True


def test_model_resolver_validates_provider_keys_and_applies_defaults() -> None:
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-anthropic",
            "GOOGLE_API_KEY": "sk-google",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
        }
    )
    resolver = ModelConfigResolver(settings)

    openai = resolver.resolve(ModelSelection(model_name="gpt-4.1-mini"))
    anthropic = resolver.resolve(
        ModelSelection(provider="anthropic", model_name="claude-sonnet-4")
    )
    gemini = resolver.resolve(
        ModelSelection(provider="google", model_name="gemini-2.5-pro")
    )

    assert openai.provider == "openai"
    assert anthropic.provider == "anthropic"
    assert gemini.provider == "gemini"


def test_model_resolver_applies_request_reasoning_override() -> None:
    settings = RuntimeSettings.load(
        environ={
            "ANTHROPIC_API_KEY": "sk-anthropic",
            "RUNTIME_DEFAULT_PROVIDER": "anthropic",
            "RUNTIME_DEFAULT_MODEL": "claude-opus-4-7",
            "RUNTIME_DEFAULT_REASONING_DISPLAY": "omitted",
        }
    )
    resolver = ModelConfigResolver(settings)

    resolved = resolver.resolve(
        ModelSelection(
            model_name="claude-opus-4-7",
            reasoning=ModelReasoningConfig(
                effort=ModelReasoningEffort.HIGH,
                display=ModelReasoningDisplay.SUMMARIZED,
            ),
        )
    )

    assert resolved.provider == "anthropic"
    assert resolved.reasoning is not None
    assert resolved.reasoning.effort is ModelReasoningEffort.HIGH
    assert resolved.reasoning.display is ModelReasoningDisplay.SUMMARIZED


def test_model_resolver_rejects_missing_provider_key() -> None:
    settings = RuntimeSettings.load(environ={})
    resolver = ModelConfigResolver(settings)

    with pytest.raises(AgentRuntimeError) as exc_info:
        resolver.resolve(
            ModelSelection(provider="anthropic", model_name="claude-sonnet-4")
        )

    assert exc_info.value.code == "configuration_error"
    assert "Missing API key" in exc_info.value.safe_message
