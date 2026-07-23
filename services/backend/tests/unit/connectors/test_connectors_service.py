"""Tests for ``ConnectorsService`` — ACL + audit emission (P11-A2 §6)."""

from __future__ import annotations

import pytest

from backend_app.connectors.service import (
    ConnectorCatalogEntry,
    ConnectorForbidden,
    ConnectorNotFound,
    ConnectorsService,
    ConsumerProjectionPort,
    load_catalog,
)
from backend_app.connectors.store import (
    ConnectorScopeEntry,
    InMemoryConnectorsStore,
    McpUpsertInput,
)


def _seed(
    service: ConnectorsService,
    store: InMemoryConnectorsStore,
    *,
    tenant_id: str = "tenant_a",
    owner_user_id: str = "user_1",
    slug: str = "gmail",
    status: str = "connected",
):
    return service.write_through_from_mcp(
        mcp_input=McpUpsertInput(
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
            vault_ref="vault:abc",
        ),
        actor_user_id=owner_user_id,
        action="connector.connected",
    )


def _service() -> tuple[ConnectorsService, InMemoryConnectorsStore]:
    store = InMemoryConnectorsStore()
    service = ConnectorsService(store=store, catalog=())
    return service, store


class TestACL:
    def test_tenant_member_can_read(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        out = service.get_connector(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
        )
        assert out.id == record.id

    def test_out_of_tenant_caller_sees_404(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        with pytest.raises(ConnectorNotFound):
            service.get_connector(
                tenant_id="tenant_b",
                caller_user_id="user_1",
                caller_roles=(),
                connector_id=record.id,
            )

    def test_non_owner_non_admin_cannot_write(self) -> None:
        service, store = _service()
        record = _seed(service, store, owner_user_id="user_1")
        with pytest.raises(ConnectorForbidden):
            service.disconnect(
                tenant_id="tenant_a",
                caller_user_id="user_2",
                caller_roles=(),
                connector_id=record.id,
            )

    def test_admin_can_write_other_owner_row(self) -> None:
        service, store = _service()
        record = _seed(service, store, owner_user_id="user_1")
        out = service.disconnect(
            tenant_id="tenant_a",
            caller_user_id="user_admin",
            caller_roles=("admin",),
            connector_id=record.id,
        )
        assert out.status == "disconnected"


class TestAuditEmission:
    def test_write_through_from_mcp_emits_audit_row(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        rows, _ = store.list_audit_for_connector(
            tenant_id="tenant_a", connector_id=record.id
        )
        assert any(r.action == "connector.connected" for r in rows)

    def test_disconnect_emits_audit_row(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        service.disconnect(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
        )
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.disconnected" in actions

    def test_refresh_emits_audit_row(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        service.refresh_token(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
        )
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.token_refreshed" in actions

    def test_scope_add_emits_distinct_audit_row(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        service.patch_scopes(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
            scopes=(
                ConnectorScopeEntry(scope="gmail.read", granted=True, description=""),
                ConnectorScopeEntry(scope="gmail.modify", granted=True, description=""),
            ),
        )
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.scope_added" in actions
        # Removed wasn't requested → no remove row.
        assert "connector.scope_removed" not in actions

    def test_scope_remove_emits_distinct_audit_row(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        service.patch_scopes(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
            scopes=(
                ConnectorScopeEntry(scope="gmail.read", granted=False, description=""),
            ),
        )
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.scope_removed" in actions

    def test_scope_patch_no_op_emits_no_audit(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        # baseline audits after seeding
        baseline = sum(1 for r in store.audits if r.target_id == record.id)
        service.patch_scopes(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
            scopes=(
                ConnectorScopeEntry(scope="gmail.read", granted=True, description=""),
            ),
        )
        after = sum(1 for r in store.audits if r.target_id == record.id)
        assert after == baseline

    def test_mark_error_emits_audit_row(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        out = service.mark_error(
            tenant_id="tenant_a",
            connector_id=record.id,
            actor_user_id="worker",
            reason="provider_401",
        )
        assert out.status == "error"
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.error" in actions


class TestConsumerProjection:
    def test_default_projection_returns_empty_sets(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        projection = service.project_consumers(
            tenant_id="tenant_a", connector_id=record.id
        )
        assert projection["agents"] == ()
        assert projection["tools"] == ()
        assert projection["projects"] == ()
        assert projection["chats_with_grant"] == 0

    def test_injected_port_supplies_consumers(self) -> None:
        class _StubPort(ConsumerProjectionPort):
            def list_agents(self, *, tenant_id: str, connector_id: str):
                return ({"kind": "agent", "id": "agent_alpha"},)

            def list_tools(self, *, tenant_id: str, connector_id: str):
                return ({"kind": "tool", "id": "tool_alpha"},)

            def list_projects(self, *, tenant_id: str, connector_id: str):
                return ({"kind": "project", "id": "proj_alpha"},)

            def count_chats_with_grant(self, *, tenant_id: str, connector_id: str):
                return 3

        store = InMemoryConnectorsStore()
        service = ConnectorsService(
            store=store, catalog=(), consumer_projection=_StubPort()
        )
        record = _seed(service, store)
        projection = service.project_consumers(
            tenant_id="tenant_a", connector_id=record.id
        )
        assert projection["agents"] == ({"kind": "agent", "id": "agent_alpha"},)
        assert projection["tools"] == ({"kind": "tool", "id": "tool_alpha"},)
        assert projection["projects"] == ({"kind": "project", "id": "proj_alpha"},)
        assert projection["chats_with_grant"] == 3


class TestCatalogLoad:
    def test_real_catalog_yaml_loads(self) -> None:
        entries = load_catalog()
        slugs = {e.slug for e in entries}
        # Sanity: every slug we claim to support shows up.
        for required in {
            "gmail",
            "gcal",
            "slack",
            "salesforce",
            "github",
            "gdrive",
            "notion",
            "outlook",
        }:
            assert required in slugs, f"missing {required} from catalog.yaml"


class TestAuditListing:
    def test_list_audit_enforces_read_acl(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        # Out-of-tenant caller → 404, not a leak of the audit rows.
        with pytest.raises(ConnectorNotFound):
            service.list_audit(
                tenant_id="tenant_b",
                caller_user_id="user_1",
                caller_roles=(),
                connector_id=record.id,
            )

    def test_list_audit_returns_seeded_rows(self) -> None:
        service, store = _service()
        record = _seed(service, store)
        service.disconnect(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
        )
        rows, _ = service.list_audit(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
        )
        actions = {r.action for r in rows}
        assert "connector.connected" in actions
        assert "connector.disconnected" in actions


class TestSetAccessMode:
    """PRD-06 D2 — audit exactly one row per real change; zero on a no-op."""

    def test_change_writes_one_audit_row_with_correlation(self) -> None:
        service, store = _service()
        record = _seed(service, store, owner_user_id="user_1")
        # Seeded rows default to access_mode="read".
        assert record.access_mode == "read"
        out = service.set_access_mode(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
            access_mode="off",
        )
        assert out.access_mode == "off"
        changed = [
            r
            for r in store.audits
            if r.target_id == record.id and r.action == "connector.access_mode_changed"
        ]
        assert len(changed) == 1
        assert changed[0].correlation_id == "read->off"

    def test_set_to_current_value_writes_zero_audit_rows(self) -> None:
        service, store = _service()
        record = _seed(service, store, owner_user_id="user_1")
        out = service.set_access_mode(
            tenant_id="tenant_a",
            caller_user_id="user_1",
            caller_roles=(),
            connector_id=record.id,
            access_mode="read",  # equal to the stored value
        )
        assert out.access_mode == "read"
        changed = [
            r for r in store.audits if r.action == "connector.access_mode_changed"
        ]
        assert changed == []

    def test_non_owner_non_admin_cannot_set(self) -> None:
        service, store = _service()
        record = _seed(service, store, owner_user_id="user_1")
        with pytest.raises(ConnectorForbidden):
            service.set_access_mode(
                tenant_id="tenant_a",
                caller_user_id="user_2",
                caller_roles=(),
                connector_id=record.id,
                access_mode="off",
            )


class TestCatalogEntryConstructor:
    def test_constructor_defaults(self) -> None:
        entry = ConnectorCatalogEntry(slug="gmail", display_name="Gmail")
        assert entry.slug == "gmail"
        assert entry.description == ""
        assert entry.icon_hint is None
