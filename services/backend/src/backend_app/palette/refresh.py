"""Per-destination LISTEN/NOTIFY hooks for the palette_index.

In production: each destination wires a Postgres trigger
(``LISTEN/NOTIFY``) into this module's :class:`PaletteRefreshDispatcher`,
which writes to the ``palette_index`` table. The dispatcher pattern
mirrors Phase 9's SSE bus — one canonical broadcaster, many local
subscribers (destinations).

In dev / in-memory: destinations call the dispatcher synchronously
on insert / update / soft-delete from their service-layer write paths.
We do **not** add dispatcher calls to destinations' existing route
files; the route layer stays unchanged. The dispatcher is injected at
:func:`backend_app.app.create_app` construction time.

Single write path discipline:

* Destinations never write directly to the store. They call
  :meth:`PaletteRefreshDispatcher.upsert_entry` /
  :meth:`PaletteRefreshDispatcher.delete_entry`.
* The dispatcher is the only caller of
  :meth:`PaletteStorePort.upsert_entry` / ``delete_entry`` in
  production.
* Destinations supply already-trusted ``tenant_id`` — the destination
  service has already bound it to a verified bearer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend_app.palette.store import PaletteEntry, PaletteStorePort


_LOGGER = logging.getLogger(__name__)


@dataclass
class PaletteRefreshDispatcher:
    """Canonical, tenant-scoped writer onto the palette index.

    Constructed once at app startup with the store adapter. Destinations
    receive a reference and call ``upsert_entry`` / ``delete_entry``
    from inside their own service layer at insert / update / soft-delete
    boundaries — this gives us a single write path with no parallel
    refresh logic per destination.

    Best-effort: failures are logged and swallowed. A palette refresh
    failure must never break the destination's primary write — the
    nightly GC job (sub-PRD §5.3) catches up.
    """

    store: PaletteStorePort

    def upsert_entry(self, entry: PaletteEntry) -> None:
        """Refresh one row.

        Idempotent: PRIMARY KEY = (tenant_id, entity_kind, entity_id).
        """
        try:
            self.store.upsert_entry(entry)
        except Exception:
            _LOGGER.warning(
                "palette_upsert_failed",
                extra={
                    "metadata": {
                        "tenant_id": entry.tenant_id,
                        "entity_kind": entry.entity_kind,
                        "entity_id": entry.entity_id,
                    }
                },
                exc_info=True,
            )

    def delete_entry(self, *, tenant_id: str, entity_kind: str, entity_id: str) -> None:
        """Soft-delete cascade — remove the row.

        Destination soft-deletes call this; hard-deletes (rare; nightly
        retention) also flow through here.
        """
        try:
            self.store.delete_entry(
                tenant_id=tenant_id, entity_kind=entity_kind, entity_id=entity_id
            )
        except Exception:
            _LOGGER.warning(
                "palette_delete_failed",
                extra={
                    "metadata": {
                        "tenant_id": tenant_id,
                        "entity_kind": entity_kind,
                        "entity_id": entity_id,
                    }
                },
                exc_info=True,
            )


class NullPaletteRefreshDispatcher:
    """No-op dispatcher.

    Used by tests / destinations that don't need to participate in
    palette refresh (e.g. when the palette is not wired in a smaller
    app composition). Keeps the destination service code simple — it
    always calls the dispatcher, and the wiring decides whether the
    write lands.
    """

    def upsert_entry(self, entry: PaletteEntry) -> None:  # noqa: ARG002
        return None

    def delete_entry(
        self,
        *,
        tenant_id: str,
        entity_kind: str,
        entity_id: str,  # noqa: ARG002
    ) -> None:
        return None
