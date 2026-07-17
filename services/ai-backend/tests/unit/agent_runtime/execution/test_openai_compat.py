"""OpenAI-wire-compatible provider registry + openrouter resolution."""

from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.execution.openai_compat import OpenAICompatibleProviders
from agent_runtime.settings import RuntimeSettings


class TestRegistry:
    def test_openrouter_endpoint_has_fixed_base_url(self) -> None:
        endpoint = OpenAICompatibleProviders.get("openrouter")
        assert endpoint is not None
        assert endpoint.base_url == "https://openrouter.ai/api/v1"
        assert endpoint.api_key_env == "OPENROUTER_API_KEY"
        assert endpoint.requires_api_key is True

    def test_ollama_endpoint_is_keyless_with_env_overridable_base_url(self) -> None:
        endpoint = OpenAICompatibleProviders.get("ollama")
        assert endpoint is not None
        assert endpoint.requires_api_key is False
        assert endpoint.resolve_base_url(environ={}) == "http://localhost:11434/v1"
        assert (
            endpoint.resolve_base_url(
                environ={"OLLAMA_BASE_URL": "http://host.docker.internal:11434/v1"}
            )
            == "http://host.docker.internal:11434/v1"
        )
        # Blank override falls back to the default.
        assert (
            endpoint.resolve_base_url(environ={"OLLAMA_BASE_URL": "  "})
            == "http://localhost:11434/v1"
        )
        assert endpoint.api_key_from_env(environ={}) is None

    def test_is_compatible_and_slugs(self) -> None:
        assert OpenAICompatibleProviders.is_compatible("openrouter") is True
        assert OpenAICompatibleProviders.is_compatible("openai") is False
        assert "openrouter" in OpenAICompatibleProviders.slugs()

    def test_unknown_provider_is_not_compatible(self) -> None:
        assert OpenAICompatibleProviders.get("nope") is None
        assert OpenAICompatibleProviders.is_compatible("nope") is False


class TestAttributionHeaders:
    def test_defaults_when_env_absent(self) -> None:
        endpoint = OpenAICompatibleProviders.get("openrouter")
        assert endpoint is not None
        headers = endpoint.default_headers(environ={})
        assert headers == {
            "HTTP-Referer": "https://0xcopilot.tech",
            "X-Title": "0xCopilot",
        }

    def test_env_override_wins(self) -> None:
        endpoint = OpenAICompatibleProviders.get("openrouter")
        assert endpoint is not None
        headers = endpoint.default_headers(
            environ={
                "OPENROUTER_APP_URL": "https://example.test",
                "OPENROUTER_APP_TITLE": "Example",
            }
        )
        assert headers == {
            "HTTP-Referer": "https://example.test",
            "X-Title": "Example",
        }

    def test_blank_env_value_drops_header(self) -> None:
        endpoint = OpenAICompatibleProviders.get("openrouter")
        assert endpoint is not None
        # An operator can opt a header out by exporting it blank; the default
        # for the *other* header still applies.
        headers = endpoint.default_headers(environ={"OPENROUTER_APP_URL": "   "})
        assert "HTTP-Referer" not in headers
        assert headers["X-Title"] == "0xCopilot"

    def test_api_key_from_env(self) -> None:
        endpoint = OpenAICompatibleProviders.get("openrouter")
        assert endpoint is not None
        assert endpoint.api_key_from_env(environ={}) is None
        assert (
            endpoint.api_key_from_env(environ={"OPENROUTER_API_KEY": "sk-or-v1-x"})
            == "sk-or-v1-x"
        )


class TestProviderResolution:
    def test_normalize_openrouter(self) -> None:
        assert ModelConfigResolver._normalize_provider("openrouter") == "openrouter"
        assert ModelConfigResolver._normalize_provider("OpenRouter") == "openrouter"

    def test_infer_openrouter_from_vendor_slash_model_slug(self) -> None:
        assert (
            ModelConfigResolver._infer_provider("anthropic/claude-3.7-sonnet")
            == "openrouter"
        )
        assert (
            ModelConfigResolver._infer_provider("meta-llama/llama-3.3-70b-instruct")
            == "openrouter"
        )

    def test_native_model_names_do_not_infer_openrouter(self) -> None:
        assert ModelConfigResolver._infer_provider("gpt-5.4-mini") == "openai"
        assert ModelConfigResolver._infer_provider("claude-opus-4-7") == "anthropic"
        assert ModelConfigResolver._infer_provider("gemini-2.5-pro") == "gemini"

    def test_normalize_ollama(self) -> None:
        assert ModelConfigResolver._normalize_provider("ollama") == "ollama"
        assert ModelConfigResolver._normalize_provider("Ollama") == "ollama"


class TestCredentialGate:
    @staticmethod
    def _resolver() -> ModelConfigResolver:
        # Build from an empty env so the gate is deterministic regardless of a
        # local .env carrying provider keys.
        return ModelConfigResolver(RuntimeSettings._from_env_values({}))

    def test_keyless_ollama_is_not_blocked_without_credentials(self) -> None:
        config = self._resolver().resolve(
            ModelSelection(provider="ollama", model_name="llama3.2:1b"),
            require_credentials=True,
            user_key_providers=(),
        )
        assert config.provider == "ollama"
        assert config.model_name == "llama3.2:1b"

    def test_keyed_provider_still_requires_a_credential(self) -> None:
        # A registered but key-requiring compat provider (openrouter) with no
        # env key and no BYOK key must still be rejected — keyless is opt-in.
        with pytest.raises(AgentRuntimeError) as exc_info:
            self._resolver().resolve(
                ModelSelection(
                    provider="openrouter", model_name="anthropic/claude-3.7-sonnet"
                ),
                require_credentials=True,
                user_key_providers=(),
            )
        assert exc_info.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
        assert "openrouter" in str(exc_info.value)
