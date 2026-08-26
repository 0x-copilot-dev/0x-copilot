"""Single source of truth for the frontend model catalog (the picker).

One canonical builder consumed by both the picker route
(:meth:`ConversationQueryService.list_models`) and workspace default-model
validation (:class:`WorkspaceCoordinator`). Both import *this* module, so a
model that appears here shows up in both the picker and the admin-default
allow-set without drift.

Model discovery and metadata come from **models.dev**, with LiteLLM as the
offline fallback (:class:`ModelsDevModelSource` owns that failover). There is no
local per-model inventory to keep current. Records carry ``release_date`` and
``family`` when models.dev is serving and omit them when the fallback is —
consumers must treat a missing release date as "unknown", never "old". The
settings-derived default model always remains the first catalog entry, so an
empty source still produces a usable picker.

The catalog advertises **only** providers the run path can actually execute.
:class:`ModelConfigResolver` (the run path) accepts a fixed provider allowlist;
:meth:`ModelConfigResolver.supports_provider` is the authority. Any source
record for a provider outside that allowlist is filtered out in
:meth:`ModelCatalog.build` so the picker can never surface a model that would be
rejected the moment a run starts. Adding a new provider is a run-path and
provider-policy change, never a per-model catalog change.

``configured`` semantics: a model is ``configured`` (selectable without further
setup) when its provider has a usable credential from **either** source the run
path accepts — a deployment env key **or** the caller's own stored BYOK key.
Callers pass the latter as ``user_key_providers`` (the provider slugs the
per-(org, user) policies resolver reports a stored key for — the *same* resolver
the run-create credential gate consults, so the picker's "your key" badge and
the gate can never disagree). When ``user_key_providers`` is empty the flag
reflects env keys only, the historical settings-only behaviour.

Every provider goes through that same check — there is no always-selectable
exemption. OpenRouter used to hold one, from when this layer could only see env
keys and its per-user BYOK credential was therefore invisible here; once
``user_key_providers`` arrived the exemption became the sole reason the badge
could lie, marking every OpenRouter model "your key" for a user with no key at
all while :class:`ModelConfigResolver` refused the run.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from agent_runtime.api.litellm_model_source import (
    CatalogModelRecord,
    CatalogModelSource,
    CompositeModelSource,
    LitellmModelSource,
    ModelDisplayName,
)
from agent_runtime.api.model_tiers import ModelSizeTierResolver, ModelTier
from agent_runtime.api.models_dev_source import ModelsDevModelSource
from agent_runtime.api.virtuals_model_source import VirtualsModelSource
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

        runnable = [
            # SSOT: never advertise a model the run path cannot execute. The
            # source already applies provider policy, but the filter stays here
            # — the one place the catalog is assembled — so a fake or
            # future source that emits an out-of-allowlist provider record can
            # never leak a model the run path's ``ModelConfigResolver`` rejects.
            record
            for record in cls._source_for().records()
            if ModelConfigResolver.supports_provider(record.provider)
        ]
        tiers = cls._tier_index(runnable)
        items = [cls._default_item(settings, user_key_providers)]
        for record in runnable:
            items.append(
                cls._item_from_record(
                    record, settings, user_key_providers, tiers.get(record.model_id)
                )
            )
        # Collapse by id, last-definition-wins. A dict comprehension keeps
        # each id at its first-insertion position (so the default stays
        # first) while replacing its value with the last same-id entry (so a
        # richer source record supersedes the minimal default placeholder).
        deduped = {item.id: item for item in items}
        return cls._drop_dated_twins(tuple(deduped.values()))

    @staticmethod
    def _drop_dated_twins(
        items: tuple[ModelCatalogItem, ...],
    ) -> tuple[ModelCatalogItem, ...]:
        """Drop ``<id>-YYYYMMDD`` when plain ``<id>`` is already in the catalog.

        Once :class:`~agent_runtime.api.litellm_model_source.ModelDisplayName`
        stopped rendering the release stamp, ``claude-haiku-4-5`` and
        ``claude-haiku-4-5-20251001`` derive the SAME label, and the picker would
        show two identical rows for one model.

        **Deliberately the narrowest rule that fixes that.** An earlier version
        collapsed by display *name*, choosing the most canonical id per label —
        and it dropped `claude-opus-4-8` from a real-table build, because "most
        canonical" is a judgement that can go wrong in a catalog of 279 rows
        whose labels come from several sources. This version can only ever
        remove an id that is **character-for-character another id plus a date
        stamp**, from the same provider. A model with no plain twin is
        untouchable, which is the property the previous rule lacked.

        The default model is safe for the same reason: it is only dropped if the
        catalog also carries it without its stamp, in which case the label the
        user picks still resolves to a runnable model.
        """

        by_provider: dict[str, set[str]] = {}
        for item in items:
            by_provider.setdefault(item.provider, set()).add(item.id)

        def is_dated_twin(item: ModelCatalogItem) -> bool:
            head, separator, tail = item.id.rpartition("-")
            if not separator or len(tail) != 8 or not tail.isdigit():
                return False
            return head in by_provider.get(item.provider, frozenset())

        return tuple(item for item in items if not is_dated_twin(item))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _source_for(cls) -> CatalogModelSource:
        """Return the process-wide source, constructing the LiteLLM one once."""

        with cls._source_lock:
            if cls._source is None:
                # models.dev primary (release dates, product families, curated
                # display names), LiteLLM as the offline fallback. The source
                # itself owns that failover — see ModelsDevModelSource.
                #
                # Virtuals is UNIONED rather than chained: it is not a rival
                # description of the same catalog, it is a different catalog —
                # ~60 gateway-hosted models neither models.dev nor LiteLLM
                # carries. A fallback chain would hide it whenever the primary
                # is healthy, which is always.
                cls._source = CompositeModelSource(
                    ModelsDevModelSource(fallback=LitellmModelSource()),
                    VirtualsModelSource(),
                )
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
    def _tier_index(cls, records: Sequence[CatalogModelRecord]) -> dict[str, ModelTier]:
        """Map model id -> size rung, for every provider's general-purpose ladder.

        Computed once per build over the whole record set, because a tier is a
        property of a model's position among its siblings, not of the row.
        """

        index: dict[str, ModelTier] = {}
        for provider in {record.provider for record in records}:
            ladder = ModelSizeTierResolver.ladder(records, provider=provider)
            for record in ladder:
                tier = ModelSizeTierResolver.tier_of(record, ladder=ladder)
                if tier is not None:
                    index[record.model_id] = tier
        return index

    @classmethod
    def _item_from_record(
        cls,
        record: CatalogModelRecord,
        settings: RuntimeSettings,
        user_key_providers: frozenset[str],
        tier: ModelTier | None = None,
    ) -> ModelCatalogItem:
        """Map one source record onto the public catalog item shape."""

        return ModelCatalogItem(
            tier=tier,
            id=record.model_id,
            provider=record.provider,
            model_name=record.model_id,
            name=record.display_name,
            configured=cls._configured(record.provider, settings, user_key_providers),
            supports_streaming=True,
            supports_attachments=record.supports_attachments,
            supports_reasoning=record.supports_reasoning
            and record.provider in cls.NATIVE_REASONING_PROVIDERS,
            # Gated on the same condition as ``supports_reasoning``: advertising
            # a ladder for a provider whose reasoning we do not expose would
            # offer the client a control the run path then ignores.
            reasoning_efforts=tuple(record.reasoning_efforts) or None
            if record.provider in cls.NATIVE_REASONING_PROVIDERS
            else None,
            context_window=record.context_window,
            max_output_tokens=record.max_output_tokens,
            input_cost_per_mtok=record.input_cost_per_mtok,
            output_cost_per_mtok=record.output_cost_per_mtok,
            supports_tools=record.supports_tools,
            release_date=record.release_date,
            family=record.family,
        )

    @classmethod
    def _configured(
        cls,
        provider: str,
        settings: RuntimeSettings,
        user_key_providers: frozenset[str],
    ) -> bool:
        """Whether the provider has a usable credential — env key OR caller BYOK key."""

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
