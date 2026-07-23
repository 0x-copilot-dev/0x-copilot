"""ActionClassifier — layered, fail-closed read/write classification (PRD-C1).

Ladder (SDR §10 invariant 1 — annotations alone can NEVER grant auto-run; only
catalog entries can):

1. **catalog hit**   -> READ if kind == READ else WRITE; ``basis = CATALOG``
                        (``catalog_kind`` records read/write/destructive).
2. **annotations**   -> ``read_only_hint is True``  -> READ,  ``basis = ANNOTATION``
                        ``destructive_hint is True`` -> WRITE, ``basis = ANNOTATION``
3. **otherwise**     -> WRITE, ``basis = DEFAULT`` (FR-C0 fail-closed).

The classifier is pure and total: no I/O, no exceptions for any string input.
The module binds a process-wide :data:`ACTION_CLASSIFIER` over the module-level
:data:`ACTION_CATALOG`; the ledger emission site imports that singleton.
"""

from __future__ import annotations

from agent_runtime.capabilities.actions.catalog import ACTION_CATALOG, ActionCatalog
from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
    ClassificationBasis,
    ClassifiedAction,
)
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotations
from agent_runtime.capabilities.surfaces.builtin import server_slug, tool_slug


class ActionClassifier:
    """Layered, fail-closed classifier over a curated :class:`ActionCatalog`."""

    __slots__ = ("_catalog",)

    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog

    def classify(
        self,
        *,
        server: str,
        tool: str,
        annotations: McpToolAnnotations | None,
    ) -> ClassifiedAction:
        """Classify one MCP tool call. Never raises; never returns UNKNOWN.

        ``server`` / ``tool`` may be the raw wire strings — they are normalized
        internally (and echoed back on the result as the slug-normalized
        ``connector`` / ``op``, identical to what the A3 emitter computes).
        """

        connector = server_slug(server)
        op = tool_slug(tool)

        # Rung 1 — curated catalog (authoritative; wins over any annotation).
        catalog_kind = self._catalog.lookup(server, tool)
        if catalog_kind is not None:
            action_class = (
                ActionClass.READ
                if catalog_kind is CatalogActionKind.READ
                else ActionClass.WRITE
            )
            return ClassifiedAction(
                connector=connector,
                op=op,
                action_class=action_class,
                basis=ClassificationBasis.CATALOG,
                catalog_kind=catalog_kind,
            )

        # Rung 2 — protocol annotations (untrusted hints; tighten only).
        if annotations is not None:
            if annotations.read_only_hint is True:
                return ClassifiedAction(
                    connector=connector,
                    op=op,
                    action_class=ActionClass.READ,
                    basis=ClassificationBasis.ANNOTATION,
                )
            if annotations.destructive_hint is True:
                return ClassifiedAction(
                    connector=connector,
                    op=op,
                    action_class=ActionClass.WRITE,
                    basis=ClassificationBasis.ANNOTATION,
                )

        # Rung 3 — fail-closed default (FR-C0): unknown op is a write, held.
        return ClassifiedAction(
            connector=connector,
            op=op,
            action_class=ActionClass.WRITE,
            basis=ClassificationBasis.DEFAULT,
        )


# Process-wide instance over the module-level catalog (mirrors builtin.py's
# ``_REGISTRY``). The emission site imports THIS — it never constructs the
# classifier per tool call.
ACTION_CLASSIFIER: ActionClassifier = ActionClassifier(ACTION_CATALOG)


__all__ = ["ACTION_CLASSIFIER", "ActionClassifier"]
