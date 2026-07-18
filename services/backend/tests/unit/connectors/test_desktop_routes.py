"""AC9 — desktop MCP connector HTTP routes (full round-trip through FastAPI).

Exercises the desktop-only OAuth transport end to end at the HTTP layer against
a fake OAuth/MCP server: catalog → start-oauth → oauth-callback → connected.
Also proves the identity/owner-match and preview/admin gates surface as stable
HTTP status codes, and that provider tokens never appear in any route response.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.connectors.desktop_routes import (
    register_desktop_connector_routes,
)
from backend_app.connectors.oauth_coordinator import DesktopMcpOAuthCoordinator
from backend_app.connectors.profile_catalog import DesktopProfileCatalog
from backend_app.contracts import OAuthTokenRequest, OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.mcp_oauth import McpAuthorization
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore
from backend_app.token_vault import LocalTokenVault

_TOKEN_CANARY = "provider-access-token-CANARY-desktop-routes"


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
    return store


class _FakeOAuthClient:
    def authorization(
        self, *, record, redirect_uri, state, code_challenge, token_vault
    ) -> McpAuthorization:
        return McpAuthorization(
            auth_url=(
                "https://fake-idp.example.com/authorize"
                f"?state={state}&redirect_uri={redirect_uri}"
            ),
            discovery={"token_endpoint": "https://fake-idp.example.com/token"},
            required_scopes=("read:jira-work",),
        )

    def refresh_token(self, *, record, refresh_token, token_vault):
        raise AssertionError("refresh not expected")


class _FakeExchanger:
    def exchange_code(self, *, record, session, code, token_vault) -> OAuthTokenRequest:
        assert code == "auth-code-123"
        return OAuthTokenRequest(
            access_token=_TOKEN_CANARY,
            refresh_token=None,
            token_type="Bearer",
            expires_at=None,
        )


def _client(*, preview_enabled: bool = False) -> tuple[TestClient, InMemoryMcpStore]:
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
    )
    mcp_store = InMemoryMcpStore()
    mcp_service = McpRegistryService(
        store=mcp_store,
        token_vault=LocalTokenVault(secret="x" * 40),
        oauth_client=_FakeOAuthClient(),
        token_exchanger=_FakeExchanger(),
        auth_session_ttl=timedelta(minutes=5),
    )
    catalog = DesktopProfileCatalog.load()
    coordinator = DesktopMcpOAuthCoordinator(
        mcp_service=mcp_service,
        catalog=catalog,
        preview_enabled=preview_enabled,
    )
    # Drop the create_app()-registered desktop routes and re-register against
    # the fake-backed coordinator so the round-trip needs no network.
    app.router.routes = [
        r
        for r in app.router.routes
        if not getattr(r, "path", "").startswith("/v1/connectors/desktop")
        and getattr(r, "path", "") != "/v1/connectors/{slug}/desktop/start-oauth"
    ]
    register_desktop_connector_routes(
        app, coordinator=coordinator, catalog=catalog, preview_enabled=preview_enabled
    )
    return TestClient(app), mcp_store


def _q() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


def _loopback_body(scope: str = "read") -> dict[str, object]:
    return {
        "callback": {
            "kind": "desktop_loopback",
            "port": 53123,
            "path": "/connectors/oauth/cb",
        },
        "requested_product_scope": scope,
    }


class TestCatalog:
    def test_reconciled_catalog_lists_slugs(self) -> None:
        client, _store = _client()
        resp = client.get("/v1/connectors/desktop/catalog", params=_q())
        assert resp.status_code == 200, resp.text
        slugs = {e["slug"] for e in resp.json()["entries"]}
        assert {"gmail", "gdrive", "outlook", "atlassian"} <= slugs

    def test_atlassian_available_gmail_preview_by_default(self) -> None:
        client, _store = _client(preview_enabled=False)
        entries = {
            e["slug"]: e
            for e in client.get("/v1/connectors/desktop/catalog", params=_q()).json()[
                "entries"
            ]
        }
        assert entries["atlassian"]["availability"] == "available"
        assert entries["gmail"]["availability"] == "preview"
        # Outlook is preview AND tenant-templated; preview is the honest default
        # first (the deployment hasn't enabled preview connectors).
        assert entries["outlook"]["availability"] == "preview"

    def test_outlook_admin_setup_when_preview_enabled(self) -> None:
        client, _store = _client(preview_enabled=True)
        entries = {
            e["slug"]: e
            for e in client.get("/v1/connectors/desktop/catalog", params=_q()).json()[
                "entries"
            ]
        }
        # With preview on, the tenant-template gate surfaces.
        assert entries["outlook"]["availability"] == "admin_setup_required"


class TestOAuthRoundTrip:
    def test_start_then_callback_connects(self) -> None:
        client, store = _client()
        start = client.post(
            "/v1/connectors/atlassian/desktop/start-oauth",
            params=_q(),
            json=_loopback_body(),
        )
        assert start.status_code == 200, start.text
        body = start.json()
        assert body["oauth_session_id"] == body["state"]
        # The redirect URI was reconstructed server-side (never client-supplied).
        assert "http://127.0.0.1:53123/connectors/oauth/cb" in body["authorization_url"]
        assert body["requested_permissions"] == ["read:jira-work"]

        cb = client.post(
            "/v1/connectors/desktop/oauth-callback",
            params=_q(),
            json={
                "oauth_session_id": body["oauth_session_id"],
                "state": body["state"],
                "code": "auth-code-123",
            },
        )
        assert cb.status_code == 200, cb.text
        result = cb.json()
        assert result["server_id"] == "seed:atlassian"
        assert result["connector_slug"] == "atlassian"
        assert result["auth_state"] == "authenticated"

        # Secret canary: the token exists only as vault ciphertext, never in a
        # route response.
        assert _TOKEN_CANARY not in start.text
        assert _TOKEN_CANARY not in cb.text
        envelope = store.get_token(server_id="seed:atlassian")
        assert envelope is not None
        assert _TOKEN_CANARY not in envelope.encrypted_access_token

    def test_wrong_owner_callback_fails_closed(self) -> None:
        client, _store = _client()
        start = client.post(
            "/v1/connectors/atlassian/desktop/start-oauth",
            params=_q(),
            json=_loopback_body(),
        ).json()
        # A different identity presenting someone else's state is rejected.
        cb = client.post(
            "/v1/connectors/desktop/oauth-callback",
            params={"org_id": "org_acme", "user_id": "usr_mallory"},
            json={
                "oauth_session_id": start["oauth_session_id"],
                "state": start["state"],
                "code": "auth-code-123",
            },
        )
        assert cb.status_code == 400
        assert cb.json()["detail"] == "connector_oauth_state_invalid"


class TestGates:
    def test_preview_disabled_returns_403(self) -> None:
        client, _store = _client(preview_enabled=False)
        resp = client.post(
            "/v1/connectors/gmail/desktop/start-oauth",
            params=_q(),
            json=_loopback_body(),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "connector_preview_disabled"

    def test_admin_setup_required_returns_403(self) -> None:
        client, _store = _client(preview_enabled=True)
        resp = client.post(
            "/v1/connectors/outlook/desktop/start-oauth",
            params=_q(),
            json=_loopback_body(),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "connector_admin_setup_required"

    def test_privileged_loopback_port_rejected(self) -> None:
        client, _store = _client()
        resp = client.post(
            "/v1/connectors/atlassian/desktop/start-oauth",
            params=_q(),
            json={
                "callback": {
                    "kind": "desktop_loopback",
                    "port": 80,
                    "path": "/connectors/oauth/cb",
                },
                "requested_product_scope": "read",
            },
        )
        assert resp.status_code == 422  # pydantic body validation (ge=1024)
