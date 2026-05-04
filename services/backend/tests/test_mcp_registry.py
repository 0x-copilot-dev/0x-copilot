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
from backend_app.mcp_oauth import McpAuthorization, RemoteMcpOAuthClient
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


class FakeRemoteMcpOAuthClient(RemoteMcpOAuthClient):
    def __init__(
        self,
        *,
        get_json,
        post_json=None,
        post_form=None,
    ) -> None:
        super().__init__()
        self.get_json = get_json
        self.post_json = post_json
        self.post_form = post_form

    def _get_json(self, url: str) -> dict[str, object]:
        return self.get_json(url)

    def _post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        if self.post_json is None:
            return {}
        return self.post_json(url, payload)

    def _post_form(self, url: str, body: dict[str, str]) -> dict[str, object]:
        if self.post_form is None:
            return {"access_token": "access-token"}
        return self.post_form(url, body)


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


def test_oauth_discovery_uses_dynamic_client_registration() -> None:
    captured: dict[str, object] = {}
    vault = LocalTokenVault(secret="unit-test-secret-value-at-least-32-chars")

    def fake_get_json(url: str) -> dict[str, object]:
        if "oauth-protected-resource" in url:
            return {
                "resource": "https://mcp.example.com/mcp",
                "authorization_servers": ["https://auth.example.com"],
                "scopes_required": ["tasks:read"],
            }
        if "oauth-authorization-server" in url:
            return {
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "registration_endpoint": "https://auth.example.com/register",
            }
        return {}

    def fake_post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
        captured["registration_url"] = url
        captured["registration_payload"] = payload
        return {
            "client_id": "registered_client",
            "client_secret": "registered_secret",
            "token_endpoint_auth_method": "client_secret_post",
            "redirect_uris": payload["redirect_uris"],
        }

    oauth_client = FakeRemoteMcpOAuthClient(
        get_json=fake_get_json,
        post_json=fake_post_json,
    )
    service = McpRegistryService(
        store=InMemoryMcpStore(),
        token_vault=vault,
        oauth_client=oauth_client,
    )
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com/mcp",
            display_name="Tasks MCP",
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
    record = service.store.get_server(org_id="org_123", server_id=created.server_id)

    assert captured["registration_url"] == "https://auth.example.com/register"
    assert "client_id=registered_client" in auth.auth_url
    assert "scope=tasks%3Aread" in auth.auth_url
    assert "/mcp/oauth/authorize" not in auth.auth_url
    assert record is not None
    encrypted_secret = record.last_discovery["oauth_client"]["encrypted_client_secret"]
    assert encrypted_secret != "registered_secret"
    assert vault.decrypt(encrypted_secret) == "registered_secret"


def test_oauth_flow_uses_per_server_configured_client() -> None:
    captured: dict[str, object] = {}
    vault = LocalTokenVault(secret="unit-test-secret-value-at-least-32-chars")

    def fake_post_form(url: str, body: dict[str, str]) -> dict[str, object]:
        captured["token_url"] = url
        captured["token_body"] = body
        return {
            "access_token": "configured-access-token",
            "refresh_token": "configured-refresh-token",
        }

    oauth_client = FakeRemoteMcpOAuthClient(
        get_json=lambda url: {},
        post_form=fake_post_form,
    )
    service = McpRegistryService(
        store=InMemoryMcpStore(),
        token_vault=vault,
        token_exchanger=oauth_client,
        oauth_client=oauth_client,
    )
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com/v2/mcp",
            display_name="Generic MCP",
            oauth_client={
                "client_id": "configured_client",
                "client_secret": "configured_secret",
                "scope": "mcp tasks:read",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
            },
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
    state = next(iter(service.store.auth_sessions.keys()))
    completed = service.complete_auth(
        McpAuthCallbackRequest(state=state, code="oauth_code")
    )

    assert auth.auth_url.startswith("https://auth.example.com/authorize?")
    assert "client_id=configured_client" in auth.auth_url
    assert "scope=mcp+tasks%3Aread" in auth.auth_url
    assert completed.auth_state == McpAuthState.AUTHENTICATED
    assert captured["token_url"] == "https://auth.example.com/token"
    assert captured["token_body"]["client_secret"] == "configured_secret"
    assert "configured_secret" not in str(
        service.store.get_server(org_id="org_123", server_id=created.server_id)
    )


def test_oauth_start_fails_safely_without_metadata_or_client_config() -> None:
    store = InMemoryMcpStore()
    service = McpRegistryService(
        store=store,
        oauth_client=FakeRemoteMcpOAuthClient(get_json=lambda url: {}),
    )
    created = service.create_server(
        CreateMcpServerRequest(
            org_id="org_123",
            user_id="user_123",
            url="https://mcp.example.com/v2/mcp",
            display_name="Generic MCP",
        )
    )

    try:
        service.start_auth(
            server_id=created.server_id,
            request=McpAuthStartRequest(
                org_id="org_123",
                user_id="user_123",
                redirect_uri="http://localhost:5173/mcp/oauth/callback",
            ),
        )
    except ValueError as exc:
        assert "MCP OAuth setup requires" in str(exc)
    else:
        raise AssertionError("OAuth start should require metadata or client config")

    record = store.get_server(org_id="org_123", server_id=created.server_id)
    assert record is not None
    assert record.auth_state == McpAuthState.UNAUTHENTICATED
    assert store.auth_sessions == {}


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
    """Legacy ``MCP_TOKEN_VAULT_PROVIDER=managed`` is rejected now that
    C6 ships a real adapter framework; operators must move to
    ``MCP_TOKEN_VAULT_BACKEND=aws_kms`` (or an explicit local for dev).
    """

    monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
    monkeypatch.delenv("MCP_TOKEN_VAULT_BACKEND", raising=False)
    monkeypatch.setenv("MCP_TOKEN_VAULT_PROVIDER", "managed")

    try:
        TokenVaultFactory.create()
    except RuntimeError as exc:
        assert "MCP_TOKEN_VAULT_PROVIDER=managed is no longer accepted" in str(exc)
    else:
        raise AssertionError("legacy managed provider must fail closed")
