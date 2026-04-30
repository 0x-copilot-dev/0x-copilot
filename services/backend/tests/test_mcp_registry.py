from __future__ import annotations

from backend_app.contracts import (
    CreateMcpServerRequest,
    McpAuthCallbackRequest,
    McpAuthStartRequest,
    McpServerHealth,
    McpAuthState,
    OAuthTokenRequest,
    UpdateMcpServerRequest,
)
from backend_app.mcp_oauth import McpAuthorization
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore
from backend_app.token_vault import LocalTokenVault, TokenVaultFactory


class FakeOAuthTokenExchanger:
    def exchange_code(self, **kwargs) -> OAuthTokenRequest:
        return OAuthTokenRequest(
            access_token=f"access-token-for-{kwargs['code']}",
            refresh_token=f"refresh-token-for-{kwargs['code']}",
        )


class FakeOAuthClient:
    def authorization(self, **kwargs) -> McpAuthorization:
        return McpAuthorization(
            auth_url=(
                "https://auth.example.com/authorize?"
                f"state={kwargs['state']}&code_challenge={kwargs['code_challenge']}"
            ),
            discovery={
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "oauth_client": {"client_id": "client_123"},
            },
            required_scopes=("mcp",),
        )

    def refresh_token(self, **kwargs) -> OAuthTokenRequest:
        return OAuthTokenRequest(access_token="refreshed-access-token")


def test_mcp_registration_skip_and_internal_cards() -> None:
    service = McpRegistryService(store=InMemoryMcpStore())
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com",
            display_name="Drive MCP",
        )
    )

    assert created.auth_state == McpAuthState.UNAUTHENTICATED

    skipped = service.skip_auth(
        org_id="org_123",
        user_id="user_123",
        server_id=created.server_id,
    )
    cards = service.list_internal_cards(org_id="org_123", user_id="user_123")

    assert skipped.auth_state == McpAuthState.AUTH_SKIPPED
    assert cards.servers[0].server_id == created.server_id
    assert cards.servers[0].auth_state == McpAuthState.AUTH_SKIPPED


def test_mcp_disable_hides_server_from_internal_cards_and_can_reenable() -> None:
    service = McpRegistryService(store=InMemoryMcpStore())
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com",
            display_name="Drive MCP",
        )
    )

    disabled = service.update_server(
        org_id="org_123",
        user_id="user_123",
        server_id=created.server_id,
        request=UpdateMcpServerRequest(enabled=False),
    )
    cards_while_disabled = service.list_internal_cards(
        org_id="org_123", user_id="user_123"
    )
    enabled = service.update_server(
        org_id="org_123",
        user_id="user_123",
        server_id=created.server_id,
        request=UpdateMcpServerRequest(enabled=True),
    )
    cards_after_reenable = service.list_internal_cards(
        org_id="org_123", user_id="user_123"
    )

    assert disabled.enabled is False
    assert disabled.health == McpServerHealth.DISABLED
    assert cards_while_disabled.servers == ()
    assert enabled.enabled is True
    assert enabled.health == McpServerHealth.HEALTHY
    assert cards_after_reenable.servers[0].server_id == created.server_id


def test_oauth_flow_stores_encrypted_tokens_without_plaintext() -> None:
    store = InMemoryMcpStore()
    vault = LocalTokenVault(secret="unit-test-secret-value-at-least-32-chars")
    service = McpRegistryService(
        store=store,
        token_vault=vault,
        token_exchanger=FakeOAuthTokenExchanger(),
        oauth_client=FakeOAuthClient(),
    )
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com",
            display_name="Drive MCP",
        )
    )

    auth = service.start_auth(
        server_id=created.server_id,
        request=McpAuthStartRequest(
            org_id="org_123",
            user_id="user_123",
            redirect_uri="http://localhost:5173/mcp/oauth/callback",
        ),
    )
    state = next(iter(store.auth_sessions.keys()))
    completed = service.complete_auth(
        McpAuthCallbackRequest(state=state, code="oauth_code")
    )
    token = store.get_token(server_id=created.server_id)

    assert "state=" in auth.auth_url
    assert completed.auth_state == McpAuthState.AUTHENTICATED
    assert token is not None
    assert "oauth_code" not in token.encrypted_access_token
    assert vault.decrypt(token.encrypted_access_token) == "access-token-for-oauth_code"
    assert token.encrypted_refresh_token is not None
    assert (
        vault.decrypt(token.encrypted_refresh_token) == "refresh-token-for-oauth_code"
    )


def test_oauth_start_uses_random_pkce_verifiers() -> None:
    store = InMemoryMcpStore()
    service = McpRegistryService(store=store, oauth_client=FakeOAuthClient())
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com",
            display_name="Drive MCP",
        )
    )

    service.start_auth(
        server_id=created.server_id,
        request=McpAuthStartRequest(
            org_id="org_123",
            user_id="user_123",
            redirect_uri="http://localhost:5173/mcp/oauth/callback",
        ),
    )
    first_verifier = next(iter(store.auth_sessions.values())).code_verifier
    service.start_auth(
        server_id=created.server_id,
        request=McpAuthStartRequest(
            org_id="org_123",
            user_id="user_123",
            redirect_uri="http://localhost:5173/mcp/oauth/callback",
        ),
    )
    verifiers = {session.code_verifier for session in store.auth_sessions.values()}

    assert len(verifiers) == 2
    assert first_verifier in verifiers


def test_oauth_start_caches_discovery_and_required_scopes() -> None:
    store = InMemoryMcpStore()
    service = McpRegistryService(store=store, oauth_client=FakeOAuthClient())
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com",
            display_name="Drive MCP",
        )
    )

    auth = service.start_auth(
        server_id=created.server_id,
        request=McpAuthStartRequest(
            org_id="org_123",
            user_id="user_123",
            redirect_uri="http://localhost:5173/mcp/oauth/callback",
        ),
    )
    record = store.get_server(org_id="org_123", server_id=created.server_id)

    assert "code_challenge=" in auth.auth_url
    assert record is not None
    assert record.required_scopes == ("mcp",)
    assert record.last_discovery["authorization_endpoint"] == (
        "https://auth.example.com/authorize"
    )


def test_url_validation_rejects_localhost() -> None:
    service = McpRegistryService(store=InMemoryMcpStore())

    try:
        service.create_server(
            CreateMcpServerRequest(
                org_id="org_123",
                user_id="user_123",
                url="http://localhost:9000/mcp",
            )
        )
    except ValueError as exc:
        assert "https" in str(exc) or "localhost" in str(exc)
    else:
        raise AssertionError("localhost MCP URL should be rejected")


def test_production_refuses_in_memory_mcp_registry(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")

    try:
        McpRegistryService()
    except RuntimeError as exc:
        assert "persistent MCP registry" in str(exc)
    else:
        raise AssertionError("production must not default to in-memory MCP registry")


def test_managed_token_vault_fails_closed_when_adapter_missing(monkeypatch) -> None:
    monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
    monkeypatch.setenv("MCP_TOKEN_VAULT_PROVIDER", "managed")

    try:
        TokenVaultFactory.create()
    except RuntimeError as exc:
        assert "Managed MCP token vault adapter" in str(exc)
    else:
        raise AssertionError("managed token vault must fail closed without adapter")
