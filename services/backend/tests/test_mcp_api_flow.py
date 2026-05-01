from __future__ import annotations

from fastapi.testclient import TestClient

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from backend_app.contracts import OAuthTokenRequest
from backend_app.mcp_oauth import McpAuthorization
from backend_app.app import create_app
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore


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


def test_public_and_internal_mcp_auth_flow() -> None:
    store = InMemoryMcpStore()
    app = create_app(
        McpRegistryService(
            store=store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        )
    )
    client = TestClient(app)

    created = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://mcp.example.com",
            "display_name": "Drive MCP",
        },
    ).json()
    server_id = created["server_id"]

    cards_before_auth = client.get(
        "/internal/v1/mcp/cards",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    auth = client.post(
        f"/internal/v1/mcp/servers/{server_id}/auth/start",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
        },
    ).json()
    state = next(iter(store.auth_sessions.keys()))
    completed = client.get(
        "/v1/mcp/oauth/callback",
        params={"state": state, "code": "oauth_code"},
    ).json()
    session = client.post(
        f"/internal/v1/mcp/servers/{server_id}/client-session",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()

    assert cards_before_auth["servers"][0]["auth_state"] == "unauthenticated"
    assert "state=" in auth["auth_url"]
    assert completed["auth_state"] == "authenticated"
    assert session["credential_ref"]


def test_internal_mcp_rpc_proxies_with_backend_held_token(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_remote_rpc(
        server_url: str, payload: dict[str, object], access_token: str
    ) -> dict[str, object]:
        captured["server_url"] = server_url
        captured["payload"] = payload
        captured["access_token"] = access_token
        return {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}

    monkeypatch.setattr(
        McpRegistryService, "_post_remote_mcp_rpc", staticmethod(fake_remote_rpc)
    )
    store = InMemoryMcpStore()
    app = create_app(
        McpRegistryService(
            store=store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        )
    )
    client = TestClient(app)
    created = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://mcp.example.com/mcp",
            "display_name": "Drive MCP",
        },
    ).json()
    server_id = created["server_id"]
    client.post(
        f"/internal/v1/mcp/servers/{server_id}/auth/start",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
        },
    )
    state = next(iter(store.auth_sessions.keys()))
    client.get(
        "/v1/mcp/oauth/callback",
        params={"state": state, "code": "oauth_code"},
    )

    proxied = client.post(
        f"/internal/v1/mcp/servers/{server_id}/rpc",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "payload": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        },
    ).json()

    assert proxied["payload"]["result"]["tools"] == []
    assert captured == {
        "server_url": "https://mcp.example.com/mcp",
        "payload": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        "access_token": "access-token-for-oauth_code",
    }


def test_internal_mcp_rpc_proxies_tools_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_remote_rpc(
        server_url: str, payload: dict[str, object], access_token: str
    ) -> dict[str, object]:
        captured["server_url"] = server_url
        captured["payload"] = payload
        captured["access_token"] = access_token
        return {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "task list"}]},
        }

    monkeypatch.setattr(
        McpRegistryService, "_post_remote_mcp_rpc", staticmethod(fake_remote_rpc)
    )
    store = InMemoryMcpStore()
    app = create_app(
        McpRegistryService(
            store=store,
            token_exchanger=FakeOAuthTokenExchanger(),
            oauth_client=FakeOAuthClient(),
        )
    )
    client = TestClient(app)
    created = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://mcp.example.com/mcp",
            "display_name": "Drive MCP",
        },
    ).json()
    server_id = created["server_id"]
    client.post(
        f"/internal/v1/mcp/servers/{server_id}/auth/start",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
        },
    )
    state = next(iter(store.auth_sessions.keys()))
    client.get(
        "/v1/mcp/oauth/callback",
        params={"state": state, "code": "oauth_code"},
    )

    proxied = client.post(
        f"/internal/v1/mcp/servers/{server_id}/rpc",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "payload": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "list_tasks",
                    "arguments": {"include_closed": True},
                },
            },
        },
    ).json()

    assert proxied["payload"]["result"]["content"][0]["text"] == "task list"
    assert captured == {
        "server_url": "https://mcp.example.com/mcp",
        "payload": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_tasks",
                "arguments": {"include_closed": True},
            },
        },
        "access_token": "access-token-for-oauth_code",
    }


def test_mcp_update_disable_remove_flow() -> None:
    store = InMemoryMcpStore()
    app = create_app(McpRegistryService(store=store))
    client = TestClient(app)

    created = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://mcp.example.com",
            "display_name": "Drive MCP",
        },
    ).json()
    server_id = created["server_id"]

    disabled = client.patch(
        f"/v1/mcp/servers/{server_id}",
        params={"org_id": "org_123", "user_id": "user_123"},
        json={"enabled": False},
    ).json()
    cards = client.get(
        "/internal/v1/mcp/cards",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    enabled = client.patch(
        f"/v1/mcp/servers/{server_id}",
        params={"org_id": "org_123", "user_id": "user_123"},
        json={"enabled": True},
    ).json()
    deleted = client.delete(
        f"/v1/mcp/servers/{server_id}",
        params={"org_id": "org_123", "user_id": "user_123"},
    )

    assert disabled["enabled"] is False
    assert disabled["health"] == "disabled"
    assert cards["servers"] == []
    assert enabled["enabled"] is True
    assert enabled["health"] == "healthy"
    assert deleted.status_code == 204


def test_mcp_server_response_hides_oauth_client_secret() -> None:
    app = create_app(McpRegistryService(store=InMemoryMcpStore()))
    client = TestClient(app)

    created = client.post(
        "/v1/mcp/servers",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "url": "https://mcp.example.com",
            "display_name": "Generic MCP",
            "oauth_client": {
                "client_id": "configured_client",
                "client_secret": "configured_secret",
                "scope": "mcp",
            },
        },
    ).json()
    listed = client.get(
        "/v1/mcp/servers",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()

    assert created["oauth_client_configured"] is True
    assert "configured_secret" not in str(created)
    assert "configured_secret" not in str(listed)


def test_internal_mcp_routes_use_service_header_scope_when_token_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "service-token")
    store = InMemoryMcpStore()
    app = create_app(McpRegistryService(store=store))
    client = TestClient(app)
    headers = {
        SERVICE_TOKEN_HEADER: "service-token",
        ORG_HEADER: "org_123",
        USER_HEADER: "user_123",
    }

    created = client.post(
        "/v1/mcp/servers",
        headers=headers,
        json={
            "org_id": "forged_org",
            "user_id": "forged_user",
            "url": "https://mcp.example.com",
            "display_name": "Drive MCP",
        },
    ).json()
    cards = client.get(
        "/internal/v1/mcp/cards",
        headers=headers,
        params={"org_id": "forged_org", "user_id": "forged_user"},
    ).json()

    assert created["server_id"] == cards["servers"][0]["server_id"]
    assert cards["servers"][0]["display_name"] == "Drive MCP"
