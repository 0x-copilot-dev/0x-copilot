"""Run-path tests for the user-supplied custom OpenAI-compatible endpoint (D-2).

Proves the run path recognizes the ``openai_compatible`` slug, that its
credential gate is satisfied by a stored BYOK key (and fails closed without
one), that the stored ``base_url`` is injected at model construction, and that
a missing base_url fails closed to a CONFIGURATION_ERROR rather than silently
hitting api.openai.com.
"""

from __future__ import annotations

import pytest

from agent_runtime.execution import deep_agent_builder
from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    RuntimeErrorCode,
)
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.execution.openai_compat import (
    CUSTOM_OPENAI_COMPATIBLE_PROVIDER,
    OpenAICompatibleProviders,
)
from agent_runtime.execution.provider_kwargs import user_policy_model_kwargs
from agent_runtime.settings import RuntimeSettings

_SLUG = CUSTOM_OPENAI_COMPATIBLE_PROVIDER
_BASE_URL = "https://vllm.example/v1"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(environ={})


class TestProviderRecognition:
    def test_canonical_and_supports(self) -> None:
        assert ModelConfigResolver.canonical_provider("openai_compatible") == _SLUG
        # The underscore/hyphen normalization both round-trips to the slug.
        assert ModelConfigResolver.canonical_provider("openai-compatible") == _SLUG
        assert ModelConfigResolver.supports_provider("openai_compatible") is True

    def test_is_custom_but_not_static_registry(self) -> None:
        assert OpenAICompatibleProviders.is_custom(_SLUG) is True
        assert OpenAICompatibleProviders.is_compatible(_SLUG) is True
        # No fixed base_url — it is resolved per-run.
        assert OpenAICompatibleProviders.get(_SLUG) is None

    def test_provider_settings_returns_empty_not_raise(self) -> None:
        settings = _settings()
        provider_settings = settings.provider_settings(_SLUG)
        assert provider_settings.is_configured is False


class TestCredentialGate:
    def test_user_key_satisfies_gate(self) -> None:
        resolver = ModelConfigResolver(_settings())
        config = resolver.resolve(
            ModelSelection(provider="openai_compatible", model_name="llama-3.1-70b"),
            user_key_providers=frozenset({_SLUG}),
        )
        assert config.provider == _SLUG
        assert config.model_name == "llama-3.1-70b"

    def test_fails_closed_without_a_key(self) -> None:
        resolver = ModelConfigResolver(_settings())
        with pytest.raises(AgentRuntimeError) as excinfo:
            resolver.resolve(
                ModelSelection(
                    provider="openai_compatible", model_name="llama-3.1-70b"
                ),
                user_key_providers=frozenset(),
            )
        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR


class TestBaseUrlInjection:
    def test_kwargs_inject_base_url_and_key(self) -> None:
        kwargs = user_policy_model_kwargs(
            provider=_SLUG,
            user_policies_json=None,
            provider_keys={_SLUG: "sk-user"},
            provider_endpoints={_SLUG: _BASE_URL},
        )
        assert kwargs["base_url"] == _BASE_URL
        assert kwargs["api_key"] == "sk-user"

    def test_native_provider_ignores_endpoint_map(self) -> None:
        kwargs = user_policy_model_kwargs(
            provider="openai",
            user_policies_json=None,
            provider_keys={"openai": "sk-native"},
            provider_endpoints={_SLUG: _BASE_URL},
        )
        assert "base_url" not in kwargs
        assert kwargs["api_key"] == "sk-native"


class _RecordingModel:
    pass


class TestBuildChatModelCustom:
    def _config(self) -> ModelConfig:
        return ModelConfig(
            provider=_SLUG,
            model_name="llama-3.1-70b",
            max_input_tokens=8192,
            max_output_tokens=1024,
            timeout_seconds=30.0,
            temperature=0.2,
            supports_streaming=True,
            reasoning=ModelReasoningConfig(enabled=False),
        )

    def test_builds_openai_client_with_base_url_and_chat_completions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_init(model_name, *, model_provider, **kwargs):
            captured["model_name"] = model_name
            captured["model_provider"] = model_provider
            captured.update(kwargs)
            return _RecordingModel()

        monkeypatch.setattr(deep_agent_builder, "init_chat_model", fake_init)
        build_chat_model(
            self._config(),
            extra_kwargs={"base_url": _BASE_URL, "api_key": "sk-user"},
        )
        assert captured["model_provider"] == "openai"
        assert captured["base_url"] == _BASE_URL
        assert captured["api_key"] == "sk-user"
        # Chat-Completions only — the /responses payload must NEVER be applied.
        assert captured["use_responses_api"] is False

    def test_missing_base_url_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_init(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("init_chat_model must not run without a base_url")

        monkeypatch.setattr(deep_agent_builder, "init_chat_model", fake_init)
        with pytest.raises(AgentRuntimeError) as excinfo:
            build_chat_model(self._config(), extra_kwargs={"api_key": "sk-user"})
        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
