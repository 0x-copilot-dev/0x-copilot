"""Model selection and provider validation."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.settings import RuntimeSettings


@dataclass(frozen=True)
class ModelSelection:
    """Request-level model selection before defaults are applied."""

    provider: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    max_input_tokens: int | None = None
    supports_streaming: bool | None = None
    reasoning: ModelReasoningConfig | None = None


class ModelConfigResolver:
    """Resolve request model settings against env-backed runtime settings."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings

    def resolve(
        self,
        selection: ModelSelection | None = None,
        *,
        require_credentials: bool = True,
    ) -> ModelConfig:
        """Return a complete model config after provider credential validation."""

        selected = selection or ModelSelection()
        provider = self._normalize_provider(
            selected.provider
            or self._infer_provider(selected.model_name)
            or self.settings.default_model.provider
        )
        provider_settings = self.settings.provider_settings(provider)
        if require_credentials and not provider_settings.is_configured:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"Missing API key for model provider '{provider}'.",
                retryable=False,
            )
        default_model = self.settings.default_model
        return ModelConfig(
            provider=provider,
            model_name=selected.model_name or default_model.model_name,
            max_input_tokens=selected.max_input_tokens
            or default_model.max_input_tokens,
            timeout_seconds=selected.timeout_seconds or default_model.timeout_seconds,
            temperature=(
                selected.temperature
                if selected.temperature is not None
                else default_model.temperature
            ),
            supports_streaming=(
                selected.supports_streaming
                if selected.supports_streaming is not None
                else default_model.supports_streaming
            ),
            reasoning=(
                selected.reasoning
                if selected.reasoning is not None
                else default_model.reasoning
            ),
        )

    @classmethod
    def _normalize_provider(cls, provider: str) -> str:
        normalized = provider.strip().lower().replace("_", "-")
        aliases = {
            "anthropic": "anthropic",
            "claude": "anthropic",
            "gemini": "gemini",
            "google": "gemini",
            "google-genai": "gemini",
            "openai": "openai",
        }
        if normalized not in aliases:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"Unsupported model provider '{provider}'.",
                retryable=False,
            )
        return aliases[normalized]

    @classmethod
    def _infer_provider(cls, model_name: str | None) -> str | None:
        if model_name is None:
            return None
        normalized = model_name.lower().replace(" ", "-").replace("_", "-")
        if normalized.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if normalized.startswith("claude"):
            return "anthropic"
        if normalized.startswith("gemini"):
            return "gemini"
        return None
