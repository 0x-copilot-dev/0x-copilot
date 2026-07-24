"""Model selection and provider validation."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningSummary,
    RuntimeErrorCode,
)
from agent_runtime.execution.depth import DepthBudgetTable, ReasoningDepth
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.fake_model import FakeModelProvider
from agent_runtime.execution.openai_compat import (
    CUSTOM_OPENAI_COMPATIBLE_PROVIDER,
    OpenAICompatibleProviders,
)
from agent_runtime.settings import RuntimeSettings


@dataclass(frozen=True)
class ModelSelection:
    """Request-level model selection before defaults are applied.

    ``reasoning_depth`` is the user-facing Fast/Balanced/Deep handle from
    the composer. The resolver translates it into scaled
    timeout/output-token/tool-call-budget values on the returned
    :class:`ModelConfig` via :class:`DepthBudgetTable` — the single
    application point for the depth → budget mapping.
    """

    provider: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    max_input_tokens: int | None = None
    supports_streaming: bool | None = None
    reasoning: ModelReasoningConfig | None = None
    reasoning_depth: ReasoningDepth | None = None


class ModelConfigResolver:
    """Resolve request model settings against env-backed runtime settings."""

    # The run path's provider allowlist — the single authority on which
    # providers a run can actually execute. Maps every accepted alias to its
    # canonical slug. ``_normalize_provider`` is the only consumer; the model
    # catalog reuses :meth:`supports_provider` so the picker can never
    # advertise a provider the run path would reject (SSOT: the catalog
    # advertises only what the run path can serve).
    PROVIDER_ALIASES: dict[str, str] = {
        "anthropic": "anthropic",
        "claude": "anthropic",
        "gemini": "gemini",
        "google": "gemini",
        "google-genai": "gemini",
        "openai": "openai",
        # OpenAI-wire-compatible endpoints; resolve to the OpenAI client
        # with a fixed base_url (see execution/openai_compat.py).
        "openrouter": "openrouter",
        "ollama": "ollama",
        # User-supplied custom OpenAI-compatible endpoint (BYOK decision D-2).
        # ``canonical_provider`` normalizes ``_``→``-``, so the incoming
        # ``openai_compatible`` slug arrives here as ``openai-compatible``; it
        # canonicalizes back to the underscore form the store + provider_keys +
        # provider_endpoints maps all key on. Its base_url is resolved per-run,
        # not from the static registry.
        "openai-compatible": CUSTOM_OPENAI_COMPATIBLE_PROVIDER,
    }

    def __init__(self, settings: RuntimeSettings) -> None:
        self.settings = settings

    def resolve(
        self,
        selection: ModelSelection | None = None,
        *,
        require_credentials: bool = True,
        user_key_providers: Collection[str] = (),
    ) -> ModelConfig:
        """Return a complete model config after provider credential validation.

        ``user_key_providers`` carries the normalized provider slugs for which
        the current user has a stored BYOK key (availability only — never the
        key values). A user-supplied key satisfies the credential gate even
        when the deployment has no env key for that provider; precedence
        (user key > env key) is applied downstream where the key is injected
        into model construction (``user_policy_model_kwargs``).

        Applies :class:`DepthBudgetTable` exactly once when the selection
        carries a ``reasoning_depth``. Downstream callers (the worker,
        budget estimator, deep-agent builder) read the already-scaled
        values straight off the returned ``ModelConfig`` — they MUST
        NOT re-apply the mapping.
        """

        selected = selection or ModelSelection()
        provider = self._normalize_provider(
            selected.provider
            or self._infer_provider(selected.model_name)
            or self.settings.default_model.provider
        )
        provider_settings = self.settings.provider_settings(provider)
        # A registered keyless compat provider (local Ollama) needs no
        # credential — the harness talks to a runtime on the user's own
        # machine. Treat it as always-satisfied so the BYOK gate doesn't
        # block it.
        compat = OpenAICompatibleProviders.get(provider)
        # The deterministic fake model needs no credential — mirror the keyless
        # (local Ollama) branch so the BYOK gate never blocks a hermetic run.
        keyless = (
            compat is not None and not compat.requires_api_key
        ) or FakeModelProvider.is_enabled()
        if (
            require_credentials
            and not provider_settings.is_configured
            and provider not in user_key_providers
            and not keyless
        ):
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"Missing API key for model provider '{provider}'. "
                "Add one in Settings -> Provider keys.",
                retryable=False,
            )
        default_model = self.settings.default_model
        base = ModelConfig(
            provider=provider,
            model_name=selected.model_name or default_model.model_name,
            max_input_tokens=selected.max_input_tokens
            or default_model.max_input_tokens,
            max_output_tokens=default_model.max_output_tokens,
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
            reasoning=self._resolve_reasoning(
                provider=provider,
                model_name=selected.model_name or default_model.model_name,
                selected=selected.reasoning,
                default=default_model.reasoning,
            ),
            # Inherit the deployment-wide tool-call budget from settings;
            # depth scales it below. Keeps a single source of truth so an
            # operator that bumps the env var also bumps Deep's ceiling.
            tool_call_budget=self.settings.execution.tool_call_budget,
        )
        return DepthBudgetTable.apply(base, selected.reasoning_depth)

    @classmethod
    def _resolve_reasoning(
        cls,
        *,
        provider: str,
        model_name: str,
        selected: ModelReasoningConfig | None,
        default: ModelReasoningConfig | None,
    ) -> ModelReasoningConfig | None:
        """Resolve the reasoning config, defaulting a summary for native OpenAI.

        Precedence is unchanged: an explicit request config wins, then the
        deployment default. Only when BOTH are absent do we synthesize one — and
        only for a native OpenAI reasoning-capable model. Without this, a reasoning
        model (e.g. ``gpt-5.4-mini``) runs with ``reasoning=None``, so the builder
        never asks OpenAI for ``reasoning.summary`` and the Responses API emits no
        ``reasoning_summary_text_delta`` — the Focus/Studio transcript then shows
        no thinking block.

        The default requests only a ``summary`` (``auto``); effort is left unset so
        OpenAI applies its own default. It is scoped to ``provider == "openai"`` so
        the OpenAI-wire-compatible gateways (OpenRouter) and custom endpoints —
        which route through Chat Completions and 400 on a ``reasoning`` kwarg —
        never inherit it. Anthropic/Gemini thinking is a separate control and is
        untouched here.
        """

        if selected is not None:
            return selected
        if default is not None:
            return default
        if provider == "openai" and cls._openai_supports_reasoning(model_name):
            return ModelReasoningConfig(summary=ModelReasoningSummary.AUTO)
        return None

    @staticmethod
    def _openai_supports_reasoning(model_name: str) -> bool:
        """Whether ``model_name`` is a native OpenAI reasoning model.

        A capability family predicate, not a per-model list: the ``gpt-5`` line and
        the ``o1``/``o3``/``o4`` reasoning series always reason, so requesting a
        summary is safe. Non-reasoning OpenAI models (``gpt-4o``, ``gpt-4.1``, and
        the ``gpt-5-chat`` conversational variants) are excluded — the Responses
        API 400s on a ``reasoning`` param they do not support.
        """

        normalized = model_name.strip().lower().replace("_", "-")
        if "chat" in normalized:
            return False
        if normalized.startswith(("o1", "o3", "o4")):
            return True
        return normalized.startswith("gpt-5")

    @classmethod
    def _normalize_provider(cls, provider: str) -> str:
        canonical = cls.canonical_provider(provider)
        if canonical is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"Unsupported model provider '{provider}'.",
                retryable=False,
            )
        return canonical

    @classmethod
    def canonical_provider(cls, provider: str) -> str | None:
        """Canonical slug for ``provider``, or ``None`` if the run path rejects it.

        Non-raising sibling of :meth:`_normalize_provider`: callers outside the
        run path (notably the model catalog) use this to filter to providers a
        run can actually execute, without catching an exception.
        """

        normalized = provider.strip().lower().replace("_", "-")
        return cls.PROVIDER_ALIASES.get(normalized)

    @classmethod
    def supports_provider(cls, provider: str) -> bool:
        """Whether the run path can execute ``provider`` (any accepted alias)."""

        return cls.canonical_provider(provider) is not None

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
        # OpenRouter slugs are ``vendor/model`` (e.g. ``anthropic/claude-…``);
        # native provider model names never contain ``/``. This is only a
        # fallback — the composer sends ``provider="openrouter"`` explicitly.
        if "/" in model_name:
            return "openrouter"
        return None
