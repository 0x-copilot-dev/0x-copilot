from __future__ import annotations

from backend_app.contracts import (
    CreateMcpServerRequest,
    McpAuthCallbackRequest,
    McpAuthStartRequest,
    McpServerHealth,
    McpAuthState,
    UpdateMcpServerRequest,
)
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore
from backend_app.token_vault import LocalTokenVault


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
    cards_while_disabled = service.list_internal_cards(org_id="org_123", user_id="user_123")
    enabled = service.update_server(
        org_id="org_123",
        user_id="user_123",
        server_id=created.server_id,
        request=UpdateMcpServerRequest(enabled=True),
    )
    cards_after_reenable = service.list_internal_cards(org_id="org_123", user_id="user_123")

    assert disabled.enabled is False
    assert disabled.health == McpServerHealth.DISABLED
    assert cards_while_disabled.servers == ()
    assert enabled.enabled is True
    assert enabled.health == McpServerHealth.HEALTHY
    assert cards_after_reenable.servers[0].server_id == created.server_id


def test_oauth_flow_stores_encrypted_tokens_without_plaintext() -> None:
    store = InMemoryMcpStore()
    vault = LocalTokenVault(secret="unit-test-secret")
    service = McpRegistryService(store=store, token_vault=vault)
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
    completed = service.complete_auth(McpAuthCallbackRequest(state=state, code="oauth_code"))
    token = store.get_token(server_id=created.server_id)

    assert "state=" in auth.auth_url
    assert completed.auth_state == McpAuthState.AUTHENTICATED
    assert token is not None
    assert "oauth_code" not in token.encrypted_access_token
    assert vault.decrypt(token.encrypted_access_token) == "access:oauth_code"


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
