"""Single source of truth for the frontend model catalog (the picker).

One canonical builder consumed by both the picker route
(:meth:`ConversationQueryService.list_models`) and workspace default-model
validation (:class:`WorkspaceCoordinator`). Both import *this* module, so a
model that appears here shows up in both the picker and the admin-default
allow-set without drift.

Model metadata is **live**: :class:`ModelsDevCatalogSource` supplies it
from https://models.dev with disk-cache and vendored-snapshot fallbacks
(see :mod:`agent_runtime.api.models_dev_source`), replacing the previous
hardcoded native + curated-OpenRouter lists. The settings-derived default
model always remains the first catalog entry, so an offline boot with an
empty source still produces a usable picker.

The catalog advertises **only** providers the run path can actually
execute. :class:`ModelConfigResolver` (the run path) accepts a fixed
provider allowlist; :meth:`ModelConfigResolver.supports_provider` is the
authority. Source records for providers outside that allowlist (e.g. ``groq``,
``xai`` from models.dev) are filtered out in :meth:`ModelCatalog.build`
so the picker can never surface a model that would be rejected the moment a
run starts. Adding a new provider is a run-path change (extend the allowlist),
never a catalog-only change — that is what keeps the two surfaces from drifting.

``configured`` semantics are unchanged: native providers reflect whether a
deployment env key is present; OpenRouter availability is per-user BYOK,
which this global (settings-only) layer cannot see, so those entries are
always **selectable** — a run started without a stored key is guided to
Settings by the run-create credential gate in :class:`ModelConfigResolver`,
not by hiding the model here.
"""

from __future__ import annotations

import threading

from agent_runtime.api.models_dev_source import (
    CatalogModelRecord,
    ModelsDevCatalogSource,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import ModelCatalogItem


class ModelCatalog:
    """Assembles the catalog the model picker shows."""

    # Providers whose runs go through a native SDK path with reasoning
    # passthrough. Reasoning for OpenAI-compat gateways (OpenRouter) is a
    # follow-up, so their entries never advertise reasoning controls even
    # when models.dev flags the underlying model as reasoning-capable.
    NATIVE_REASONING_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
    # Providers that are always selectable because their credentials are
    # per-user BYOK — invisible at this settings-only layer.
    ALWAYS_SELECTABLE_PROVIDERS = frozenset({"openrouter"})

    _source: ModelsDevCatalogSource | None = None
    _source_lock = threading.Lock()

    @classmethod
    def configure_source(cls, source: ModelsDevCatalogSource | None) -> None:
        """Inject the metadata source (tests) or reset to lazy construction."""

        with cls._source_lock:
            cls._source = source

    @classmethod
    def reset_source(cls) -> None:
        """Drop the shared source so the next build reconstructs from settings."""

        cls.configure_source(None)

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
        """Return the ordered, **id-unique** catalog: default model first, then live records.

        This is the single deduplication point every consumer relies on —
        the picker route (:meth:`ConversationQueryService.list_models`) and
        workspace default-model validation (:class:`WorkspaceCoordinator`)
        both take the tuple verbatim, so neither has to re-deduplicate and
        neither can drift from the other.

        Two invariants hold **by construction**:

        * ``settings.default_model`` is always present and always first. Its
          entry is emitted before any source record, so its id occupies the
          leading slot even when the live source is empty (offline boot).
        * No id appears twice — in particular the default is never
          double-listed when the source also ships the same model id. Source
          records arrive deterministically ordered (provider asc, release
          date desc, id asc); collapsing with last-definition-wins keeps the
          default's leading position while upgrading its value to the richer
          live record (context window, costs, capability flags) for the same
          id. Downstream ids are keys, so the frontend picker — which keys
          rows by ``id`` — never sees a duplicate key.
        """

        items = [cls._default_item(settings)]
        for record in cls._source_for(settings).records():
            # SSOT: never advertise a model the run path cannot execute. The
            # models.dev source carries providers (groq, xai) that the run
            # path's ``ModelConfigResolver`` provider allowlist rejects, so a
            # user could otherwise pick a model that can never run. Filter to
            # run-path-executable providers here — the one place the catalog
            # is assembled — rather than papering over the divergence later.
            if not ModelConfigResolver.supports_provider(record.provider):
                continue
            items.append(cls._item_from_record(record, settings))
        # Collapse by id, last-definition-wins. A dict comprehension keeps
        # each id at its first-insertion position (so the default stays
        # first) while replacing its value with the last same-id entry (so a
        # richer live record supersedes the minimal default placeholder).
        deduped = {item.id: item for item in items}
        return tuple(deduped.values())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _source_for(cls, settings: RuntimeSettings) -> ModelsDevCatalogSource:
        """Return the process-wide source, constructing it from settings once."""

        with cls._source_lock:
            if cls._source is None:
                cls._source = ModelsDevCatalogSource(
                    cache_dir=settings.model_catalog_cache_dir
                )
            return cls._source

    @classmethod
    def _default_item(cls, settings: RuntimeSettings) -> ModelCatalogItem:
        """Entry for the settings-driven default model (always present, always first)."""

        default = settings.default_model
        return ModelCatalogItem(
            id=default.model_name,
            provider=default.provider,
            model_name=default.model_name,
            name=cls.display_name(default.model_name),
            description="Runtime default model",
            configured=cls._configured(default.provider, settings),
            supports_streaming=default.supports_streaming,
            supports_reasoning=default.reasoning is not None,
            reasoning=default.reasoning.model_dump(mode="json")
            if default.reasoning is not None
            else None,
        )

    @classmethod
    def _item_from_record(
        cls, record: CatalogModelRecord, settings: RuntimeSettings
    ) -> ModelCatalogItem:
        """Map one source record onto the public catalog item shape."""

        return ModelCatalogItem(
            id=record.model_id,
            provider=record.provider,
            model_name=record.model_id,
            name=record.display_name,
            configured=cls._configured(record.provider, settings),
            supports_streaming=True,
            supports_attachments=record.supports_attachments,
            supports_reasoning=record.supports_reasoning
            and record.provider in cls.NATIVE_REASONING_PROVIDERS,
            context_window=record.context_window,
            max_output_tokens=record.max_output_tokens,
            input_cost_per_mtok=record.input_cost_per_mtok,
            output_cost_per_mtok=record.output_cost_per_mtok,
            supports_tools=record.supports_tools,
            release_date=record.release_date,
        )

    @classmethod
    def _configured(cls, provider: str, settings: RuntimeSettings) -> bool:
        """Whether the provider is usable without per-user setup, as far as settings can see."""

        if provider in cls.ALWAYS_SELECTABLE_PROVIDERS:
            return True
        try:
            return settings.provider_settings(provider).is_configured
        except ValueError:
            # Defensive: a run-path-executable provider with no deployment-level
            # key settings would land here (only a per-user BYOK key could
            # enable it, invisible to this settings-only layer) — report
            # not-configured rather than guessing. Providers the run path
            # rejects (groq, xai) are already filtered out before reaching here.
            return False


__all__ = ["ModelCatalog"]
