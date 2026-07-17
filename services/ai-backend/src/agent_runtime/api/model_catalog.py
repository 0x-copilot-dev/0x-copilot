"""Single source of truth for the frontend model catalog (the picker).

One canonical builder consumed by both the picker route
(:meth:`ConversationQueryService.list_models`) and workspace default-model
validation (:class:`WorkspaceCoordinator`). Both previously assembled the
same hard-coded list inline — the workspace side even noted the
duplication was to dodge a circular import between the two coordinator
modules. This neutral module breaks that cycle: both import *it*, so a
model added here shows up in both the picker and the admin-default
allow-set without drift.

OpenRouter models are appended when they exist in the curated set. Their
availability is per-user BYOK, which this global (settings-only) layer
cannot see, so they are always **selectable** — a run started without a
stored OpenRouter key is guided to Settings by the run-create credential
gate in :class:`ModelConfigResolver`, not by hiding the model here.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import ModelCatalogItem


@dataclass(frozen=True)
class _CuratedOpenRouterModel:
    """One curated OpenRouter model — a ``vendor/model`` slug plus display copy."""

    slug: str
    name: str
    description: str


class ModelCatalog:
    """Assembles the catalog the model picker shows."""

    # Curated OpenRouter starting set (a convenience/discovery aid — the
    # picker also accepts an arbitrary ``vendor/model`` slug via its
    # custom-model field). Slugs are OpenRouter's identifiers; refresh this
    # list as models come and go — an unknown slug simply errors at call
    # time, and the custom-model field always covers the long tail.
    OPENROUTER_MODELS: tuple[_CuratedOpenRouterModel, ...] = (
        _CuratedOpenRouterModel(
            "openai/gpt-4o", "GPT-4o (OpenRouter)", "OpenAI GPT-4o via OpenRouter"
        ),
        _CuratedOpenRouterModel(
            "anthropic/claude-3.7-sonnet",
            "Claude 3.7 Sonnet (OpenRouter)",
            "Anthropic Claude via OpenRouter",
        ),
        _CuratedOpenRouterModel(
            "google/gemini-2.0-flash-001",
            "Gemini 2.0 Flash (OpenRouter)",
            "Google Gemini via OpenRouter",
        ),
        _CuratedOpenRouterModel(
            "meta-llama/llama-3.3-70b-instruct",
            "Llama 3.3 70B (OpenRouter)",
            "Meta open model via OpenRouter",
        ),
        _CuratedOpenRouterModel(
            "deepseek/deepseek-chat",
            "DeepSeek Chat (OpenRouter)",
            "DeepSeek via OpenRouter",
        ),
        _CuratedOpenRouterModel(
            "mistralai/mistral-large",
            "Mistral Large (OpenRouter)",
            "Mistral via OpenRouter",
        ),
        _CuratedOpenRouterModel(
            "qwen/qwen-2.5-72b-instruct",
            "Qwen2.5 72B (OpenRouter)",
            "Qwen open model via OpenRouter",
        ),
        _CuratedOpenRouterModel(
            "x-ai/grok-2", "Grok 2 (OpenRouter)", "xAI Grok via OpenRouter"
        ),
    )

    @classmethod
    def display_name(cls, model_name: str) -> str:
        """Convert a slug-style model name to a human-readable label.

        ``gpt`` is forced to uppercase; everything else is title-cased.
        Underscores are normalised to hyphens first so ``claude_opus`` and
        ``claude-opus`` produce identical output.
        """

        parts = model_name.replace("_", "-").split("-")
        return " ".join(
            part.upper() if part in {"gpt"} else part.capitalize() for part in parts
        )

    @classmethod
    def build(cls, settings: RuntimeSettings) -> tuple[ModelCatalogItem, ...]:
        """Return the ordered catalog: default model, native curated set, OpenRouter."""

        default = settings.default_model
        configured = {
            "openai": settings.openai.is_configured,
            "anthropic": settings.anthropic.is_configured,
            "gemini": settings.gemini.is_configured,
        }
        items: list[ModelCatalogItem] = [
            ModelCatalogItem(
                id=default.model_name,
                provider=default.provider,
                model_name=default.model_name,
                name=cls.display_name(default.model_name),
                description="Runtime default model",
                configured=configured.get(default.provider, False),
                supports_streaming=default.supports_streaming,
                supports_reasoning=default.reasoning is not None,
                reasoning=default.reasoning.model_dump(mode="json")
                if default.reasoning is not None
                else None,
            ),
            ModelCatalogItem(
                id="gpt-5.4-mini",
                provider="openai",
                model_name="gpt-5.4-mini",
                name="GPT-5.4 Mini",
                description="Compact OpenAI model",
                configured=configured["openai"],
                supports_streaming=True,
                supports_attachments=True,
                supports_reasoning=True,
                reasoning={"enabled": True, "effort": "medium", "summary": "auto"},
            ),
            ModelCatalogItem(
                id="claude-opus-4-7",
                provider="anthropic",
                model_name="claude-opus-4-7",
                name="Claude Opus 4.7",
                description="Anthropic reasoning model",
                configured=configured["anthropic"],
                supports_streaming=True,
                supports_reasoning=True,
            ),
            ModelCatalogItem(
                id="gemini-2.5-pro",
                provider="gemini",
                model_name="gemini-2.5-pro",
                name="Gemini 2.5 Pro",
                description="Google long-context model",
                configured=configured["gemini"],
                supports_streaming=True,
                supports_attachments=True,
            ),
        ]
        items.extend(
            ModelCatalogItem(
                id=model.slug,
                provider="openrouter",
                model_name=model.slug,
                name=model.name,
                description=model.description,
                # Always selectable: BYOK availability is per-user and not
                # visible at this settings-only layer. Reasoning passthrough
                # for OpenRouter is a follow-up, so no reasoning controls.
                configured=True,
                supports_streaming=True,
            )
            for model in cls.OPENROUTER_MODELS
        )
        return tuple(items)


__all__ = ["ModelCatalog"]
