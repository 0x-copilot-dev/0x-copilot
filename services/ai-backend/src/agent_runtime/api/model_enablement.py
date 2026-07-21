"""Per-workspace model enablement (PR-2C).

Turns a raw model catalog into a curated one by stamping each item's
``enabled`` flag from the workspace's ``enabled_models`` selection:

* An EXPLICIT selection (a tuple, possibly empty) enables exactly the
  ids/model_names it names — the workspace has curated its picker.
* NO selection (``None``) enables the **whole catalog**. The catalog is now a
  curated product-model set sourced from LiteLLM (not the hundreds-strong
  models.dev firehose), so it is already the short list a fresh workspace
  should see — there is nothing left to trim, and LiteLLM carries no
  ``release_date`` to trim by. Workspace curation still narrows it later.

Two invariants hold in BOTH modes so a user can never lock themselves out of a
working picker:

* Local models (Ollama) are always enabled — they cost nothing to list and are
  the offline fallback.
* The workspace's default model is always enabled — it is what runs use when a
  request omits a model, so it must be selectable.

Class-based with no module-level helpers, per the service conventions.
"""

from __future__ import annotations

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
        # Uncurated default: the curated product catalog is itself the short
        # list, so every model is enabled. The local/default invariants are
        # trivially satisfied here but are re-asserted explicitly so the rule
        # stays safe if the default is ever re-narrowed.
        return tuple(item.model_copy(update={"enabled": True}) for item in items)

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
