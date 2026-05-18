"""⌘K command palette destination (Phase 12 P12-A4) — denormalized index + search.

Source: docs/atlas-new-design/destinations/team-memory-cmdk-prd.md
  §3.3 (Palette wire shapes), §4.3 (single search endpoint),
  §5.2 (palette_index table), §6.3 (ACL — read-only; results
  pre-filtered by per-entity ACL), §3.4 (quick actions —
  "Make this a routine?").

One denormalized table (``palette_index``) is the read substrate. Per-
destination LISTEN/NOTIFY triggers (dev: synchronous in-proc
dispatcher) refresh rows on insert / update / soft-delete. The search
route fans out one BM25 query across all entity_kinds, then ACL-filters
each candidate via the canonical
:func:`backend_app.projects.acl.is_member` predicate before projecting
into the wire ``PaletteHit`` shape.

Single-source-of-truth invariants (cross-audit §1.3 / §3.1):

* Tenant_id is the **first** filter on every query path.
* Project-scoped reads consume the canonical membership port — no
  reimplementation here.
* The dispatcher is the **only** write surface for the index;
  destinations call the dispatcher, not the store.

Wire shape is canonical at ``packages/api-types/src/palette.ts``.
"""

from __future__ import annotations

from backend_app.palette.refresh import (
    NullPaletteRefreshDispatcher,
    PaletteRefreshDispatcher,
)
from backend_app.palette.routes import register_palette_routes
from backend_app.palette.service import (
    PaletteHitWire,
    PaletteSearchResultWire,
    PaletteService,
    QuickActions,
    hit_to_dict,
)
from backend_app.palette.store import (
    EntityKind,
    InMemoryPaletteStore,
    PaletteEntry,
    PaletteSearchHit,
    PaletteStorePort,
)


__all__ = [
    "EntityKind",
    "InMemoryPaletteStore",
    "NullPaletteRefreshDispatcher",
    "PaletteEntry",
    "PaletteHitWire",
    "PaletteRefreshDispatcher",
    "PaletteSearchHit",
    "PaletteSearchResultWire",
    "PaletteService",
    "PaletteStorePort",
    "QuickActions",
    "hit_to_dict",
    "register_palette_routes",
]
