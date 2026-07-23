"""Tests for the ``/v1/connectors`` HTTP routes (P11-A2 §4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.connectors.service import (
    ConnectorCatalogEntry,
    ConnectorsService,
    ConsumerProjectionPort,
)
from backend_app.connectors.store import (
    ConnectorScopeEntry,
    InMemoryConnectorsStore,
    McpUpsertInput,
)
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_bob",
            org_id="org_acme",
            primary_email="bob@acme.com",
            display_name="Bob",
        )
    )
    return store


def _client(
    *,
    connectors_store: InMemoryConnectorsStore | None = None,
    catalog: tuple[ConnectorCatalogEntry, ...] = (
        ConnectorCatalogEntry(slug="gmail", display_name="Gmail", description="Mail."),
        ConnectorCatalogEntry(slug="slack", display_name="Slack", description="Chat."),
    ),
    consumer_projection: ConsumerProjectionPort | None = None,
) -> tuple[TestClient, InMemoryConnectorsStore]:
    store = connectors_store or InMemoryConnectorsStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        connectors_store=store,
    )
    # Re-register the connectors service with our test catalog +
    # consumer projection. We drop the existing /v1/connectors routes
    # first so the new service is the one the routes consult.
    service = ConnectorsService(
        store=store,
        catalog=catalog,
        consumer_projection=consumer_projection,
    )
    app.state.connectors_service = service
    app.router.routes = [
        r
        for r in app.router.routes
        if not getattr(r, "path", "").startswith("/v1/connectors")
    ]
    from backend_app.connectors.routes import register_connector_routes

    register_connector_routes(app, service=service)
    return TestClient(app), store


def _q(user: str = "usr_sarah") -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": user}


def _seed_record(
    store: InMemoryConnectorsStore,
    *,
    owner_user_id: str = "usr_sarah",
    slug: str = "gmail",
    status: str = "connected",
):
    return store.upsert_from_mcp_registration(
        McpUpsertInput(
            tenant_id="org_acme",
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
        )
    )


class TestListEndpoint:
    def test_list_returns_installed_and_available(self) -> None:
        client, store = _client()
        _seed_record(store, slug="gmail")
        resp = client.get("/v1/connectors", params=_q())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # gmail installed; slack still in available.
        installed_slugs = [c["slug"] for c in body["connectors"]]
        available_slugs = [c["slug"] for c in body["available"]]
        assert "gmail" in installed_slugs
        assert "gmail" not in available_slugs
        assert "slack" in available_slugs

    def test_list_filter_status_or(self) -> None:
        client, store = _client()
        _seed_record(store, slug="gmail", status="connected")
        _seed_record(store, slug="slack", status="error")
        resp = client.get(
            "/v1/connectors",
            params=[
                ("org_id", "org_acme"),
                ("user_id", "usr_sarah"),
                ("filter[status]", "error"),
            ],
        )
        assert resp.status_code == 200
        slugs = [c["slug"] for c in resp.json()["connectors"]]
        assert slugs == ["slack"]

    def test_list_pagination(self) -> None:
        client, store = _client()
        # Seed several rows.
        for slug in ("a", "b", "c", "d"):
            _seed_record(store, slug=slug)
        resp = client.get("/v1/connectors", params={**_q(), "limit": 2})
        body = resp.json()
        assert len(body["connectors"]) == 2
        assert body["next_cursor"] is not None


class TestDetailEndpoint:
    def test_detail_returns_connector_and_consumers(self) -> None:
        class _Port(ConsumerProjectionPort):
            def list_agents(self, *, tenant_id, connector_id):
                return ({"kind": "agent", "id": "agent_atlas"},)

            def count_chats_with_grant(self, *, tenant_id, connector_id):
                return 2

        client, store = _client(consumer_projection=_Port())
        record = _seed_record(store)
        resp = client.get(f"/v1/connectors/{record.id}", params=_q())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connector"]["id"] == record.id
        assert body["consumers"]["agents"] == [{"kind": "agent", "id": "agent_atlas"}]
        assert body["consumers"]["chats_with_grant"] == 2

    def test_detail_out_of_tenant_returns_404(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        # Sarah belongs to org_acme but the route is forced via query
        # — we ask for a different tenant by mocking the query identity.
        resp = client.get(
            f"/v1/connectors/{record.id}",
            params={"org_id": "org_zeta", "user_id": "usr_sarah"},
        )
        assert resp.status_code == 404


class TestDisconnectEndpoint:
    def test_owner_can_disconnect(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        resp = client.post(f"/v1/connectors/{record.id}/disconnect", params=_q())
        assert resp.status_code == 200
        assert resp.json()["connector"]["status"] == "disconnected"

    def test_non_owner_non_admin_403(self) -> None:
        client, store = _client()
        record = _seed_record(store, owner_user_id="usr_sarah")
        # Bob is in tenant but not the owner.
        resp = client.post(
            f"/v1/connectors/{record.id}/disconnect",
            params=_q(user="usr_bob"),
        )
        assert resp.status_code == 403

    def test_idempotent_when_already_disconnected(self) -> None:
        client, store = _client()
        record = _seed_record(store, status="disconnected")
        resp = client.post(f"/v1/connectors/{record.id}/disconnect", params=_q())
        assert resp.status_code == 200
        assert resp.json()["connector"]["status"] == "disconnected"


class TestRefreshEndpoint:
    def test_owner_refresh_flips_to_connected_and_audits(self) -> None:
        client, store = _client()
        record = _seed_record(store, status="error")
        resp = client.post(f"/v1/connectors/{record.id}/refresh", params=_q())
        assert resp.status_code == 200
        body = resp.json()
        assert body["connector"]["status"] == "connected"
        assert body["connector"]["last_sync_at"] is not None
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.token_refreshed" in actions

    def test_404_for_unknown_connector(self) -> None:
        client, _ = _client()
        resp = client.post("/v1/connectors/conn_unknown/refresh", params=_q())
        assert resp.status_code == 404


class TestScopePatchEndpoint:
    def test_scope_shrink_returns_202_reauth_url(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        resp = client.patch(
            f"/v1/connectors/{record.id}/scopes",
            params=_q(),
            json={
                "scopes": [
                    {
                        "scope": "gmail.read",
                        "granted": False,
                        "description": "",
                    }
                ]
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "reauth_url" in body
        assert "state" in body
        # Audit emitted.
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.scope_removed" in actions


class TestAccessModePatchEndpoint:
    """PRD-06 D2 — ``PATCH /v1/connectors/{id}/access-mode`` (200, not 202)."""

    def test_owner_sets_read_act_returns_200(self) -> None:
        client, store = _client()
        record = _seed_record(store)  # defaults to access_mode="read"
        resp = client.patch(
            f"/v1/connectors/{record.id}/access-mode",
            params=_q(),
            json={"access_mode": "read_act"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connector"]["access_mode"] == "read_act"
        actions = [r.action for r in store.audits if r.target_id == record.id]
        assert "connector.access_mode_changed" in actions

    def test_non_owner_non_admin_403_owner_or_admin_only(self) -> None:
        client, store = _client()
        record = _seed_record(store, owner_user_id="usr_sarah")
        resp = client.patch(
            f"/v1/connectors/{record.id}/access-mode",
            params=_q(user="usr_bob"),
            json={"access_mode": "off"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "owner_or_admin_only"

    def test_cross_tenant_returns_404_not_403(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        resp = client.patch(
            f"/v1/connectors/{record.id}/access-mode",
            params={"org_id": "org_zeta", "user_id": "usr_sarah"},
            json={"access_mode": "off"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "connector_not_found"

    def test_invalid_mode_returns_400(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        resp = client.patch(
            f"/v1/connectors/{record.id}/access-mode",
            params=_q(),
            json={"access_mode": "maybe"},
        )
        assert resp.status_code == 400


class TestStartOAuthEndpoint:
    def test_start_oauth_returns_stub_url(self) -> None:
        client, _ = _client()
        resp = client.post("/v1/connectors/gmail/start-oauth", params=_q())
        assert resp.status_code == 200
        body = resp.json()
        assert "authorization_url" in body
        assert "state" in body


class TestAuditEndpoint:
    def test_audit_returns_entries(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        # Disconnect to seed a second audit row.
        client.post(f"/v1/connectors/{record.id}/disconnect", params=_q())
        resp = client.get(f"/v1/connectors/{record.id}/audit", params=_q())
        assert resp.status_code == 200, resp.text
        entries = resp.json()["entries"]
        assert any(e["action"] == "connector.disconnected" for e in entries)

    def test_audit_for_other_tenant_returns_404(self) -> None:
        client, store = _client()
        record = _seed_record(store)
        resp = client.get(
            f"/v1/connectors/{record.id}/audit",
            params={"org_id": "org_zeta", "user_id": "usr_sarah"},
        )
        assert resp.status_code == 404
