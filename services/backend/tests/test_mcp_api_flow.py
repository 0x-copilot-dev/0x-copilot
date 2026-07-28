from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError

import pytest
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import (
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerRecord,
    McpTransport,
    OAuthTokenRequest,
    TokenEnvelope,
)
from backend_app.mcp_oauth import McpAuthorization
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
    assert set(session) == {"lease"}
    assert len(session["lease"]) >= 16


def test_restarting_mcp_auth_keeps_existing_token_runtime_loadable() -> None:
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
    auth_payload = {
        "org_id": "org_123",
        "user_id": "user_123",
        "redirect_uri": "http://localhost:5173/mcp/oauth/callback",
    }
    client.post(f"/internal/v1/mcp/servers/{server_id}/auth/start", json=auth_payload)
    state = next(iter(store.auth_sessions.keys()))
    client.get("/v1/mcp/oauth/callback", params={"state": state, "code": "oauth_code"})

    client.post(f"/internal/v1/mcp/servers/{server_id}/auth/start", json=auth_payload)
    record = store.get_server(org_id="org_123", server_id=server_id)
    assert record is not None
    assert record.auth_state == McpAuthState.AUTHENTICATED
    store.update_server(
        record.model_copy(update={"auth_state": McpAuthState.AUTH_PENDING})
    )
    cards = client.get(
        "/internal/v1/mcp/cards",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()
    session = client.post(
        f"/internal/v1/mcp/servers/{server_id}/client-session",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()

    assert cards["servers"][0]["auth_state"] == "authenticated"
    assert set(session) == {"lease"}


def test_internal_mcp_rpc_proxies_with_backend_held_token(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args: object) -> bytes:
            return b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'

    def fake_urlopen(request, timeout):
        captured["server_url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["access_token"] = request.get_header("Authorization").removeprefix(
            "Bearer "
        )
        return Response()

    monkeypatch.setattr("backend_app.mcp_transport.urlopen", fake_urlopen)
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

    lease = client.post(
        f"/internal/v1/mcp/servers/{server_id}/client-session",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()["lease"]
    proxied = client.post(
        f"/internal/v1/mcp/servers/{server_id}/rpc",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "lease": lease,
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

    class Response:
        headers = {"content-type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args: object) -> bytes:
            return b'{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"task list"}]}}'

    def fake_urlopen(request, timeout):
        captured["server_url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["access_token"] = request.get_header("Authorization").removeprefix(
            "Bearer "
        )
        return Response()

    monkeypatch.setattr("backend_app.mcp_transport.urlopen", fake_urlopen)
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

    # PRD-06 D3(c): a ``tools/call`` is gated under the connector's default
    # ``read`` mode (a non-read-only tool would be denied). This test covers
    # the proxy PLUMBING, not the gate, so flip the auto-created connector row
    # to ``read_act`` — which allows the call and skips the classification
    # round-trip, keeping ``captured`` to the single tools/call payload.
    _conn_store = app.state.connectors_store
    _row = next(iter(_conn_store.connectors.values()))
    _conn_store.update_connector(_row.model_copy(update={"access_mode": "read_act"}))

    lease = client.post(
        f"/internal/v1/mcp/servers/{server_id}/client-session",
        params={"org_id": "org_123", "user_id": "user_123"},
    ).json()["lease"]
    proxied = client.post(
        f"/internal/v1/mcp/servers/{server_id}/rpc",
        json={
            "org_id": "org_123",
            "user_id": "user_123",
            "lease": lease,
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


@pytest.mark.parametrize(
    "case,expected_code,expected_status,redispatch_safe",
    [
        ("stale", "lease_stale_pre_dispatch", 409, True),
        ("wrong_owner", "lease_wrong_owner", 403, False),
        ("invalid", "lease_invalid", 400, False),
        ("saturated", "pool_saturated", 429, False),
        ("unavailable", "server_unavailable", 503, False),
        ("auth", "auth_required", 401, False),
        ("ambiguous", "ambiguous_transport_failure", 503, False),
    ],
)
def test_internal_mcp_lease_failure_contract_is_typed_and_nonsecret(
    monkeypatch,
    case: str,
    expected_code: str,
    expected_status: int,
    redispatch_safe: bool,
) -> None:
    endpoint_marker = "https://mcp.invalid/private-endpoint-marker"
    token_marker = "private-token-marker"
    if case == "saturated":
        monkeypatch.setenv("MCP_SESSION_POOL_MAX_TOTAL", "1")
        monkeypatch.setenv("MCP_SESSION_POOL_MAX_PER_KEY", "1")

    store = InMemoryMcpStore()
    service = McpRegistryService(store=store)
    record = McpServerRecord(
        org_id="org",
        user_id="user",
        name="failure-contract-server",
        display_name="Failure contract server",
        url=endpoint_marker,
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2 if case == "auth" else McpAuthMode.NONE,
        auth_state=McpAuthState.AUTHENTICATED,
        health=(
            McpServerHealth.UNAVAILABLE
            if case == "unavailable"
            else McpServerHealth.HEALTHY
        ),
    )
    store.create_server(record)
    if case == "auth":
        store.put_token(
            TokenEnvelope(
                server_id=record.server_id,
                org_id=record.org_id,
                user_id=record.user_id,
                encrypted_access_token=service.token_vault.encrypt(token_marker),
            )
        )

    client = TestClient(create_app(service))
    session_url = f"/internal/v1/mcp/servers/{record.server_id}/client-session"
    rpc_url = f"/internal/v1/mcp/servers/{record.server_id}/rpc"
    owner_params = {"org_id": "org", "user_id": "user"}

    if case == "unavailable":
        response = client.post(session_url, params=owner_params)
    elif case == "saturated":
        first = client.post(session_url, params=owner_params)
        assert first.status_code == 200
        response = client.post(session_url, params=owner_params)
    elif case == "invalid":
        response = client.post(
            rpc_url,
            json={
                **owner_params,
                "lease": "invalid-lease-token",
                "payload": {"jsonrpc": "2.0", "method": "tools/list"},
            },
        )
    else:
        lease_response = client.post(session_url, params=owner_params)
        assert lease_response.status_code == 200
        lease = lease_response.json()["lease"]
        request_identity = dict(owner_params)
        if case == "wrong_owner":
            request_identity["user_id"] = "attacker"
        elif case == "stale":
            store.update_server(
                record.model_copy(
                    update={"updated_at": datetime.now(UTC) + timedelta(seconds=1)}
                )
            )
        elif case == "auth":
            monkeypatch.setattr(
                "backend_app.mcp_transport.urlopen",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    HTTPError(endpoint_marker, 401, "rejected", {}, None)
                ),
            )
        elif case == "ambiguous":
            monkeypatch.setattr(
                "backend_app.mcp_transport.urlopen",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    URLError(f"{endpoint_marker}?token={token_marker}")
                ),
            )
        response = client.post(
            rpc_url,
            json={
                **request_identity,
                "lease": lease,
                "payload": {"jsonrpc": "2.0", "method": "tools/list"},
            },
        )

    assert response.status_code == expected_status
    assert response.json() == {
        "detail": {
            "code": expected_code,
            "redispatch_safe": redispatch_safe,
        }
    }
    serialized = response.text
    assert endpoint_marker not in serialized
    assert token_marker not in serialized
