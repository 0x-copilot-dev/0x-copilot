"""Integration tests for the MCP → connectors write-through (PR-E.3 D1).

Exercises the REAL route wiring end-to-end over ``create_app``: every
MCP mutation (register-by-URL, catalog install, OAuth start/complete,
auth skip, enable/disable PATCH, delete) must land in the denormalized
``/v1/connectors`` read model with an HONEST status projection, publish
on the tenant SSE channel, and append a ``connector.*`` audit row.

Fixture style follows ``tests/test_mcp_api_flow.py`` (fake OAuth client
+ exchanger over ``InMemoryMcpStore``) and
``tests/unit/connectors/test_connectors_routes.py`` (seeded identity
store + injected in-memory connectors store).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.connectors.sse import InMemoryConnectorActivityBus
from backend_app.connectors.store import InMemoryConnectorsStore
from backend_app.contracts import OAuthTokenRequest, OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.mcp_oauth import McpAuthorization
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore

ORG = "org_acme"
USER = "usr_sarah"
OTHER_ORG = "org_beta"
OTHER_USER = "usr_eve"


class FakeOAuthTokenExchanger:
    def exchange_code(self, **kwargs) -> OAuthTokenRequest:
        return OAuthTokenRequest(
            access_token=f"access-token-for-{kwargs['code']}",
            refresh_token=f"refresh-token-for-{kwargs['code']}",
        )


class FakeOAuthClient:
    def authorization(self, **kwargs) -> McpAuthorization:
        return McpAuthorization(
            auth_url=f"https://auth.example.com/authorize?state={kwargs['state']}",
            discovery={
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "oauth_client": {"client_id": "client_123"},
            },
            required_scopes=("mcp",),
        )

    def refresh_token(self, **kwargs) -> OAuthTokenRequest:
        return OAuthTokenRequest(access_token="refreshed-access-token")


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id=ORG, display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id=USER,
            org_id=ORG,
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
        )
    )
    store.create_organization(
        OrganizationRecord(org_id=OTHER_ORG, display_name="Beta", slug="beta")
    )
    store.create_user(
        UserRecord(
            user_id=OTHER_USER,
            org_id=OTHER_ORG,
            primary_email="eve@beta.com",
            display_name="Eve",
        )
    )
    return store


def _make_app() -> tuple[TestClient, FastAPI, InMemoryMcpStore]:
    # The SSE bus is a process-global singleton; reset so each test's
    # sequence numbers start at 1 (same discipline as the projects bus).
    InMemoryConnectorActivityBus.reset_default_for_tests()
    mcp_store = InMemoryMcpStore()
    app = create_app(
        McpRegistryService(
            store=mcp_store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        ),
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        connectors_store=InMemoryConnectorsStore(),
    )
    return TestClient(app), app, mcp_store


def _q(org: str = ORG, user: str = USER) -> dict[str, str]:
    return {"org_id": org, "user_id": user}


def _register(client: TestClient, *, auth_mode: str = "oauth2") -> dict:
    resp = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": ORG,
            "user_id": USER,
            "url": "https://mcp.example.com",
            "display_name": "Drive MCP",
            "auth_mode": auth_mode,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _connectors(client: TestClient, *, org: str = ORG, user: str = USER) -> list[dict]:
    resp = client.get("/v1/connectors", params=_q(org, user))
    assert resp.status_code == 200, resp.text
    return resp.json()["connectors"]


def _events(app: FastAPI, *, org: str = ORG, user: str = USER) -> list:
    bus = app.state.connector_activity_bus
    return list(bus.list_after(org_id=org, user_id=user, after_sequence=0))


class TestRegisterByUrl:
    def test_oauth_server_lists_as_honest_pending(self) -> None:
        client, app, _ = _make_app()
        _register(client, auth_mode="oauth2")
        rows = _connectors(client)
        assert len(rows) == 1
        row = rows[0]
        assert row["display_name"] == "Drive MCP"
        assert row["slug"] == "drive_mcp"
        # No token yet — the row must NOT claim connected.
        assert row["status"] == "disconnected"
        assert row["status_reason"] == "unauthenticated"

    def test_no_auth_server_lists_as_connected(self) -> None:
        client, app, _ = _make_app()
        _register(client, auth_mode="none")
        rows = _connectors(client)
        assert len(rows) == 1
        assert rows[0]["status"] == "connected"
        assert rows[0]["status_reason"] is None

    def test_register_is_idempotent_in_read_model(self) -> None:
        client, app, _ = _make_app()
        _register(client)
        _register(client)  # same URL — MCP path returns the existing row
        rows = _connectors(client)
        assert len(rows) == 1

    def test_register_appends_connector_audit_row(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        store = app.state.connectors_store
        actions = [a.action for a in store.audits]
        assert "connector.installed" in actions
        audit = next(a for a in store.audits if a.action == "connector.installed")
        assert audit.tenant_id == ORG
        assert audit.actor_user_id == USER
        assert audit.correlation_id == f"mcp:{created['server_id']}"


class TestCatalogInstall:
    def test_install_lists_connector_by_catalog_slug(self) -> None:
        client, app, _ = _make_app()
        resp = client.post(
            "/v1/mcp/servers/install",
            json={"org_id": ORG, "user_id": USER, "slug": "asana"},
        )
        assert resp.status_code == 200, resp.text
        rows = _connectors(client)
        assert len(rows) == 1
        assert rows[0]["slug"] == "asana"
        assert rows[0]["status"] == "disconnected"
        assert rows[0]["status_reason"] == "unauthenticated"


class TestOAuthLifecycle:
    def _start_auth(self, client: TestClient, server_id: str, store) -> str:
        resp = client.post(
            f"/v1/mcp/servers/{server_id}/auth/start",
            json={
                "org_id": ORG,
                "user_id": USER,
                "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
            },
        )
        assert resp.status_code == 200, resp.text
        return next(iter(store.auth_sessions.keys()))

    def test_start_auth_projects_auth_pending(self) -> None:
        client, app, mcp_store = _make_app()
        created = _register(client)
        self._start_auth(client, created["server_id"], mcp_store)
        row = _connectors(client)[0]
        assert row["status"] == "disconnected"
        assert row["status_reason"] == "auth_pending"

    def test_complete_auth_flips_connected(self) -> None:
        client, app, mcp_store = _make_app()
        created = _register(client)
        state = self._start_auth(client, created["server_id"], mcp_store)
        resp = client.get(
            "/v1/mcp/oauth/callback", params={"state": state, "code": "oauth_code"}
        )
        assert resp.status_code == 200, resp.text
        row = _connectors(client)[0]
        assert row["status"] == "connected"
        assert row["status_reason"] is None
        # OAuth exchange success is recorded as the last sync point.
        assert row["last_sync_at"] is not None
        actions = [a.action for a in app.state.connectors_store.audits]
        assert "connector.connected" in actions

    def test_skip_auth_projects_connected_with_reason(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        resp = client.post(
            f"/v1/mcp/servers/{created['server_id']}/auth/skip", params=_q()
        )
        assert resp.status_code == 200, resp.text
        row = _connectors(client)[0]
        assert row["status"] == "connected"
        assert row["status_reason"] == "auth_skipped"


class TestDisableAndDelete:
    def test_disable_patch_projects_disconnected(self) -> None:
        client, app, _ = _make_app()
        created = _register(client, auth_mode="none")
        resp = client.patch(
            f"/v1/mcp/servers/{created['server_id']}",
            params=_q(),
            json={"enabled": False},
        )
        assert resp.status_code == 200, resp.text
        row = _connectors(client)[0]
        assert row["status"] == "disconnected"
        assert row["status_reason"] == "disabled"

    def test_reenable_patch_projects_connected_again(self) -> None:
        client, app, _ = _make_app()
        created = _register(client, auth_mode="none")
        server_id = created["server_id"]
        client.patch(
            f"/v1/mcp/servers/{server_id}", params=_q(), json={"enabled": False}
        )
        client.patch(
            f"/v1/mcp/servers/{server_id}", params=_q(), json={"enabled": True}
        )
        row = _connectors(client)[0]
        assert row["status"] == "connected"

    def test_delete_projects_removed(self) -> None:
        client, app, _ = _make_app()
        created = _register(client, auth_mode="none")
        resp = client.delete(f"/v1/mcp/servers/{created['server_id']}", params=_q())
        assert resp.status_code == 204, resp.text
        row = _connectors(client)[0]
        assert row["status"] == "disconnected"
        assert row["status_reason"] == "mcp_server_deleted"
        actions = [a.action for a in app.state.connectors_store.audits]
        assert "connector.removed" in actions

    def test_delete_unknown_server_does_not_touch_read_model(self) -> None:
        client, app, _ = _make_app()
        resp = client.delete("/v1/mcp/servers/nope", params=_q())
        assert resp.status_code == 404
        assert _connectors(client) == []


class TestSseEmission:
    def test_register_publishes_connector_created(self) -> None:
        client, app, _ = _make_app()
        _register(client)
        events = _events(app)
        assert [e.event_type for e in events] == ["connector.created"]
        assert events[0].connector is not None
        assert events[0].connector["slug"] == "drive_mcp"
        assert events[0].connector["status"] == "disconnected"
        # Token bytes / vault pointers never ride the stream.
        assert "vault_ref" not in events[0].connector

    def test_auth_complete_publishes_status_changed(self) -> None:
        client, app, mcp_store = _make_app()
        created = _register(client)
        client.post(
            f"/v1/mcp/servers/{created['server_id']}/auth/start",
            json={
                "org_id": ORG,
                "user_id": USER,
                "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
            },
        )
        state = next(iter(mcp_store.auth_sessions.keys()))
        client.get(
            "/v1/mcp/oauth/callback", params={"state": state, "code": "oauth_code"}
        )
        events = _events(app)
        assert events[0].event_type == "connector.created"
        assert events[-1].event_type == "connector.status_changed"
        assert events[-1].connector["status"] == "connected"


class TestInternalRoutes:
    """PRD-I I1 — the ai-backend-driven internal routes write through too."""

    def _internal_start_auth(self, client: TestClient, server_id: str) -> None:
        resp = client.post(
            f"/internal/v1/mcp/servers/{server_id}/auth/start",
            json={
                "org_id": ORG,
                "user_id": USER,
                "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
            },
        )
        assert resp.status_code == 200, resp.text

    def test_internal_start_auth_projects_auth_pending(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        self._internal_start_auth(client, created["server_id"])
        row = _connectors(client)[0]
        assert row["status"] == "disconnected"
        assert row["status_reason"] == "auth_pending"
        actions = [a.action for a in app.state.connectors_store.audits]
        assert "connector.updated" in actions

    def test_internal_start_auth_publishes_status_changed(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        self._internal_start_auth(client, created["server_id"])
        events = _events(app)
        assert events[-1].event_type == "connector.status_changed"
        assert events[-1].connector["status_reason"] == "auth_pending"

    def test_complete_auth_after_internal_start_converges_connected(self) -> None:
        client, app, mcp_store = _make_app()
        created = _register(client)
        self._internal_start_auth(client, created["server_id"])
        state = next(iter(mcp_store.auth_sessions.keys()))
        resp = client.get(
            "/v1/mcp/oauth/callback", params={"state": state, "code": "oauth_code"}
        )
        assert resp.status_code == 200, resp.text
        row = _connectors(client)[0]
        assert row["status"] == "connected"
        assert row["status_reason"] is None
        actions = [a.action for a in app.state.connectors_store.audits]
        assert "connector.connected" in actions

    def test_internal_test_token_projects_connected(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        resp = client.post(
            f"/internal/v1/mcp/servers/{created['server_id']}/test-token",
            params=_q(),
            json={"access_token": "test-access-token"},
        )
        assert resp.status_code == 200, resp.text
        row = _connectors(client)[0]
        assert row["status"] == "connected"
        assert row["status_reason"] is None
        actions = [a.action for a in app.state.connectors_store.audits]
        assert "connector.connected" in actions

    def test_internal_routes_respect_tenant_isolation(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        self._internal_start_auth(client, created["server_id"])
        # The other tenant sees neither the row nor the SSE events.
        assert _connectors(client, org=OTHER_ORG, user=OTHER_USER) == []
        assert _events(app, org=OTHER_ORG, user=OTHER_USER) == []

    def test_internal_start_auth_for_other_tenants_server_is_404(self) -> None:
        client, app, _ = _make_app()
        created = _register(client)
        resp = client.post(
            f"/internal/v1/mcp/servers/{created['server_id']}/auth/start",
            json={
                "org_id": OTHER_ORG,
                "user_id": OTHER_USER,
                "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
            },
        )
        assert resp.status_code in (400, 404), resp.text
        # No write-through happened for either tenant's read model.
        assert _connectors(client, org=OTHER_ORG, user=OTHER_USER) == []
        row = _connectors(client)[0]
        assert row["status_reason"] == "unauthenticated"

    def test_internal_write_through_failure_never_fails_the_request(self) -> None:
        """NFR-I.1 — same log-and-continue discipline on internal routes."""

        client, app, _ = _make_app()
        created = _register(client)

        class ExplodingService:
            def write_through_from_mcp(self, **kwargs):
                raise RuntimeError("connectors store down")

        app.state.connectors_service = ExplodingService()
        self._internal_start_auth(client, created["server_id"])  # asserts 200
        resp = client.post(
            f"/internal/v1/mcp/servers/{created['server_id']}/test-token",
            params=_q(),
            json={"access_token": "test-access-token"},
        )
        assert resp.status_code == 200, resp.text


class TestTenantIsolation:
    def test_other_tenant_never_sees_the_connector(self) -> None:
        client, app, _ = _make_app()
        _register(client)
        assert len(_connectors(client)) == 1
        assert _connectors(client, org=OTHER_ORG, user=OTHER_USER) == []
        # The SSE channel is (org, user)-scoped too.
        assert _events(app, org=OTHER_ORG, user=OTHER_USER) == []


class TestLifespanLoopBinding:
    """PRD-I I2 — the app lifespan binds/unbinds the SSE bus loop once."""

    def test_lifespan_binds_then_unbinds_the_connector_bus(self) -> None:
        client, app, _ = _make_app()
        bus = app.state.connector_activity_bus
        assert not bus.loop_bound  # no lifespan yet
        with client:
            assert bus.loop_bound
        assert not bus.loop_bound  # unbound on shutdown

    def test_mutation_under_running_lifespan_still_write_throughs(self) -> None:
        """NFR-I.2 — the wakeup path adds no failure mode to the mutation."""

        client, app, _ = _make_app()
        with client:
            _register(client)
            rows = _connectors(client)
            assert len(rows) == 1
            events = _events(app)
            assert [e.event_type for e in events] == ["connector.created"]


class TestFailureDiscipline:
    def test_write_through_failure_never_fails_the_mcp_request(self) -> None:
        """Log-and-continue: the MCP mutation already committed."""

        client, app, _ = _make_app()

        class ExplodingService:
            def write_through_from_mcp(self, **kwargs):
                raise RuntimeError("connectors store down")

        app.state.connectors_service = ExplodingService()
        created = _register(client)  # asserts 200 internally
        assert created["server_id"]
        # Read model lagged (empty audit trail, no row) but the MCP row
        # is durable and a later mutation reconverges the projection.
        assert app.state.connectors_store.audits == []
