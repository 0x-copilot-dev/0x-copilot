"""Refresh-dispatcher round-trip + store invariants.

Covers (sub-PRD §3.3 / §5.2):

* Insert / update / soft-delete via the dispatcher.
* PRIMARY KEY uniqueness — an update upserts the same row.
* The dispatcher swallows store exceptions (best-effort refresh; the
  destination's primary write never fails because palette tripped).
* Tenant isolation on read.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend_app.palette.refresh import (
    NullPaletteRefreshDispatcher,
    PaletteRefreshDispatcher,
)
from backend_app.palette.store import (
    EntityKind,
    InMemoryPaletteStore,
    PaletteEntry,
)


class _BrokenStore:
    """A store whose ``upsert_entry`` always raises — used to verify
    the dispatcher's best-effort guarantee.
    """

    def __init__(self) -> None:
        self.delete_calls: list[tuple[str, str, str]] = []

    def upsert_entry(self, entry):  # noqa: ANN001 - test stub
        raise RuntimeError("explode")

    def delete_entry(self, *, tenant_id, entity_kind, entity_id):
        self.delete_calls.append((tenant_id, entity_kind, entity_id))
        raise RuntimeError("explode")

    def bulk_query(self, *, tenant_id, query, entity_kinds, top_k):
        return ()


class TestRefreshRoundTrip:
    """Insert / update / delete through the dispatcher land in the store."""

    def test_upsert_inserts_row(self) -> None:
        store = InMemoryPaletteStore()
        dispatcher = PaletteRefreshDispatcher(store=store)
        entry = PaletteEntry(
            tenant_id="org_acme",
            entity_kind=EntityKind.LIBRARY_ITEM,
            entity_id="lib_42",
            title="Q4 deck",
            body="Quarterly plan",
            route="/library/lib_42",
            owner_user_id="usr_sarah",
        )
        dispatcher.upsert_entry(entry)

        landed = store.get_entry(
            tenant_id="org_acme",
            entity_kind=EntityKind.LIBRARY_ITEM,
            entity_id="lib_42",
        )
        assert landed is not None
        assert landed.title == "Q4 deck"

    def test_upsert_is_idempotent_on_primary_key(self) -> None:
        store = InMemoryPaletteStore()
        dispatcher = PaletteRefreshDispatcher(store=store)
        first = PaletteEntry(
            tenant_id="org_acme",
            entity_kind=EntityKind.CHAT,
            entity_id="conv_1",
            title="Initial title",
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        second = PaletteEntry(
            tenant_id="org_acme",
            entity_kind=EntityKind.CHAT,
            entity_id="conv_1",
            title="Renamed title",
            updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        dispatcher.upsert_entry(first)
        dispatcher.upsert_entry(second)

        rows = store.all_entries(tenant_id="org_acme")
        assert len(rows) == 1
        assert rows[0].title == "Renamed title"

    def test_delete_removes_row(self) -> None:
        store = InMemoryPaletteStore()
        dispatcher = PaletteRefreshDispatcher(store=store)
        entry = PaletteEntry(
            tenant_id="org_acme",
            entity_kind=EntityKind.MEMORY,
            entity_id="mem_1",
            title="Slack handle",
            owner_user_id="usr_sarah",
        )
        dispatcher.upsert_entry(entry)
        dispatcher.delete_entry(
            tenant_id="org_acme",
            entity_kind=EntityKind.MEMORY,
            entity_id="mem_1",
        )
        assert store.all_entries(tenant_id="org_acme") == ()

    def test_dispatcher_swallows_store_failure(self) -> None:
        """A palette refresh failure MUST NOT break the destination's write."""
        dispatcher = PaletteRefreshDispatcher(store=_BrokenStore())
        # Should not raise.
        dispatcher.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.PROJECT,
                entity_id="proj_x",
                title="x",
            )
        )
        dispatcher.delete_entry(
            tenant_id="acme", entity_kind=EntityKind.PROJECT, entity_id="proj_x"
        )


class TestTenantIsolation:
    """Reads never leak across tenants."""

    def test_bulk_query_scopes_by_tenant(self) -> None:
        store = InMemoryPaletteStore()
        store.upsert_entry(
            PaletteEntry(
                tenant_id="org_acme",
                entity_kind=EntityKind.CHAT,
                entity_id="conv_1",
                title="Acme planning",
            )
        )
        store.upsert_entry(
            PaletteEntry(
                tenant_id="org_zeta",
                entity_kind=EntityKind.CHAT,
                entity_id="conv_2",
                title="Acme planning",  # same title, different tenant
            )
        )

        hits = store.bulk_query(
            tenant_id="org_acme",
            query="acme",
            entity_kinds=None,
            top_k=10,
        )
        assert len(hits) == 1
        assert hits[0].entry.tenant_id == "org_acme"
        assert hits[0].entry.entity_id == "conv_1"


class TestNullDispatcher:
    """No-op dispatcher for compositions that don't wire the palette."""

    def test_null_dispatcher_does_not_raise(self) -> None:
        d = NullPaletteRefreshDispatcher()
        d.upsert_entry(
            PaletteEntry(
                tenant_id="acme",
                entity_kind=EntityKind.PROJECT,
                entity_id="p1",
                title="ignored",
            )
        )
        d.delete_entry(tenant_id="acme", entity_kind=EntityKind.PROJECT, entity_id="p1")


@pytest.mark.parametrize("query", ["", " ", "   "])
def test_empty_query_returns_recency_ordered(query: str) -> None:
    """An empty / whitespace q falls back to recency order."""
    store = InMemoryPaletteStore()
    older = PaletteEntry(
        tenant_id="acme",
        entity_kind=EntityKind.CHAT,
        entity_id="c1",
        title="older",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = PaletteEntry(
        tenant_id="acme",
        entity_kind=EntityKind.CHAT,
        entity_id="c2",
        title="newer",
        updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    store.upsert_entry(older)
    store.upsert_entry(newer)
    hits = store.bulk_query(
        tenant_id="acme",
        query=query,
        entity_kinds=None,
        top_k=10,
    )
    assert hits[0].entry.entity_id == "c2"
    assert hits[1].entry.entity_id == "c1"
