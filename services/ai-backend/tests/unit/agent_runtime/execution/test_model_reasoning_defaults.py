"""Tests for the resolver's default reasoning-summary injection.

Pins the contract that a native OpenAI reasoning model resolves to a
``ModelReasoningConfig`` that requests a summary even when neither the request
nor the deployment default carries a reasoning config. Without this the builder
never asks OpenAI for ``reasoning.summary``, so the Responses API emits no
``reasoning_summary_text_delta`` and the Focus/Studio transcript shows no
thinking block.
"""

from __future__ import annotations


from agent_runtime.execution.contracts import (
    ModelReasoningConfig,
    ModelReasoningEffort,
    ModelReasoningSummary,
)
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.settings import RuntimeSettings


# Point ``load`` at nonexistent env files so it ignores the developer's local
# ``.env`` / ``env_example`` (which set ``RUNTIME_DEFAULT_REASONING_SUMMARY``).
# This reproduces the desktop/production posture the bug lives in: NO deployment
# default reasoning config, so the resolver's synthesized default is what runs.
_NO_ENV_FILE = "/nonexistent/does-not-exist.env"


def _settings(
    *,
    provider: str = "openai",
    model: str = "gpt-5.4-mini",
    extra_env: dict[str, str] | None = None,
) -> RuntimeSettings:
    environ = {
        "OPENAI_API_KEY": "sk-test",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "GOOGLE_API_KEY": "g-test",
        "OPENROUTER_API_KEY": "sk-or-test",
        "RUNTIME_DEFAULT_PROVIDER": provider,
        "RUNTIME_DEFAULT_MODEL": model,
    }
    if extra_env:
        environ.update(extra_env)
    return RuntimeSettings.load(
        env_file=_NO_ENV_FILE,
        template_file=_NO_ENV_FILE,
        environ=environ,
    )


class TestResolverDefaultsReasoningSummary:
    def test_openai_reasoning_model_defaults_to_auto_summary(self) -> None:
        resolver = ModelConfigResolver(settings=_settings())
        config = resolver.resolve(
            ModelSelection(provider="openai", model_name="gpt-5.4-mini"),
        )
        assert config.reasoning is not None
        assert config.reasoning.enabled is True
        assert config.reasoning.summary is ModelReasoningSummary.AUTO
        # Effort stays unset so OpenAI applies its own default.
        assert config.reasoning.effort is None

    def test_default_model_selection_inherits_summary(self) -> None:
        """A bare selection resolves the settings default model (gpt-5.4-mini)."""

        resolver = ModelConfigResolver(settings=_settings())
        config = resolver.resolve(ModelSelection())
        assert config.model_name == "gpt-5.4-mini"
        assert config.reasoning is not None
        assert config.reasoning.summary is ModelReasoningSummary.AUTO

    def test_gpt5_and_o_series_are_reasoning_capable(self) -> None:
        resolver = ModelConfigResolver(settings=_settings())
        for model_name in ("gpt-5", "gpt-5.6", "o1", "o3-mini", "o4-mini"):
            config = resolver.resolve(
                ModelSelection(provider="openai", model_name=model_name),
            )
            assert config.reasoning is not None, model_name
            assert config.reasoning.summary is ModelReasoningSummary.AUTO, model_name

    def test_explicit_request_reasoning_wins(self) -> None:
        resolver = ModelConfigResolver(settings=_settings())
        requested = ModelReasoningConfig(
            effort=ModelReasoningEffort.HIGH,
            summary=ModelReasoningSummary.DETAILED,
        )
        config = resolver.resolve(
            ModelSelection(
                provider="openai",
                model_name="gpt-5.4-mini",
                reasoning=requested,
            ),
        )
        assert config.reasoning == requested

    def test_deployment_default_reasoning_wins_over_synthesized(self) -> None:
        resolver = ModelConfigResolver(
            settings=_settings(extra_env={"RUNTIME_DEFAULT_REASONING_EFFORT": "low"})
        )
        config = resolver.resolve(
            ModelSelection(provider="openai", model_name="gpt-5.4-mini"),
        )
        assert config.reasoning is not None
        assert config.reasoning.effort is ModelReasoningEffort.LOW
        # The deployment default carried no summary; we do NOT force one on it.
        assert config.reasoning.summary is None

    def test_non_reasoning_openai_model_stays_none(self) -> None:
        resolver = ModelConfigResolver(settings=_settings())
        for model_name in ("gpt-4o", "gpt-4.1", "gpt-5-chat-latest"):
            config = resolver.resolve(
                ModelSelection(provider="openai", model_name=model_name),
            )
            assert config.reasoning is None, model_name

    def test_anthropic_model_not_defaulted(self) -> None:
        resolver = ModelConfigResolver(settings=_settings())
        config = resolver.resolve(
            ModelSelection(provider="anthropic", model_name="claude-opus-4-8"),
        )
        assert config.reasoning is None

    def test_gemini_model_not_defaulted(self) -> None:
        resolver = ModelConfigResolver(settings=_settings())
        config = resolver.resolve(
            ModelSelection(provider="gemini", model_name="gemini-2.5-pro"),
        )
        assert config.reasoning is None

    def test_openrouter_gateway_not_defaulted(self) -> None:
        """OpenRouter is Chat-Completions-only; a reasoning kwarg would 400."""

        resolver = ModelConfigResolver(settings=_settings())
        config = resolver.resolve(
            ModelSelection(
                provider="openrouter",
                model_name="openai/gpt-5",
            ),
        )
        assert config.reasoning is None
