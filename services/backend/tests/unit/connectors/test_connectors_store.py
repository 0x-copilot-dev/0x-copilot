"""Tests for the in-memory connectors store (P11-A2 §5.1)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend_app.connectors.store import (
    ConnectorAuditRecord,
    ConnectorRecord,
    ConnectorScopeEntry,
    InMemoryConnectorsStore,
    McpUpsertInput,
)


def _input(
    *,
    tenant_id: str = "tenant_a",
    owner_user_id: str = "user_1",
    slug: str = "gmail",
    status: str = "connected",
    vault_ref: str = "vault:abc",
) -> McpUpsertInput:
    return McpUpsertInput(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        slug=slug,
        display_name=slug.title(),
        description=f"{slug} connector",
        status=status,
        status_reason=None,
        scopes=(
            ConnectorScopeEntry(scope=f"{slug}.read", granted=True, description=""),
        ),
        last_sync_at=None,
        last_error_at=None,
        vault_ref=vault_ref,
    )


class TestUpsertFromMcp:
    def test_first_call_inserts_a_new_row(self) -> None:
        store = InMemoryConnectorsStore()
        record = store.upsert_from_mcp_registration(_input())
        assert record.tenant_id == "tenant_a"
        assert record.slug == "gmail"
        assert record.status == "connected"
        assert record.vault_ref == "vault:abc"
        assert record.id.startswith("conn_")

    def test_second_call_updates_existing_row_by_natural_key(self) -> None:
        store = InMemoryConnectorsStore()
        first = store.upsert_from_mcp_registration(_input(vault_ref="v1"))
        second = store.upsert_from_mcp_registration(_input(vault_ref="v2"))
        assert first.id == second.id
        assert second.vault_ref == "v2"
        assert len(store.connectors) == 1

    def test_status_transitions_are_persisted(self) -> None:
        store = InMemoryConnectorsStore()
        store.upsert_from_mcp_registration(_input(status="connected"))
        updated = store.upsert_from_mcp_registration(_input(status="error"))
        assert updated.status == "error"

    def test_different_owner_creates_distinct_row(self) -> None:
        store = InMemoryConnectorsStore()
        store.upsert_from_mcp_registration(_input(owner_user_id="user_1"))
        store.upsert_from_mcp_registration(_input(owner_user_id="user_2"))
        assert len(store.connectors) == 2


class TestTenantIsolation:
    def test_get_connector_filters_by_tenant(self) -> None:
        store = InMemoryConnectorsStore()
        a = store.upsert_from_mcp_registration(_input(tenant_id="tenant_a"))
        store.upsert_from_mcp_registration(_input(tenant_id="tenant_b"))
        assert store.get_connector(tenant_id="tenant_a", connector_id=a.id) is not None
        assert store.get_connector(tenant_id="tenant_b", connector_id=a.id) is None

    def test_list_connectors_filters_by_tenant(self) -> None:
        store = InMemoryConnectorsStore()
        store.upsert_from_mcp_registration(_input(tenant_id="tenant_a", slug="gmail"))
        store.upsert_from_mcp_registration(_input(tenant_id="tenant_b", slug="slack"))
        rows, _ = store.list_connectors(tenant_id="tenant_a")
        assert len(rows) == 1
        assert rows[0].tenant_id == "tenant_a"

    def test_list_audit_filters_by_tenant(self) -> None:
        store = InMemoryConnectorsStore()
        store.append_audit(
            ConnectorAuditRecord(
                tenant_id="tenant_a",
                actor_user_id="u",
                action="connector.connected",
                target_id="conn_x",
            )
        )
        store.append_audit(
            ConnectorAuditRecord(
                tenant_id="tenant_b",
                actor_user_id="u",
                action="connector.connected",
                target_id="conn_x",
            )
        )
        rows_a, _ = store.list_audit_for_connector(
            tenant_id="tenant_a", connector_id="conn_x"
        )
        rows_b, _ = store.list_audit_for_connector(
            tenant_id="tenant_b", connector_id="conn_x"
        )
        assert len(rows_a) == 1
        assert len(rows_b) == 1


class TestListFilters:
    def test_filter_by_status_or_set(self) -> None:
        store = InMemoryConnectorsStore()
        store.upsert_from_mcp_registration(_input(slug="gmail", status="connected"))
        store.upsert_from_mcp_registration(_input(slug="slack", status="error"))
        rows, _ = store.list_connectors(tenant_id="tenant_a", statuses=("error",))
        assert [r.slug for r in rows] == ["slack"]

    def test_filter_by_slug_set(self) -> None:
        store = InMemoryConnectorsStore()
        store.upsert_from_mcp_registration(_input(slug="gmail"))
        store.upsert_from_mcp_registration(_input(slug="slack"))
        rows, _ = store.list_connectors(tenant_id="tenant_a", slugs=("slack",))
        assert [r.slug for r in rows] == ["slack"]

    def test_q_matches_display_name(self) -> None:
        store = InMemoryConnectorsStore()
        store.upsert_from_mcp_registration(_input(slug="gmail"))
        store.upsert_from_mcp_registration(_input(slug="slack"))
        rows, _ = store.list_connectors(tenant_id="tenant_a", q="mail")
        assert {r.slug for r in rows} == {"gmail"}

    def test_pagination_returns_next_cursor(self) -> None:
        store = InMemoryConnectorsStore()
        for i in range(3):
            store.upsert_from_mcp_registration(_input(owner_user_id=f"u_{i}"))
        rows1, cursor = store.list_connectors(tenant_id="tenant_a", limit=2)
        assert len(rows1) == 2
        assert cursor == "2"
        rows2, cursor2 = store.list_connectors(
            tenant_id="tenant_a", limit=2, cursor=cursor
        )
        assert len(rows2) == 1
        assert cursor2 is None


class TestDirectInsertAndUpdate:
    def test_insert_and_update_round_trip(self) -> None:
        store = InMemoryConnectorsStore()
        record = ConnectorRecord(
            tenant_id="tenant_a",
            slug="custom",
            display_name="Custom",
            owner_user_id="u",
            vault_ref="v",
        )
        store.insert_connector(record)
        fetched = store.get_connector(tenant_id="tenant_a", connector_id=record.id)
        assert fetched is not None
        updated = fetched.model_copy(update={"status": "disconnected"})
        store.update_connector(updated)
        again = store.get_connector(tenant_id="tenant_a", connector_id=record.id)
        assert again is not None
        assert again.status == "disconnected"


class TestTransactionContext:
    def test_transaction_is_a_no_op_yield(self) -> None:
        store = InMemoryConnectorsStore()
        seen: list[datetime] = []
        with store.transaction():
            seen.append(datetime.now(timezone.utc))
        assert len(seen) == 1
