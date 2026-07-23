"""Single source of truth for the frontend model catalog (the picker).

One canonical builder consumed by both the picker route
(:meth:`ConversationQueryService.list_models`) and workspace default-model
validation (:class:`WorkspaceCoordinator`). Both import *this* module, so a
model that appears here shows up in both the picker and the admin-default
allow-set without drift.

Model metadata comes from **LiteLLM**: :class:`LitellmModelSource` enriches a
curated product-model registry with context window, capability flags, and
per-Mtok cost from the installed ``litellm`` package's bundled ``model_cost``
table (see :mod:`agent_runtime.api.litellm_model_source`), replacing the retired
models.dev source. The settings-derived default model always remains the first
catalog entry, so an empty source still produces a usable picker.

The catalog advertises **only** providers the run path can actually execute.
:class:`ModelConfigResolver` (the run path) accepts a fixed provider allowlist;
:meth:`ModelConfigResolver.supports_provider` is the authority. Any source
record for a provider outside that allowlist is filtered out in
:meth:`ModelCatalog.build` so the picker can never surface a model that would be
rejected the moment a run starts. The curated registry only lists allowlisted
providers, so the filter is a defensive SSOT guard — adding a new provider is a
run-path change (extend the allowlist), never a catalog-only change.

``configured`` semantics: a model is ``configured`` (selectable without further
setup) when its provider has a usable credential from **either** source the run
path accepts — a deployment env key **or** the caller's own stored BYOK key.
Callers pass the latter as ``user_key_providers`` (the provider slugs the
per-(org, user) policies resolver reports a stored key for — the *same* resolver
the run-create credential gate consults, so the picker's "your key" badge and
the gate can never disagree). When ``user_key_providers`` is empty the flag
reflects env keys only, the historical settings-only behaviour. OpenRouter stays
always-selectable because its credential is per-user BYOK that no deployment env
key can stand in for.
"""

from __future__ import annotations

import threading

