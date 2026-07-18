"""AC9 desktop MCP OAuth coordinator — flow against a fake OAuth/MCP server.

Proves the DoD invariants: the coordinator completes OAuth against a fake
provider; the redirect URI is reconstructed (never client-supplied); the
callback caller must own the session; and provider tokens land only in the
vault, never in any coordinator response (secret canary).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend_app.connectors.oauth_coordinator import (
    DesktopMcpOAuthCoordinator,
    DesktopOAuthCallback,
    DesktopOAuthError,
)
from backend_app.connectors.profile_catalog import DesktopProfileCatalog
from backend_app.contracts import McpAuthState, OAuthTokenRequest
from backend_app.mcp_oauth import McpAuthorization
from backend_app.service import McpRegistryService
from backend_app.store import InMemoryMcpStore
from backend_app.token_vault import LocalTokenVault

# The provider access token a successful exchange yields. It must only ever
# exist as vault ciphertext + inside the backend — never in a renderer-facing
# response object.
_TOKEN_CANARY = "provider-access-token-CANARY-2f9a"


class FakeOAuthClient:
    """Stands in for :class:`RemoteMcpOAuthClient` — no network."""

    def authorization(
        self, *, record, redirect_uri, state, code_challenge, token_vault
    ) -> McpAuthorization:
        discovery = {
            "authorization_endpoint": "https://fake-idp.example.com/authorize",
            "token_endpoint": "https://fake-idp.example.com/token",
            "oauth_client": {"client_id": "fake-client"},
        }
        return McpAuthorization(
            auth_url=(
                "https://fake-idp.example.com/authorize"
                f"?state={state}&redirect_uri={redirect_uri}"
                f"&code_challenge={code_challenge}"
            ),
            discovery=discovery,
            required_scopes=("read:jira-work",),
        )

    def refresh_token(self, *, record, refresh_token, token_vault) -> OAuthTokenRequest:
        raise AssertionError("refresh not expected in this test")


class FakeExchanger:
    def exchange_code(self, *, record, session, code, token_vault) -> OAuthTokenRequest:
        assert code == "auth-code-123"
        return OAuthTokenRequest(
            access_token=_TOKEN_CANARY,
            refresh_token=None,
            token_type="Bearer",
            expires_at=None,
        )


class Fixture:
    ORG = "org_acme"
    USER = "user_sarah"

    @classmethod
    def build(cls, *, preview_enabled: bool = False):
        store = InMemoryMcpStore()
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=FakeOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service,
            catalog=DesktopProfileCatalog.load(),
            preview_enabled=preview_enabled,
        )
        return store, service, coordinator

    @staticmethod
    def loopback() -> DesktopOAuthCallback:
        return DesktopOAuthCallback(kind="desktop_loopback", port=53123)


class TestHappyPath:
    def test_completes_oauth_against_fake_server(self) -> None:
        store, service, coordinator = Fixture.build()

        start = coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )
        assert start.authorization_url.startswith("https://fake-idp.example.com")
        assert start.requested_permissions == ("read:jira-work",)

        result = coordinator.complete(
            oauth_session_id=start.oauth_session_id,
            state=start.state,
            caller_org_id=Fixture.ORG,
            caller_user_id=Fixture.USER,
            code="auth-code-123",
        )

        assert result.server_id == "seed:atlassian"
        assert result.connector_slug == "atlassian"
        assert result.auth_state is McpAuthState.AUTHENTICATED

    def test_redirect_uri_is_reconstructed_not_client_supplied(self) -> None:
        _store, _service, coordinator = Fixture.build()
        start = coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )
        # The fake echoes the redirect_uri into the auth URL; assert the backend
        # built the fixed loopback path/host, not something arbitrary.
        assert "http://127.0.0.1:53123/connectors/oauth/cb" in (start.authorization_url)


class TestSecretCanary:
    def test_token_only_in_vault_never_in_response(self) -> None:
        store, service, coordinator = Fixture.build()
        start = coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )
        result = coordinator.complete(
            oauth_session_id=start.oauth_session_id,
            state=start.state,
            caller_org_id=Fixture.ORG,
            caller_user_id=Fixture.USER,
            code="auth-code-123",
        )

        # Renderer-facing responses carry no token.
        assert _TOKEN_CANARY not in str(start.model_dump())
        assert _TOKEN_CANARY not in str(result.model_dump())

        # The token exists — but only as vault ciphertext keyed by server.
        envelope = store.get_token(server_id="seed:atlassian")
        assert envelope is not None
        assert _TOKEN_CANARY not in envelope.encrypted_access_token
        assert (
            service.token_vault.decrypt(envelope.encrypted_access_token)
            == _TOKEN_CANARY
        )


class TestOwnerMatchAndFailClosed:
    def test_wrong_caller_identity_fails_closed(self) -> None:
        _store, _service, coordinator = Fixture.build()
        start = coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )

        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.complete(
                oauth_session_id=start.oauth_session_id,
                state=start.state,
                caller_org_id="org_evil",
                caller_user_id="user_mallory",
                code="auth-code-123",
            )
        assert excinfo.value.code == "connector_oauth_state_invalid"

        # Session was dropped: a subsequent legitimate attempt cannot replay it.
        with pytest.raises(DesktopOAuthError):
            coordinator.complete(
                oauth_session_id=start.oauth_session_id,
                state=start.state,
                caller_org_id=Fixture.ORG,
                caller_user_id=Fixture.USER,
                code="auth-code-123",
            )

    def test_session_id_state_mismatch_rejected(self) -> None:
        _store, _service, coordinator = Fixture.build()
        start = coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.complete(
                oauth_session_id=start.oauth_session_id,
                state="tampered-state",
                caller_org_id=Fixture.ORG,
                caller_user_id=Fixture.USER,
                code="auth-code-123",
            )
        assert excinfo.value.code == "connector_oauth_state_invalid"

    def test_user_denial_maps_to_denied(self) -> None:
        _store, _service, coordinator = Fixture.build()
        start = coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.complete(
                oauth_session_id=start.oauth_session_id,
                state=start.state,
                caller_org_id=Fixture.ORG,
                caller_user_id=Fixture.USER,
                error="access_denied",
            )
        assert excinfo.value.code == "connector_oauth_denied"


class TestPreviewAndSetupGates:
    def test_preview_connector_disabled_by_default(self) -> None:
        _store, _service, coordinator = Fixture.build(preview_enabled=False)
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="gmail",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        assert excinfo.value.code == "connector_preview_disabled"

    def test_tenant_template_requires_admin_setup(self) -> None:
        _store, _service, coordinator = Fixture.build(preview_enabled=True)
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="outlook",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        assert excinfo.value.code == "connector_admin_setup_required"

    def test_arbitrary_redirect_port_rejected(self) -> None:
        # Pydantic wraps the field validator's DesktopOAuthError at construction.
        with pytest.raises(ValidationError):
            DesktopOAuthCallback(kind="desktop_loopback", port=80)
