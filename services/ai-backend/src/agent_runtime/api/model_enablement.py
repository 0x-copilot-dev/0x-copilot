"""Per-workspace model enablement (PR-2C).

Turns a raw model catalog into a curated one by stamping each item's
``enabled`` flag from the workspace's ``enabled_models`` selection:

* An EXPLICIT selection (a tuple, possibly empty) enables exactly the
  ids/model_names it names — the workspace has curated its picker.
* NO selection (``None``) applies the default heuristic: the newest
  ``DEFAULT_PER_PROVIDER`` models per cloud provider (by release date),
  so a fresh workspace sees a sensible short list instead of hundreds.

Two invariants hold in BOTH modes so a user can never lock themselves out
of a working picker:

* Local models (Ollama) are always enabled — they cost nothing to list
  and are the offline fallback.
* The workspace's default model is always enabled — it is what runs use
  when a request omits a model, so it must be selectable.

Class-based with no module-level helpers, per the service conventions.
"""

from __future__ import annotations

from collections import defaultdict

from runtime_api.schemas.runs import ModelCatalogItem
from runtime_api.schemas.workspace_defaults import DefaultModelSelection

# Provider ids whose models are curated by the heuristic. Local models are
# never in this set — they are unconditionally enabled.
_LOCAL_PROVIDERS = frozenset({"ollama"})


class ModelEnablementResolver:
    """Stamp ``enabled`` onto catalog items for one workspace."""

    # Newest-N per cloud provider enabled when the workspace has no explicit
    # curation. Small on purpose: the default picker is a short, current list.
    DEFAULT_PER_PROVIDER = 2

    @classmethod
    def apply(
        cls,
        items: tuple[ModelCatalogItem, ...],
        *,
        enabled_models: tuple[str, ...] | None,
        default_model: DefaultModelSelection | None,
    ) -> tuple[ModelCatalogItem, ...]:
        """Return the catalog with each item's ``enabled`` flag resolved."""

        default_keys = cls._default_model_keys(default_model)
        if enabled_models is not None:
            selection = frozenset(enabled_models)
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
        return cls._apply_heuristic(items, default_keys=default_keys)

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
    # Heuristic (no explicit selection) mode
    # ------------------------------------------------------------------

    @classmethod
    def _apply_heuristic(
        cls,
        items: tuple[ModelCatalogItem, ...],
        *,
        default_keys: frozenset[str],
    ) -> tuple[ModelCatalogItem, ...]:
        # Rank each cloud provider's models by release date (newest first;
        # missing dates sort last) and enable the top N. The catalog already
        # arrives provider-grouped and newest-first, but we re-rank defensively
        # rather than trust upstream ordering.
        by_provider: dict[str, list[ModelCatalogItem]] = defaultdict(list)
        for item in items:
            if not cls._is_local(item):
                by_provider[item.provider].append(item)
        newest_ids: set[str] = set()
        for provider_items in by_provider.values():
            ranked = sorted(
                provider_items,
                key=lambda entry: (entry.release_date or "", entry.id),
                reverse=True,
            )
            for entry in ranked[: cls.DEFAULT_PER_PROVIDER]:
                newest_ids.add(entry.id)
        return tuple(
            item.model_copy(
                update={
                    "enabled": (
                        cls._is_local(item)
                        or cls._is_default(item, default_keys)
                        or item.id in newest_ids
                    )
                }
            )
            for item in items
        )

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
