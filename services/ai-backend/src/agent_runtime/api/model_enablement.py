"""Per-workspace model enablement (PR-2C).

Turns a raw model catalog into a curated one by stamping each item's
``enabled`` flag from the workspace's ``enabled_models`` selection:

* An EXPLICIT selection (a tuple, possibly empty) enables exactly the
  ids/model_names it names — the workspace has curated its picker.
* NO selection (``None``) enables the **derived default short list** — the 6-9
  models :class:`DefaultModelSelectionPolicy` picks, spanning small/medium/big
  for the providers the caller holds a key for plus a taste of the others. The
  full catalog stays reachable in Settings -> Models; it is simply not what the
  composer's pill opens on. Enabling everything (the previous behaviour) put
  ~190 rows behind a 264px scroller, most of them dated snapshots the user has
  no reason to choose between.

Two invariants hold in BOTH modes so a user can never lock themselves out of a
working picker:

* Local models (Ollama) are always enabled — they cost nothing to list and are
  the offline fallback.
* The workspace's default model is always enabled — it is what runs use when a
  request omits a model, so it must be selectable.

Class-based with no module-level helpers, per the service conventions.
"""

from __future__ import annotations

from agent_runtime.api.litellm_model_source import CatalogModelRecord
from agent_runtime.api.model_tiers import DefaultModelSelectionPolicy
from runtime_api.schemas.runs import ModelCatalogItem
from runtime_api.schemas.workspace_defaults import DefaultModelSelection

# Provider ids whose models are always unconditionally enabled (local runtime).
_LOCAL_PROVIDERS = frozenset({"ollama"})


class ModelEnablementResolver:
    """Stamp ``enabled`` onto catalog items for one workspace."""

    @classmethod
    def apply(
        cls,
        items: tuple[ModelCatalogItem, ...],
        *,
        enabled_models: tuple[str, ...] | None,
        default_model: DefaultModelSelection | None,
        user_key_providers: frozenset[str] = frozenset(),
    ) -> tuple[ModelCatalogItem, ...]:
        """Return the catalog with each item's ``enabled`` flag resolved."""

        default_keys = cls._default_model_keys(default_model)
        selection = (
            frozenset(enabled_models)
            if enabled_models is not None
            else cls._derived_default_selection(items, user_key_providers)
        )
        return tuple(
            item.model_copy(
                update={
                    "enabled": cls._explicitly_enabled(
                        item, selection=selection, default_keys=default_keys
                    )
                }
            )
            for item in items
        )

    @classmethod
    def _derived_default_selection(
        cls,
        items: tuple[ModelCatalogItem, ...],
        user_key_providers: frozenset[str],
    ) -> frozenset[str]:
        """The default short list for a workspace that has not curated.

        Adapts catalog items back onto :class:`CatalogModelRecord` because the
        tier ladder is defined over records — one shape for the selection logic,
        whether it runs at build time or here.
        """

        records = tuple(
            CatalogModelRecord(
                provider=item.provider,
                model_id=item.id,
                display_name=item.name,
                output_cost_per_mtok=item.output_cost_per_mtok,
                release_date=item.release_date,
                family=item.family,
            )
            for item in items
            if item.provider not in _LOCAL_PROVIDERS
        )
        return frozenset(
            DefaultModelSelectionPolicy.select(
                records, user_key_providers=user_key_providers
            )
        )

    # ------------------------------------------------------------------
    # Explicit-selection mode
    # ------------------------------------------------------------------

    @classmethod
    def _explicitly_enabled(
        cls,
        item: ModelCatalogItem,
        *,
        selection: frozenset[str],
        default_keys: frozenset[str],
    ) -> bool:
        if cls._is_local(item) or cls._is_default(item, default_keys):
            return True
        return item.id in selection or item.model_name in selection

    # ------------------------------------------------------------------
    # Shared predicates
    # ------------------------------------------------------------------

    @staticmethod
    def _is_local(item: ModelCatalogItem) -> bool:
        return item.provider in _LOCAL_PROVIDERS

    @staticmethod
    def _is_default(item: ModelCatalogItem, default_keys: frozenset[str]) -> bool:
        return item.id in default_keys or item.model_name in default_keys

    @staticmethod
    def _default_model_keys(
        default_model: DefaultModelSelection | None,
    ) -> frozenset[str]:
        if default_model is None:
            return frozenset()
        return frozenset({default_model.model_name})


__all__ = ["ModelEnablementResolver"]
