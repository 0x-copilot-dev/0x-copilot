"""AC9 desktop MCP OAuth coordinator — flow against a fake OAuth/MCP server.

Proves the DoD invariants: the coordinator completes OAuth against a fake
provider; the redirect URI is reconstructed (never client-supplied); the
callback caller must own the session; and provider tokens land only in the
vault, never in any coordinator response (secret canary).
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from backend_app.connectors.oauth_coordinator import (
    DesktopMcpOAuthCoordinator,
    DesktopOAuthCallback,
    DesktopOAuthError,
)
from backend_app.connectors.profile_catalog import (
    ConnectorReleaseStage,
    DesktopConnectorProfile,
    DesktopProfileCatalog,
)
from backend_app.contracts import (
    McpAuthState,
    McpOAuthClientRequest,
    OAuthTokenRequest,
)
from backend_app.mcp_oauth import (
    McpAuthorization,
    McpOAuthError,
    RemoteMcpOAuthClient,
    Values,
)
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


def _tenant_scoped_profile() -> DesktopConnectorProfile:
    """A profile whose endpoint needs a tenant id the app cannot supply."""

    return DesktopConnectorProfile(
        profile_id="synthetic-tenanted",
        connector_slug="tenanted",
        display_name="Tenanted",
        description="Needs a tenant admin.",
        server_id="desktop:synthetic:tenanted",
        display_group="Synthetic",
        endpoint_template="https://example.invalid/tenants/{tenantId}/mcp",
        transport="http",
        release_stage=ConnectorReleaseStage.STABLE,
        requires_preview_gate=False,
        verified_at=date(2026, 7, 18),
        # The loader requires this of any profile-owned seed.
        requires_pre_registered_client=True,
        requires_admin_setup=True,
        callback_modes=("loopback_pkce",),
    )


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
        """A profile whose endpoint carries an unresolved `{placeholder}` — or
        that declares `requires_admin_setup` — must fail closed even with
        preview on.

        This used to be exercised by the shipped `outlook` profile (Microsoft
        Work IQ), which was removed: it needs an M365 Copilot licence and a
        tenant admin's Entra app registration, so no personal account could
        ever complete it. The RULE outlives the data, so it is now pinned with
        a synthetic profile instead of being deleted alongside its last user.
        """

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
            catalog=DesktopProfileCatalog((_tenant_scoped_profile(),)),
            preview_enabled=True,
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="tenanted",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        assert excinfo.value.code == "connector_admin_setup_required"

    def test_arbitrary_redirect_port_rejected(self) -> None:
        # Pydantic wraps the field validator's DesktopOAuthError at construction.
        with pytest.raises(ValidationError):
            DesktopOAuthCallback(kind="desktop_loopback", port=80)


class _SetupRequiredOAuthClient:
    """``authorization`` raises SETUP_REQUIRED — no configured OAuth client."""

    def authorization(
        self, *, record, redirect_uri, state, code_challenge, token_vault
    ) -> McpAuthorization:
        raise McpOAuthError(Values.OAuth.SETUP_REQUIRED)

    def refresh_token(self, *, record, refresh_token, token_vault) -> OAuthTokenRequest:
        raise AssertionError("refresh not expected in this test")


class TestOAuthClientNotConfigured:
    """A connector whose MCP server has no OAuth client must not 500."""

    def test_start_maps_setup_required_to_stable_desktop_error(self) -> None:
        store = InMemoryMcpStore()
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=_SetupRequiredOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service, catalog=DesktopProfileCatalog.load()
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="atlassian",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        # Stable code → the route maps it to 409 (never a raw 500).
        #
        # Atlassian is `requires_pre_registered_client` and no client was
        # supplied or stored, so the failure is the SPECIFIC one the user can
        # fix by pasting a client_id — not the generic "setup is broken". Both
        # still map to 409; the distinction exists so the client can render a
        # form instead of a dead end.
        assert excinfo.value.code == "connector_oauth_client_required"

    def test_start_maps_generic_setup_failure_when_a_client_is_supplied(
        self,
    ) -> None:
        """With a client in hand, a discovery failure is NOT a missing client.

        Guards the other side of the branch: supplying a client must not make
        every downstream OAuth failure masquerade as "needs a client_id", which
        would loop the user through the form forever.
        """

        store = InMemoryMcpStore()
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=_SetupRequiredOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service, catalog=DesktopProfileCatalog.load()
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="atlassian",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
                oauth_client=McpOAuthClientRequest(
                    client_id="atlassian-client", client_secret="shhh"
                ),
            )
        assert excinfo.value.code == "connector_oauth_setup_required"

    def test_supplied_client_is_persisted_encrypted_not_in_the_clear(
        self,
    ) -> None:
        """The secret reaches the vault, never the record or any response."""

        store = InMemoryMcpStore()
        vault = LocalTokenVault(secret="x" * 40)
        service = McpRegistryService(
            store=store,
            token_vault=vault,
            oauth_client=FakeOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service, catalog=DesktopProfileCatalog.load()
        )
        coordinator.start(
            slug="atlassian",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
            oauth_client=McpOAuthClientRequest(
                client_id="atlassian-client", client_secret="CLIENT-SECRET-CANARY"
            ),
        )
        record = store.get_server(org_id=Fixture.ORG, server_id="seed:atlassian")
        assert record is not None
        assert record.oauth_client is not None
        assert record.oauth_client.client_id == "atlassian-client"
        assert "CLIENT-SECRET-CANARY" not in repr(record.oauth_client)
        assert record.oauth_client.encrypted_client_secret is not None
        assert (
            vault.decrypt(record.oauth_client.encrypted_client_secret)
            == "CLIENT-SECRET-CANARY"
        )

    def test_a_stored_client_survives_a_later_connect_without_one(self) -> None:
        """Re-connecting must not demand the client again."""

        store = InMemoryMcpStore()
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=_SetupRequiredOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service, catalog=DesktopProfileCatalog.load()
        )
        with pytest.raises(DesktopOAuthError):
            coordinator.start(
                slug="atlassian",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
                oauth_client=McpOAuthClientRequest(client_id="atlassian-client"),
            )
        # Second attempt supplies nothing; the stored client must still count.
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="atlassian",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        assert excinfo.value.code == "connector_oauth_setup_required"


class TestDeploymentOwnedOAuthClient:
    """The app owns the OAuth client; the user only consents.

    This is how every consumer product does Google/Microsoft integration —
    Claude included: ONE app registration, and each user grants it access on
    the vendor's own consent screen. Asking each user to register their own
    OAuth app is a developer workflow, and it was the actual reason Connect
    could not succeed for a normal account.
    """

    @staticmethod
    def _coordinator(store: InMemoryMcpStore, vault: LocalTokenVault):
        service = McpRegistryService(
            store=store,
            token_vault=vault,
            oauth_client=FakeOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        return DesktopMcpOAuthCoordinator(
            mcp_service=service,
            catalog=DesktopProfileCatalog.load(),
            preview_enabled=True,
        )

    def test_gmail_uses_the_deployment_client_with_no_user_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "app-owned-client.apps.google")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "APP-SECRET-CANARY")
        store, vault = InMemoryMcpStore(), LocalTokenVault(secret="x" * 40)
        coordinator = self._coordinator(store, vault)

        # No `oauth_client` argument: the user pastes nothing.
        coordinator.start(
            slug="gmail",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )

        record = store.get_server(org_id=Fixture.ORG, server_id="desktop:google:gmail")
        assert record is not None
        assert record.oauth_client is not None
        assert record.oauth_client.client_id == "app-owned-client.apps.google"
        # The deployment secret lands in the vault, never in the clear.
        assert "APP-SECRET-CANARY" not in repr(record.oauth_client)
        assert (
            vault.decrypt(record.oauth_client.encrypted_client_secret or "")
            == "APP-SECRET-CANARY"
        )

    def test_a_secretless_deployment_client_is_public_pkce_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A desktop app is a PUBLIC client — PKCE, no secret."""

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "app-owned-client.apps.google")
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        store, vault = InMemoryMcpStore(), LocalTokenVault(secret="x" * 40)
        coordinator = self._coordinator(store, vault)

        coordinator.start(
            slug="gmail",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
        )
        record = store.get_server(org_id=Fixture.ORG, server_id="desktop:google:gmail")
        assert record is not None
        assert record.oauth_client is not None
        assert record.oauth_client.encrypted_client_secret is None
        assert record.oauth_client.token_endpoint_auth_method == "none"

    def test_a_user_supplied_client_still_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Self-hosters replacing the shipped client must not be overridden."""

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "app-owned-client.apps.google")
        store, vault = InMemoryMcpStore(), LocalTokenVault(secret="x" * 40)
        coordinator = self._coordinator(store, vault)

        coordinator.start(
            slug="gmail",
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
            oauth_client=McpOAuthClientRequest(client_id="my-own-client"),
        )
        record = store.get_server(org_id=Fixture.ORG, server_id="desktop:google:gmail")
        assert record is not None
        assert record.oauth_client is not None
        assert record.oauth_client.client_id == "my-own-client"

    def test_atlassian_has_no_deployment_client_so_still_asks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider the app ships no client for must still prompt.

        Guards against the env fallback silently applying a Google client to an
        unrelated provider.
        """

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "app-owned-client.apps.google")
        store = InMemoryMcpStore()
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=_SetupRequiredOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service, catalog=DesktopProfileCatalog.load()
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="atlassian",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        assert excinfo.value.code == "connector_oauth_client_required"

    def test_no_env_client_falls_back_to_asking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment that ships no Google client degrades, never breaks."""

        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        store = InMemoryMcpStore()
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=_SetupRequiredOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=service,
            catalog=DesktopProfileCatalog.load(),
            preview_enabled=True,
        )
        with pytest.raises(DesktopOAuthError) as excinfo:
            coordinator.start(
                slug="gmail",
                org_id=Fixture.ORG,
                user_id=Fixture.USER,
                callback=Fixture.loopback(),
            )
        assert excinfo.value.code == "connector_oauth_client_required"


class _NoNetworkOAuthClient(RemoteMcpOAuthClient):
    """The real OAuth client, with the network removed.

    A known provider must be fully described by its profile — endpoints, client,
    scopes, extra params — so authorization needs no metadata fetch. Any attempt
    to reach out is the failure this class exists to catch.
    """

    def _fetch_first_json(self, urls):  # type: ignore[no-untyped-def]
        raise AssertionError(f"network discovery attempted: {list(urls)}")


class TestKnownProviderNeedsNoDiscovery:
    """Google connects over plain OAuth against the app's own client.

    This is the shape the product needs and the shape Claude uses: ONE app
    registration, and the user grants it access on Google's own consent screen.
    Sign-in stays identity-only (`openid email profile`) — the connector scopes
    are a SEPARATE, larger request made when the user actually connects, which
    is why `prompt=consent` is set rather than silently widening a login grant.
    """

    @staticmethod
    def _coordinator(store: InMemoryMcpStore) -> DesktopMcpOAuthCoordinator:
        service = McpRegistryService(
            store=store,
            token_vault=LocalTokenVault(secret="x" * 40),
            oauth_client=_NoNetworkOAuthClient(),
            token_exchanger=FakeExchanger(),
            auth_session_ttl=timedelta(minutes=5),
        )
        return DesktopMcpOAuthCoordinator(
            mcp_service=service,
            catalog=DesktopProfileCatalog.load(),
            preview_enabled=True,
        )

    @staticmethod
    def _query(url: str) -> dict[str, list[str]]:
        return parse_qs(urlsplit(url).query)

    def _start(self, monkeypatch, slug: str, scope: str = "read"):
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "123.apps.googleusercontent.com")
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        store = InMemoryMcpStore()
        result = self._coordinator(store).start(
            slug=slug,
            org_id=Fixture.ORG,
            user_id=Fixture.USER,
            callback=Fixture.loopback(),
            requested_product_scope=scope,  # type: ignore[arg-type]
        )
        return result, store

    def test_authorizes_against_google_with_the_app_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, _store = self._start(monkeypatch, "gmail")
        assert result.authorization_url.startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"
        )
        query = self._query(result.authorization_url)
        assert query["client_id"] == ["123.apps.googleusercontent.com"]
        assert query["code_challenge_method"] == ["S256"]

    def test_requests_only_the_connector_scopes_never_the_login_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Login asks for identity; connect asks for the mailbox. Neither
        should ever be quietly folded into the other's consent screen."""

        result, _store = self._start(monkeypatch, "gmail")
        scopes = self._query(result.authorization_url)["scope"][0].split()
        assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]
        assert not ({"openid", "email", "profile"} & set(scopes))

    def test_the_product_scope_ladder_widens_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, _store = self._start(monkeypatch, "gmail", scope="draft")
        scopes = self._query(result.authorization_url)["scope"][0].split()
        assert scopes == [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ]

    def test_asks_for_a_refresh_token_and_authorizes_incrementally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`access_type=offline` or the connector silently dies in an hour;
        `include_granted_scopes=true` or connecting Drive narrows Gmail."""

        result, _store = self._start(monkeypatch, "gdrive")
        query = self._query(result.authorization_url)
        assert query["access_type"] == ["offline"]
        assert query["include_granted_scopes"] == ["true"]
        assert query["prompt"] == ["consent"]

    def test_provider_params_cannot_override_the_security_parameters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config supplies extras; it must never reach state/PKCE/redirect."""

        result, _store = self._start(monkeypatch, "gmail")
        query = self._query(result.authorization_url)
        assert query["redirect_uri"] == ["http://127.0.0.1:53123/connectors/oauth/cb"]
        assert query["response_type"] == ["code"]
        assert len(query["state"][0]) >= 32
        assert query["code_challenge"][0]