from agent_runtime.api.litellm_model_source import (
    CatalogModelRecord,
    CatalogModelSource,
    LitellmModelSource,
    ModelDisplayName,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import ModelCatalogItem


class ModelCatalog:
    """Assembles the catalog the model picker shows."""

    # Providers whose runs go through a native SDK path with reasoning
    # passthrough. Reasoning for OpenAI-compat gateways (OpenRouter) is a
    # follow-up, so their entries never advertise reasoning controls even
    # when LiteLLM flags the underlying model as reasoning-capable.
    NATIVE_REASONING_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
    # Providers that are always selectable because their credentials are
    # per-user BYOK — invisible at this settings-only layer.
    ALWAYS_SELECTABLE_PROVIDERS = frozenset({"openrouter"})

    _source: CatalogModelSource | None = None
    _source_lock = threading.Lock()

    @classmethod
    def configure_source(cls, source: CatalogModelSource | None) -> None:
        """Inject the metadata source (tests) or reset to lazy construction."""

        with cls._source_lock:
            cls._source = source

    @classmethod
    def reset_source(cls) -> None:
        """Drop the shared source so the next build reconstructs from defaults."""

        cls.configure_source(None)

    @classmethod
    def display_name(cls, model_name: str) -> str:
        """Human-readable label for a slug-style model id.

        Delegates to :meth:`ModelDisplayName.derive` — the single deriver shared
        with the LiteLLM source, so the default-model entry and the source
        records label identically.
        """

        return ModelDisplayName.derive(model_name)

    @classmethod
    def build(
        cls,
        settings: RuntimeSettings,
        *,
        user_key_providers: frozenset[str] = frozenset(),
    ) -> tuple[ModelCatalogItem, ...]:
        """Return the ordered, **id-unique** catalog: default model first, then source records.

        ``user_key_providers`` is the set of provider slugs (post
        ``ModelConfigResolver`` normalization, e.g. ``google`` → ``gemini``) the
        caller has a stored BYOK key for; a model whose provider is in that set is
        marked ``configured`` even without a deployment env key. Defaults to empty
        (env-key-only) so non-per-user callers — e.g. workspace default-model
        validation (:class:`WorkspaceCoordinator`) — keep their prior behaviour.

        This is the single deduplication point every consumer relies on —
        the picker route (:meth:`ConversationQueryService.list_models`) and
        workspace default-model validation (:class:`WorkspaceCoordinator`)
        both take the tuple verbatim, so neither has to re-deduplicate and
        neither can drift from the other.

        Two invariants hold **by construction**:

        * ``settings.default_model`` is always present and always first. Its
          entry is emitted before any source record, so its id occupies the
          leading slot even when the source is empty.
        * No id appears twice — in particular the default is never
          double-listed when the source also ships the same model id. Source
          records arrive deterministically ordered (provider asc, id asc);
          collapsing with last-definition-wins keeps the default's leading
          position while upgrading its value to the richer source record
          (context window, costs, capability flags) for the same id.
        """

        items = [cls._default_item(settings, user_key_providers)]
        for record in cls._source_for().records():
            # SSOT: never advertise a model the run path cannot execute. The
            # curated registry only lists allowlisted providers, but the filter
            # stays here — the one place the catalog is assembled — so a fake or
            # future source that emits an out-of-allowlist provider record can
            # never leak a model the run path's ``ModelConfigResolver`` rejects.
            if not ModelConfigResolver.supports_provider(record.provider):
                continue
            items.append(cls._item_from_record(record, settings, user_key_providers))
        # Collapse by id, last-definition-wins. A dict comprehension keeps
        # each id at its first-insertion position (so the default stays
        # first) while replacing its value with the last same-id entry (so a
        # richer source record supersedes the minimal default placeholder).
        deduped = {item.id: item for item in items}
        return tuple(deduped.values())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _source_for(cls) -> CatalogModelSource:
        """Return the process-wide source, constructing the LiteLLM one once."""

        with cls._source_lock:
            if cls._source is None:
                cls._source = LitellmModelSource()
            return cls._source

    @classmethod
    def _default_item(
        cls, settings: RuntimeSettings, user_key_providers: frozenset[str]
    ) -> ModelCatalogItem:
        """Entry for the settings-driven default model (always present, always first)."""

        default = settings.default_model
        return ModelCatalogItem(
            id=default.model_name,
            provider=default.provider,
            model_name=default.model_name,
            name=cls.display_name(default.model_name),
            description="Runtime default model",
            configured=cls._configured(default.provider, settings, user_key_providers),
            supports_streaming=default.supports_streaming,
            supports_reasoning=default.reasoning is not None,
            reasoning=default.reasoning.model_dump(mode="json")
            if default.reasoning is not None
            else None,
        )

    @classmethod
    def _item_from_record(
        cls,
        record: CatalogModelRecord,
        settings: RuntimeSettings,
        user_key_providers: frozenset[str],
    ) -> ModelCatalogItem:
        """Map one source record onto the public catalog item shape."""

        return ModelCatalogItem(
            id=record.model_id,
            provider=record.provider,
            model_name=record.model_id,
            name=record.display_name,
            configured=cls._configured(record.provider, settings, user_key_providers),
            supports_streaming=True,
            supports_attachments=record.supports_attachments,
            supports_reasoning=record.supports_reasoning
            and record.provider in cls.NATIVE_REASONING_PROVIDERS,
            context_window=record.context_window,
            max_output_tokens=record.max_output_tokens,
            input_cost_per_mtok=record.input_cost_per_mtok,
            output_cost_per_mtok=record.output_cost_per_mtok,
            supports_tools=record.supports_tools,
        )

    @classmethod
    def _configured(
        cls,
        provider: str,
        settings: RuntimeSettings,
        user_key_providers: frozenset[str],
    ) -> bool:
        """Whether the provider has a usable credential — env key OR caller BYOK key."""

        if provider in cls.ALWAYS_SELECTABLE_PROVIDERS:
            return True
        # The caller's own stored BYOK key makes the provider usable even with no
        # deployment env key — the run-create gate accepts exactly this source, so
        # the badge here matches what a run would actually do.
        if provider in user_key_providers:
            return True
        try:
            return settings.provider_settings(provider).is_configured
        except ValueError:
            # A run-path-executable provider with no deployment-level key settings
            # and no caller BYOK key lands here — report not-configured rather
            # than guessing.
            return False


__all__ = ["ModelCatalog"]
