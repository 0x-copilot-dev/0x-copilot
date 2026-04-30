from __future__ import annotations

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore


def test_public_and_internal_mcp_auth_flow() -> None:
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
