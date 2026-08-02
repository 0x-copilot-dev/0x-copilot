"""``POST /internal/v1/mcp/servers/{id}/access-token`` — the scoped-token mint.

The endpoint exists so a runtime that opens an MCP server *itself* never has to
hold the durable credential. That only buys anything if five claims are true, so
each one is pinned here rather than asserted in prose:

* **the caller is who it says it is** — with ``ENTERPRISE_SERVICE_TOKEN``
  configured, the query-string org/user is ignored in favour of the verified
  service headers, and a caller without them is refused;
* **the access-mode gate holds** — an ``off`` connector is refused with the same
  403 + reason code the RPC proxy answers with, *before* the vault decrypts;
* **the bearer cannot exceed the connector's mode** — a ``read`` connector
  yields no token at all, because a token is a write capability this service
  cannot take back and cannot narrow;
* **the refresh token never leaves** — not in the body, not in a field, not
  anywhere in the raw response text, even after a refresh has just rotated it;
* **the bearer is narrow** — an expiry is always stated, never later than the
  stored credential's own, and never further out than the issue ceiling.

The service is exercised directly where the assertion is about *ordering*
(nothing decrypted before the gate ran), and over HTTP where the assertion is
about the wire (status codes, the response body, header identity).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.connectors.store import ConnectorAccessMode
from backend_app.contracts import (
    InternalMcpAccessToken,
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerRecord,
    McpStdioConfig,
    McpTransport,
    OAuthTokenRequest,
    TokenEnvelope,
)
from backend_app.service import (
    ConnectorAccessDenied,
    InternalMcpLeaseFailureError,
    McpAccessTokenUnavailable,
    McpRegistryService,
)
from backend_app.store import InMemoryMcpStore

ORG = "org_acme"
USER = "usr_sarah"
OTHER_USER = "usr_marcus"
SERVICE_TOKEN = "service-token"
ACCESS_SECRET = "upstream-access-token-value"
REFRESH_SECRET = "upstream-refresh-token-value"
ROTATED_ACCESS = "rotated-access-token-value"
ROTATED_REFRESH = "rotated-refresh-token-value"
ENDPOINT = "https://mcp.example.com/mcp"
SCOPES = ("issues:read", "issues:write")


class CountingVault:
    """Identity vault that counts decryptions — 'nothing decrypted yet' is testable."""

    def __init__(self) -> None:
        self.decrypt_calls = 0

    def encrypt(self, plaintext: str) -> str:
        return f"enc:{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        self.decrypt_calls += 1
        return ciphertext.removeprefix("enc:")

    def key_id_for(self, ciphertext: str) -> str:
        return "test-key"


class RotatingExchanger:
    """Token exchanger whose refresh rotates BOTH halves of the credential.

    Rotating the refresh token too is the point: it proves the mint returns the
    freshly refreshed *access* token while the equally fresh *refresh* token
    stays behind.
    """

    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh_token(self, **_kwargs: object) -> OAuthTokenRequest:
        self.refresh_calls += 1
        return OAuthTokenRequest(
            access_token=ROTATED_ACCESS,
            refresh_token=ROTATED_REFRESH,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


class MintFixtureMixin:
    """Builders shared by every concrete test class in this module."""

    def _record(
        self,
        *,
        user_id: str = USER,
        auth_mode: McpAuthMode = McpAuthMode.OAUTH2,
        transport: McpTransport = McpTransport.HTTP,
        url: str | None = ENDPOINT,
        stdio: McpStdioConfig | None = None,
        health: McpServerHealth = McpServerHealth.HEALTHY,
        enabled: bool = True,
    ) -> McpServerRecord:
        return McpServerRecord(
            org_id=ORG,
            user_id=user_id,
            name="linear",
            display_name="Linear",
            url=url,
            stdio=stdio,
            transport=transport,
            auth_mode=auth_mode,
            auth_state=McpAuthState.AUTHENTICATED,
            health=health,
            enabled=enabled,
            required_scopes=SCOPES,
        )

    def _service(
        self,
        *,
        record: McpServerRecord | None = None,
        access_mode: ConnectorAccessMode | None = None,
        seed_token: bool = True,
        expires_at: datetime | None = None,
        refresh: bool = True,
        exchanger: RotatingExchanger | None = None,
        store: InMemoryMcpStore | None = None,
    ) -> tuple[McpRegistryService, McpServerRecord, CountingVault]:
        store = InMemoryMcpStore() if store is None else store
        vault = CountingVault()
        service = McpRegistryService(
            store=store,
            token_vault=vault,
            token_exchanger=RotatingExchanger() if exchanger is None else exchanger,
        )
        stored = self._record() if record is None else record
        store.create_server(stored)
        if seed_token:
            store.put_token(
                TokenEnvelope(
                    server_id=stored.server_id,
                    org_id=ORG,
                    user_id=stored.user_id,
                    encrypted_access_token=f"enc:{ACCESS_SECRET}",
                    encrypted_refresh_token=(
                        f"enc:{REFRESH_SECRET}" if refresh else None
                    ),
                    expires_at=expires_at,
                )
            )
        service.connector_access_resolver = lambda _record: access_mode
        return service, stored, vault

    def _mint(
        self, service: McpRegistryService, record: McpServerRecord
    ) -> InternalMcpAccessToken:
        return service.mint_internal_access_token(
            org_id=ORG, user_id=record.user_id, server_id=record.server_id
        )

    def _client(self, service: McpRegistryService) -> TestClient:
        """Compose the app, keeping the access mode this test configured.

        ``create_app`` rewires ``connector_access_resolver`` to the composed
        connectors store (PRD-06 D3), which in a fixture with no connector rows
        answers ``None`` for everything — including the server a test just set
        to ``off``. Reinstating the fixture's resolver after composition is what
        lets the route be asked the question the service-level tests already
        answer, without standing up a second store to say one word.
        """

        resolver = service.connector_access_resolver
        client = TestClient(create_app(service))
        service.connector_access_resolver = resolver
        return client

    def _headers(
        self, *, token: str | None = SERVICE_TOKEN, org: str = ORG, user: str = USER
    ) -> dict[str, str]:
        headers = {ORG_HEADER: org, USER_HEADER: user}
        if token is not None:
            headers[SERVICE_TOKEN_HEADER] = token
        return headers

    def _post(
        self,
        client: TestClient,
        record: McpServerRecord,
        *,
        headers: dict[str, str] | None = None,
        org: str = ORG,
        user: str = USER,
    ):
        return client.post(
            f"/internal/v1/mcp/servers/{record.server_id}/access-token",
            params={"org_id": org, "user_id": user},
            headers=headers,
        )


class TestMintAdmission(MintFixtureMixin):
    """Who is allowed to mint, and what happens in what order."""

    def test_mints_the_endpoint_transport_token_expiry_and_scopes(self) -> None:
        service, record, _vault = self._service()
        minted = self._mint(service, record)
        assert minted.url == ENDPOINT
        assert minted.transport is McpTransport.HTTP
        assert minted.access_token == ACCESS_SECRET
        assert minted.scopes == SCOPES
        assert minted.expires_at is not None

    def test_off_connector_is_denied_before_anything_is_decrypted(self) -> None:
        # The ordering IS the control: an `off` connector must not cause a
        # plaintext credential to exist in this process at all, even briefly.
        service, record, vault = self._service(access_mode=ConnectorAccessMode.OFF)
        with pytest.raises(ConnectorAccessDenied) as exc:
            self._mint(service, record)
        assert exc.value.reason == ConnectorAccessDenied.OFF
        assert vault.decrypt_calls == 0

    def test_a_read_only_connector_yields_no_token_at_all(self) -> None:
        # The write gate, and the only form it can take here. The proxy refuses
        # a `read` connector's writes per call, because it sees each call. A
        # minted bearer names no call: it is the provider's own credential, and
        # from the moment it leaves this process every write and every DELETE
        # made with it is invisible, unclassifiable, and un-revocable until the
        # TTL runs out. So "the token must not be usable for writes" can only
        # be enforced as "the token must not exist" — asserted as the typed
        # refusal, with the same reason code the proxy denies a write with, and
        # with nothing decrypted on the way to it.
        service, record, vault = self._service(access_mode=ConnectorAccessMode.READ)
        with pytest.raises(ConnectorAccessDenied) as exc:
            self._mint(service, record)
        assert exc.value.reason == ConnectorAccessDenied.READ_ONLY
        assert vault.decrypt_calls == 0

    def test_a_resolver_answering_with_the_raw_column_string_still_denies(self) -> None:
        # `ConnectorAccessMode` is a `StrEnum` and the resolver port is only
        # *declared* to return it. A gate written with `is` rather than `==`
        # mints here — fail-open, on the one comparison that must not be. This
        # is the test that catches that edit.
        service, record, vault = self._service()
        service.connector_access_resolver = lambda _record: "read"
        with pytest.raises(ConnectorAccessDenied) as exc:
            self._mint(service, record)
        assert exc.value.reason == ConnectorAccessDenied.READ_ONLY
        assert vault.decrypt_calls == 0

    @pytest.mark.parametrize(
        ("access_mode", "stated"),
        [(ConnectorAccessMode.READ_ACT, "read_act"), (None, None)],
    )
    def test_only_read_act_and_an_unjoined_connector_mint(
        self, access_mode, stated
    ) -> None:
        # The other half of the table, and the two are exhaustive over the
        # enum. `None` (no connector row joins the server) is not a user-set
        # mode: it gates nothing, exactly as in `proxy_internal_rpc`, and it is
        # reported as `None` rather than folded into `read` so the caller is
        # told nothing was configured instead of guessing at a silence.
        service, record, _vault = self._service(access_mode=access_mode)
        minted = self._mint(service, record)
        assert minted.access_token == ACCESS_SECRET
        assert minted.access_mode == stated

    def test_the_minted_mode_is_never_one_the_gate_refuses(self) -> None:
        # Structural: whatever a future edit does to the gate, the mode the
        # response *states* can never be one that should have stopped the mint.
        # This is the assertion that fails if `read` is ever quietly re-allowed
        # by handing back an honest `access_mode` instead of a refusal.
        for access_mode in (*ConnectorAccessMode, None):
            service, record, _vault = self._service(access_mode=access_mode)
            try:
                minted = self._mint(service, record)
            except ConnectorAccessDenied:
                continue
            assert minted.access_mode not in {
                ConnectorAccessMode.OFF.value,
                ConnectorAccessMode.READ.value,
            }

    def test_another_users_server_is_not_found(self) -> None:
        # Tenant isolation: the org matches, the user does not. The caller must
        # not even learn that the server exists.
        service, record, vault = self._service(record=self._record(user_id=OTHER_USER))
        with pytest.raises(ValueError) as exc:
            service.mint_internal_access_token(
                org_id=ORG, user_id=USER, server_id=record.server_id
            )
        assert not isinstance(exc.value, InternalMcpLeaseFailureError)
        assert vault.decrypt_calls == 0

    def test_a_server_with_no_credential_is_a_typed_conflict(self) -> None:
        service, record, _vault = self._service(
            record=self._record(auth_mode=McpAuthMode.NONE), seed_token=False
        )
        with pytest.raises(McpAccessTokenUnavailable) as exc:
            self._mint(service, record)
        assert exc.value.reason == McpAccessTokenUnavailable.NO_CREDENTIAL

    def test_a_stdio_server_is_a_typed_conflict_and_never_decrypts(self) -> None:
        service, record, vault = self._service(
            record=self._record(
                transport=McpTransport.STDIO,
                url=None,
                stdio=McpStdioConfig(command="npx", args=("-y", "some-server")),
            )
        )
        with pytest.raises(McpAccessTokenUnavailable) as exc:
            self._mint(service, record)
        assert exc.value.reason == McpAccessTokenUnavailable.NO_ENDPOINT
        assert vault.decrypt_calls == 0

    def test_an_unhealthy_server_raises_the_shared_lease_failure(self) -> None:
        service, record, vault = self._service(
            record=self._record(health=McpServerHealth.UNAVAILABLE)
        )
        with pytest.raises(InternalMcpLeaseFailureError) as exc:
            self._mint(service, record)
        assert exc.value.failure.code.value == "server_unavailable"
        assert vault.decrypt_calls == 0

    def test_a_server_with_no_stored_token_requires_auth(self) -> None:
        service, record, _vault = self._service(seed_token=False)
        with pytest.raises(InternalMcpLeaseFailureError) as exc:
            self._mint(service, record)
        assert exc.value.failure.code.value == "auth_required"

    def test_the_mint_is_audited_with_the_authority_it_was_issued_under(self) -> None:
        # "A credential was released" is half the question an auditor asks; the
        # other half is what it was allowed to do. Recording the mode on the
        # row means answering it later does not depend on reconstructing a
        # connector's state as it was minutes ago.
        store = InMemoryMcpStore()
        service, record, _vault = self._service(
            store=store, access_mode=ConnectorAccessMode.READ_ACT
        )
        self._mint(service, record)
        minted_events = [
            event
            for event in store.audit_events
            if event.action == "mcp_access_token_minted"
        ]
        assert len(minted_events) == 1
        assert minted_events[0].metadata["access_mode"] == "read_act"

    def test_a_refused_mint_writes_no_credential_release_row(self) -> None:
        # The audit row means a credential left the vault. A refusal did not
        # release one, so writing the row anyway would make the release log
        # unusable as evidence of what was actually handed out.
        store = InMemoryMcpStore()
        service, record, _vault = self._service(
            store=store, access_mode=ConnectorAccessMode.READ
        )
        with pytest.raises(ConnectorAccessDenied):
            self._mint(service, record)
        assert "mcp_access_token_minted" not in {
            event.action for event in store.audit_events
        }


class TestRefreshAndExpiry(MintFixtureMixin):
    """The same ``_require_valid_token`` path the proxy uses, and the TTL cap."""

    def test_an_expiring_token_is_refreshed_and_the_fresh_one_is_returned(self) -> None:
        exchanger = RotatingExchanger()
        service, record, _vault = self._service(
            expires_at=datetime.now(UTC) + timedelta(seconds=5), exchanger=exchanger
        )
        minted = self._mint(service, record)
        assert exchanger.refresh_calls == 1
        assert minted.access_token == ROTATED_ACCESS

    def test_a_refreshless_expiring_token_requires_auth(self) -> None:
        service, record, _vault = self._service(
            expires_at=datetime.now(UTC) + timedelta(seconds=5), refresh=False
        )
        with pytest.raises(InternalMcpLeaseFailureError) as exc:
            self._mint(service, record)
        assert exc.value.failure.code.value == "auth_required"

    def test_a_never_expiring_credential_still_gets_a_ceiling(self) -> None:
        service, record, _vault = self._service(expires_at=None)
        minted = self._mint(service, record)
        assert minted.expires_at <= datetime.now(UTC) + service.ACCESS_TOKEN_MAX_TTL
        assert minted.expires_at > datetime.now(UTC)

    def test_a_long_lived_credential_is_capped_at_the_ceiling(self) -> None:
        service, record, _vault = self._service(
            expires_at=datetime.now(UTC) + timedelta(days=30)
        )
        assert (
            self._mint(service, record).expires_at
            <= datetime.now(UTC) + service.ACCESS_TOKEN_MAX_TTL
        )

    def test_a_short_lived_credential_is_never_promised_longer_than_it_lives(
        self,
    ) -> None:
        # The cap must not become a floor: promising 5 minutes on a credential
        # that dies in 90 seconds would guarantee a window of 401s.
        stored_expiry = datetime.now(UTC) + timedelta(seconds=90)
        service, record, _vault = self._service(expires_at=stored_expiry)
        assert self._mint(service, record).expires_at == stored_expiry


class TestMintRoute(MintFixtureMixin):
    """The wire: status codes, the response body, and header identity."""

    @pytest.fixture(autouse=True)
    def _service_token(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", SERVICE_TOKEN)

    def test_returns_exactly_the_six_documented_fields(self) -> None:
        service, record, _vault = self._service(
            access_mode=ConnectorAccessMode.READ_ACT
        )
        body = self._post(self._client(service), record, headers=self._headers()).json()
        assert set(body) == {
            "url",
            "transport",
            "access_token",
            "expires_at",
            "scopes",
            "access_mode",
        }
        assert body["access_token"] == ACCESS_SECRET
        assert body["transport"] == "http"
        assert body["scopes"] == list(SCOPES)
        assert body["expires_at"]
        assert body["access_mode"] == "read_act"

    def test_the_refresh_token_is_nowhere_in_the_response(self) -> None:
        service, record, _vault = self._service()
        response = self._post(self._client(service), record, headers=self._headers())
        assert REFRESH_SECRET not in response.text
        assert "refresh" not in json.dumps(response.json())

    def test_a_rotated_refresh_token_still_never_reaches_the_caller(self) -> None:
        # The refresh just minted a brand-new refresh token. It is the freshest
        # secret in the vault and it must still not cross the boundary.
        service, record, _vault = self._service(
            expires_at=datetime.now(UTC) + timedelta(seconds=5)
        )
        response = self._post(self._client(service), record, headers=self._headers())
        assert response.json()["access_token"] == ROTATED_ACCESS
        assert ROTATED_REFRESH not in response.text
        assert REFRESH_SECRET not in response.text

    def test_the_contract_has_no_field_a_refresh_token_could_ride(self) -> None:
        # Structural, not conventional: `extra="forbid"` plus this field set is
        # what stops a later edit from adding one by accident.
        assert set(InternalMcpAccessToken.model_fields) == {
            "url",
            "transport",
            "access_token",
            "expires_at",
            "scopes",
            "access_mode",
        }
        with pytest.raises(ValueError):
            InternalMcpAccessToken(
                url=ENDPOINT,
                transport=McpTransport.HTTP,
                access_token=ACCESS_SECRET,
                expires_at=datetime.now(UTC),
                refresh_token=REFRESH_SECRET,
            )

    def test_the_token_does_not_render_into_a_repr(self) -> None:
        # `repr` is the shape that reaches tracebacks and log lines.
        minted = InternalMcpAccessToken(
            url=ENDPOINT,
            transport=McpTransport.HTTP,
            access_token=ACCESS_SECRET,
            expires_at=datetime.now(UTC),
        )
        assert ACCESS_SECRET not in repr(minted)
        assert minted.model_dump()["access_token"] == ACCESS_SECRET

    def test_a_missing_service_token_is_rejected(self) -> None:
        service, record, vault = self._service()
        response = self._post(
            self._client(service), record, headers=self._headers(token=None)
        )
        assert response.status_code == 401
        assert vault.decrypt_calls == 0

    def test_a_wrong_service_token_is_rejected(self) -> None:
        service, record, vault = self._service()
        response = self._post(
            self._client(service), record, headers=self._headers(token="not-the-token")
        )
        assert response.status_code == 401
        assert vault.decrypt_calls == 0

    def test_a_missing_identity_header_is_rejected(self) -> None:
        service, record, _vault = self._service()
        response = self._post(
            self._client(service),
            record,
            headers={SERVICE_TOKEN_HEADER: SERVICE_TOKEN, ORG_HEADER: ORG},
        )
        assert response.status_code == 401

    def test_the_verified_headers_outrank_the_query_string(self) -> None:
        # A caller that forges org/user in the query string mints for the tenant
        # its service headers were vouched for — which here owns no such server.
        service, record, _vault = self._service()
        response = self._post(
            self._client(service),
            record,
            headers=self._headers(user=OTHER_USER),
            user=USER,
        )
        assert response.status_code == 404
        assert ACCESS_SECRET not in response.text

    @pytest.mark.parametrize(
        ("access_mode", "reason"),
        [
            (ConnectorAccessMode.OFF, ConnectorAccessDenied.OFF),
            (ConnectorAccessMode.READ, ConnectorAccessDenied.READ_ONLY),
        ],
    )
    def test_a_restricted_connector_answers_403_with_the_shared_reason_code(
        self, access_mode, reason
    ) -> None:
        # Both refusals reach the wire as the RPC proxy's own vocabulary, so a
        # caller that already handles a denied connector in one topology needs
        # no second word for it in the other.
        service, record, vault = self._service(access_mode=access_mode)
        response = self._post(self._client(service), record, headers=self._headers())
        assert response.status_code == 403
        assert response.json()["detail"] == reason
        assert ACCESS_SECRET not in response.text
        assert vault.decrypt_calls == 0

    def test_a_read_only_connector_hands_the_caller_nothing_to_write_with(self) -> None:
        # The end-to-end form of the gap this route used to have: a service
        # token plus a connector the user set to read-only produced a bearer
        # good for writes and DELETEs straight at the provider, for the full
        # TTL, with no way to withdraw it. The response must carry no bearer at
        # all — not the stored one, not a rotated one.
        service, record, _vault = self._service(access_mode=ConnectorAccessMode.READ)
        response = self._post(self._client(service), record, headers=self._headers())
        assert response.status_code == 403
        assert "access_token" not in response.json()
        assert ACCESS_SECRET not in response.text
        assert ROTATED_ACCESS not in response.text

    @pytest.mark.parametrize(
        ("record_kwargs", "seed_token", "reason"),
        [
            (
                {"auth_mode": McpAuthMode.NONE},
                False,
                McpAccessTokenUnavailable.NO_CREDENTIAL,
            ),
            (
                {"transport": McpTransport.STDIO, "url": None},
                True,
                McpAccessTokenUnavailable.NO_ENDPOINT,
            ),
        ],
    )
    def test_a_server_that_admits_no_bearer_answers_409(
        self, record_kwargs, seed_token, reason
    ) -> None:
        stdio = (
            McpStdioConfig(command="npx")
            if record_kwargs.get("transport") is McpTransport.STDIO
            else None
        )
        service, record, _vault = self._service(
            record=self._record(stdio=stdio, **record_kwargs), seed_token=seed_token
        )
        response = self._post(self._client(service), record, headers=self._headers())
        assert response.status_code == 409
        assert response.json()["detail"] == reason

    def test_an_unknown_server_answers_404_without_leaking(self) -> None:
        service, _record, _vault = self._service()
        response = self._client(service).post(
            "/internal/v1/mcp/servers/does_not_exist/access-token",
            params={"org_id": ORG, "user_id": USER},
            headers=self._headers(),
        )
        assert response.status_code == 404
        assert ACCESS_SECRET not in response.text
