"""Unit tests for the P2-7a desktop credential plane.

The provider is additive and unwired, so these tests pin the properties P2-8 will
depend on when it injects it — and, more importantly, the ones a leak would make
invisible:

* one object answers **both** seams (``CredentialProvider.auth_for`` and
  ``McpConnectionDirectory.connection_for``) off the same broker contract;
* the bearer has exactly **one** route to the wire — the ``httpx.Auth`` — and
  never appears in the connection headers, a ``repr``, a ``str``, a model dump,
  or a log line, even when the broker itself echoes it back inside a failure;
* every broker outcome becomes a **typed** ``McpClientError`` with an authored
  safe message: an unknown connector is a not-found, a refused workspace gate is
  an auth failure, an unreachable broker is a connection failure, and anything
  else is an unreadable-credential protocol failure.

The broker is a fake behind the ``DesktopMcpSecretBroker`` Protocol (no loopback
server, no keychain, no network). Where the bearer's journey to the wire matters,
the test drives httpx's **real** auth machinery through an ``httpx.MockTransport``
rather than re-implementing the flow.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerError,
    BrokerGrantRequiredError,
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
    BrokerProtocolError,
    BrokerUnavailableError,
    McpSecretResult,
)
from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpNotFoundError,
    McpRequestRejectedError,
)
from agent_runtime.capabilities.mcp.connection import (
    McpServerConnectionConfig,
    MintedToken,
)
from agent_runtime.capabilities.mcp.credentials.desktop import (
    DesktopCredentialMessages,
    DesktopKeychainCredentialProvider,
    DesktopMcpBrokerUnavailableError,
    DesktopMcpConnectorNotFoundError,
    DesktopMcpCredentialDeniedError,
    DesktopMcpSecretReader,
    DesktopMcpSecretUnreadableError,
    DesktopSecretExpiry,
    DesktopSecretTransport,
)
from agent_runtime.capabilities.mcp.credentials.refreshing_auth import (
    RefreshingBearerAuth,
)

_SERVER_ID = "srv_linear_01HXYZ"
_OTHER_SERVER_ID = "srv_notion_01HABC"
_SECRET = "lin_oauth_desktop-keychain-secret-value"
_ROTATED_SECRET = "lin_oauth_desktop-keychain-rotated-value"
_URL = "https://mcp.linear.app/mcp"
_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_LIFETIME = timedelta(hours=1)
_EXPIRES_AT = _NOW + _LIFETIME
#: The builder's default expiry. A module constant rather than an ``is None``
#: default so a test can ask for a genuinely absent ``expires_at``.
_EXPIRES_AT_ISO = _EXPIRES_AT.isoformat()


class FakeSecretBroker:
    """A scripted ``/mcp/secret`` broker: one answer per call, or a failure.

    Records every ``server_id`` it was asked for, so "one read per need" and
    "the fetch cannot be pointed at another connector" are both observable.
    """

    def __init__(
        self,
        results: Sequence[McpSecretResult] = (),
        *,
        error: BaseException | None = None,
    ) -> None:
        self._results = list(results)
        self._error = error
        self.requested: list[str] = []

    async def mcp_secret(self, server_id: str) -> McpSecretResult:
        self.requested.append(server_id)
        if self._error is not None:
            raise self._error
        if not self._results:
            raise AssertionError("the broker was read more times than it was scripted")
        return self._results[0] if len(self._results) == 1 else self._results.pop(0)

    @property
    def reads(self) -> int:
        return len(self.requested)


class FakeClock:
    """A movable aware-UTC clock — expiry is proven without sleeping."""

    def __init__(self, now: datetime = _NOW) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeMcpEndpoint:
    """Replays scripted statuses and records every bearer it was sent."""

    def __init__(self, *statuses: int) -> None:
        self._statuses = list(statuses) or [httpx.codes.OK]
        self.seen_headers: list[httpx.Headers] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.seen_headers.append(request.headers)
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return httpx.Response(status)

    def client(self, auth: httpx.Auth) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), auth=auth)

    @property
    def seen_bearers(self) -> list[str]:
        return [headers.get("Authorization", "") for headers in self.seen_headers]


class DesktopProviderFixtureMixin:
    """Builders shared by the concrete test classes."""

    def _secret(
        self,
        *,
        token: str = _SECRET,
        url: str = _URL,
        transport: str = "http",
        expires_at: str | int | float | None = _EXPIRES_AT_ISO,
        headers: dict[str, str] | None = None,
    ) -> McpSecretResult:
        return McpSecretResult(
            url=url,
            transport=transport,
            token=SecretStr(token),
            expires_at=expires_at,
            headers={} if headers is None else headers,
        )

    def _broker(self, *secrets: McpSecretResult) -> FakeSecretBroker:
        return FakeSecretBroker(secrets or (self._secret(),))

    def _provider(
        self,
        broker: FakeSecretBroker | None = None,
        *,
        clock: FakeClock | None = None,
        skew: timedelta | None = None,
    ) -> DesktopKeychainCredentialProvider:
        return DesktopKeychainCredentialProvider(
            broker=self._broker() if broker is None else broker,
            clock=FakeClock() if clock is None else clock,
            skew=skew,
        )

    def _failing_provider(
        self, error: BaseException
    ) -> DesktopKeychainCredentialProvider:
        return DesktopKeychainCredentialProvider(
            broker=FakeSecretBroker(error=error), clock=FakeClock()
        )

    async def _mint(
        self, broker: FakeSecretBroker, *, clock: FakeClock | None = None
    ) -> MintedToken:
        """Run the bound reader — exactly what the auth calls on first use.

        Driving the reader rather than reaching into the auth keeps this on the
        public seam; that the provider hands the auth *this* reader is proven
        separately by the wire tests.
        """

        reader = DesktopMcpSecretReader(
            broker=broker,
            server_id=_SERVER_ID,
            clock=FakeClock() if clock is None else clock,
        )
        return await reader()

    def _bearer(self, secret: str) -> str:
        return f"Bearer {secret}"


class TestConnectionDirectory(DesktopProviderFixtureMixin):
    async def test_connection_for_returns_the_endpoint_half(self) -> None:
        broker = self._broker(self._secret(url=_URL, transport="http"))
        config = await self._provider(broker).connection_for(_SERVER_ID)
        assert isinstance(config, McpServerConnectionConfig)
        assert config.url == _URL
        assert config.transport is McpTransport.HTTP
        assert broker.requested == [_SERVER_ID]

    async def test_the_bearer_never_rides_the_connection_headers(self) -> None:
        # The whole point of the split: a config that is logged, cached, or
        # rendered must be incapable of carrying the credential.
        broker = self._broker(self._secret())
        config = await self._provider(broker).connection_for(_SERVER_ID)
        assert config.headers == {}
        assert _SECRET not in str(config.model_dump())
        assert _SECRET not in repr(config)

    async def test_a_broker_supplied_authorization_header_is_stripped(self) -> None:
        # Defence in depth: even if the broker (or a future route change) puts a
        # bearer in the non-secret header bag, it must not reach the connection.
        broker = self._broker(
            self._secret(
                headers={
                    "Authorization": f"Bearer {_SECRET}",
                    "Cookie": f"session={_SECRET}",
                    "X-Linear-Workspace": "acme",
                }
            )
        )
        config = await self._provider(broker).connection_for(_SERVER_ID)
        assert config.headers == {"X-Linear-Workspace": "acme"}
        assert _SECRET not in str(config.headers)

    async def test_non_secret_headers_are_preserved(self) -> None:
        broker = self._broker(self._secret(headers={"X-Api-Version": "2026-07-28"}))
        config = await self._provider(broker).connection_for(_SERVER_ID)
        assert config.headers == {"X-Api-Version": "2026-07-28"}

    async def test_sse_and_stdio_transports_resolve(self) -> None:
        # stdio is reported, not refused: P2-3's connection builder owns the
        # decision that a local command spec cannot be opened yet (P2-PLAN §6.4).
        for raw, expected in (("sse", McpTransport.SSE), ("stdio", McpTransport.STDIO)):
            broker = self._broker(self._secret(transport=raw))
            config = await self._provider(broker).connection_for(_SERVER_ID)
            assert config.transport is expected

    async def test_the_streamable_http_alias_resolves_to_http(self) -> None:
        broker = self._broker(self._secret(transport="Streamable_HTTP"))
        config = await self._provider(broker).connection_for(_SERVER_ID)
        assert config.transport is McpTransport.HTTP

    async def test_an_unknown_transport_is_a_typed_safe_failure(self) -> None:
        broker = self._broker(self._secret(transport="carrier-pigeon"))
        with pytest.raises(DesktopMcpSecretUnreadableError) as caught:
            await self._provider(broker).connection_for(_SERVER_ID)
        assert isinstance(caught.value, McpClientError)
        assert str(caught.value) == DesktopCredentialMessages.UNSUPPORTED_TRANSPORT

    async def test_each_call_is_one_broker_read(self) -> None:
        broker = self._broker(self._secret())
        provider = self._provider(broker)
        await provider.connection_for(_SERVER_ID)
        await provider.connection_for(_SERVER_ID)
        assert broker.reads == 2


class TestCredentialProvider(DesktopProviderFixtureMixin):
    async def test_auth_for_returns_a_refreshing_bearer_auth(self) -> None:
        auth = await self._provider().auth_for(_SERVER_ID)
        assert isinstance(auth, RefreshingBearerAuth)
        assert isinstance(auth, httpx.Auth)

    async def test_auth_for_reads_no_credential_at_construction(self) -> None:
        # Building an auth for a connector the run never calls must cost zero
        # keychain reads.
        broker = self._broker(self._secret())
        await self._provider(broker).auth_for(_SERVER_ID)
        assert broker.reads == 0

    async def test_the_bearer_reaches_the_wire_only_through_the_auth(self) -> None:
        broker = self._broker(self._secret())
        auth = await self._provider(broker).auth_for(_SERVER_ID)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            response = await client.get(_URL)
        assert response.status_code == httpx.codes.OK
        assert endpoint.seen_bearers == [self._bearer(_SECRET)]
        assert broker.reads == 1

    async def test_the_fetch_is_bound_to_one_connector(self) -> None:
        # The auth calls its fetch with no arguments, so it cannot be pointed at
        # a connector it was not minted for.
        broker = self._broker(self._secret())
        provider = self._provider(broker)
        auth = await provider.auth_for(_OTHER_SERVER_ID)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            await client.get(_URL)
        assert broker.requested == [_OTHER_SERVER_ID]

    async def test_the_token_is_cached_then_refreshed_at_expiry(self) -> None:
        broker = FakeSecretBroker(
            [
                self._secret(token=_SECRET),
                self._secret(
                    token=_ROTATED_SECRET,
                    expires_at=(_NOW + 2 * _LIFETIME).isoformat(),
                ),
            ]
        )
        clock = FakeClock()
        auth = await self._provider(broker, clock=clock).auth_for(_SERVER_ID)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            await client.get(_URL)
            clock.advance(_LIFETIME - RefreshingBearerAuth.DEFAULT_SKEW)
            await client.get(_URL)
        assert broker.reads == 2
        assert endpoint.seen_bearers == [
            self._bearer(_SECRET),
            self._bearer(_ROTATED_SECRET),
        ]

    async def test_an_epoch_milliseconds_expiry_is_read_as_milliseconds(self) -> None:
        # Electron speaks `Date.now()` on this broker; reading that as seconds
        # would cache the bearer for centuries past its death.
        broker = self._broker(
            self._secret(expires_at=int(_EXPIRES_AT.timestamp() * 1000))
        )
        token = await self._mint(broker)
        assert token.expires_at == _EXPIRES_AT

    async def test_an_epoch_seconds_expiry_is_read_as_seconds(self) -> None:
        # An OAuth store commonly speaks seconds; reading that as milliseconds
        # would make every token born expired — a keychain-read storm.
        broker = self._broker(self._secret(expires_at=_EXPIRES_AT.timestamp()))
        token = await self._mint(broker)
        assert token.expires_at == _EXPIRES_AT

    async def test_a_naive_iso_expiry_is_read_as_utc(self) -> None:
        broker = self._broker(
            self._secret(expires_at=_EXPIRES_AT.replace(tzinfo=None).isoformat())
        )
        token = await self._mint(broker)
        assert token.expires_at == _EXPIRES_AT

    async def test_a_zulu_iso_expiry_is_accepted(self) -> None:
        zulu = f"{_EXPIRES_AT.replace(tzinfo=None).isoformat()}Z"
        broker = self._broker(self._secret(expires_at=zulu))
        token = await self._mint(broker)
        assert token.expires_at == _EXPIRES_AT

    async def test_a_missing_expiry_yields_a_short_lifetime(self) -> None:
        # Not an error — a non-expiring stored credential is legitimate — but it
        # is not trusted with an unbounded cache either.
        broker = self._broker(self._secret(expires_at=None))
        token = await self._mint(broker)
        assert token.expires_at == _NOW + DesktopSecretExpiry.UNKNOWN_TTL

    async def test_an_unparseable_expiry_is_a_typed_safe_failure(self) -> None:
        broker = self._broker(self._secret(expires_at="whenever"))
        with pytest.raises(DesktopMcpSecretUnreadableError):
            await self._mint(broker)

    def test_a_boolean_expiry_is_refused_at_the_boundary(self) -> None:
        # A lax coercion would read ``true`` as epoch 1 — an expiry in 1970,
        # which re-mints on every single request.
        with pytest.raises(ValidationError):
            self._secret(expires_at=True)  # type: ignore[arg-type]


class TestSecretSafety(DesktopProviderFixtureMixin):
    async def test_the_minted_token_masks_its_plaintext(self) -> None:
        token = await self._mint(self._broker(self._secret()))
        assert _SECRET not in repr(token)
        assert _SECRET not in str(token)
        assert _SECRET not in token.model_dump_json()

    async def test_the_broker_result_masks_its_plaintext(self) -> None:
        secret = self._secret()
        assert _SECRET not in repr(secret)
        assert _SECRET not in str(secret)
        assert _SECRET not in secret.model_dump_json()

    async def test_provider_and_reader_state_never_render_the_plaintext(self) -> None:
        broker = self._broker(self._secret())
        provider = self._provider(broker)
        await provider.connection_for(_SERVER_ID)
        auth = await provider.auth_for(_SERVER_ID)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            await client.get(_URL)
        assert _SECRET not in str(vars(provider))
        assert _SECRET not in str(vars(auth))
        assert _SECRET not in repr(auth)

    async def test_a_successful_read_logs_no_secret(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broker = self._broker(self._secret())
        with caplog.at_level(logging.DEBUG):
            await self._provider(broker).connection_for(_SERVER_ID)
            await self._mint(broker)
        assert _SECRET not in caplog.text

    async def test_a_leaky_upstream_failure_never_reaches_a_log_or_a_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A response-validation error can echo the offending value — which on
        # this route is the token. It must not be chained into a traceback and
        # must not be logged.
        leaky = RuntimeError(f"1 validation error: token={_SECRET}")
        provider = self._failing_provider(leaky)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(DesktopMcpSecretUnreadableError) as caught:
                await provider.connection_for(_SERVER_ID)
        assert _SECRET not in caplog.text
        assert _SECRET not in str(caught.value)
        assert caught.value.__cause__ is None
        assert str(caught.value) == DesktopCredentialMessages.UNREADABLE


class TestBrokerFailures(DesktopProviderFixtureMixin):
    async def test_an_unknown_connector_is_a_typed_not_found(self) -> None:
        provider = self._failing_provider(BrokerNotFoundError())
        with pytest.raises(DesktopMcpConnectorNotFoundError) as caught:
            await provider.connection_for(_SERVER_ID)
        # Lands on the existing taxonomy rung the P2-5 error map already reads.
        assert isinstance(caught.value, McpNotFoundError)
        assert isinstance(caught.value, McpRequestRejectedError)
        assert caught.value.status_code == 404
        assert str(caught.value) == DesktopCredentialMessages.NOT_CONNECTED
        assert _SERVER_ID not in str(caught.value)

    async def test_the_not_found_also_surfaces_through_the_auth(self) -> None:
        provider = self._failing_provider(BrokerNotFoundError())
        auth = await provider.auth_for(_SERVER_ID)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            with pytest.raises(DesktopMcpConnectorNotFoundError):
                await client.get(_URL)
        # Nothing was sent: an unauthenticated request must never go out.
        assert endpoint.seen_bearers == []

    async def test_a_refused_workspace_gate_is_a_typed_auth_failure(self) -> None:
        # The active-workspace gate lives in the main process; a run that asks
        # for another workspace's connector is refused there, and that refusal
        # must arrive as an actionable, non-retryable auth failure.
        for error in (BrokerPermissionDeniedError(), BrokerGrantRequiredError()):
            provider = self._failing_provider(error)
            with pytest.raises(DesktopMcpCredentialDeniedError) as caught:
                await provider.connection_for(_SERVER_ID)
            assert isinstance(caught.value, McpAuthError)
            assert str(caught.value) == DesktopCredentialMessages.ACCESS_REFUSED

    async def test_an_unreachable_broker_is_a_typed_connection_failure(self) -> None:
        provider = self._failing_provider(BrokerUnavailableError("connect refused"))
        with pytest.raises(DesktopMcpBrokerUnavailableError) as caught:
            await provider.connection_for(_SERVER_ID)
        assert isinstance(caught.value, McpConnectionError)
        assert str(caught.value) == DesktopCredentialMessages.UNAVAILABLE
        assert "connect refused" not in str(caught.value)

    async def test_any_other_broker_error_is_a_typed_protocol_failure(self) -> None:
        provider = self._failing_provider(
            BrokerProtocolError("capability broker returned malformed JSON")
        )
        with pytest.raises(DesktopMcpSecretUnreadableError) as caught:
            await provider.connection_for(_SERVER_ID)
        assert isinstance(caught.value, McpClientError)
        assert not isinstance(caught.value, McpAuthError)
        assert not isinstance(caught.value, McpConnectionError)
        assert str(caught.value) == DesktopCredentialMessages.UNREADABLE

    async def test_a_safe_broker_error_is_still_chained_for_logs(self) -> None:
        original = BrokerError("broker_error")
        provider = self._failing_provider(original)
        with pytest.raises(DesktopMcpSecretUnreadableError) as caught:
            await provider.connection_for(_SERVER_ID)
        assert caught.value.__cause__ is original

    async def test_the_failure_survives_the_auth_flow_untyped_by_the_auth(
        self,
    ) -> None:
        # ``RefreshingBearerAuth._mint`` re-raises an already-typed
        # ``McpClientError`` unchanged, so the desktop taxonomy reaches the P2-5
        # error map instead of being flattened into a generic fetch failure.
        provider = self._failing_provider(BrokerUnavailableError())
        auth = await provider.auth_for(_SERVER_ID)
        endpoint = FakeMcpEndpoint(httpx.codes.OK)
        async with endpoint.client(auth) as client:
            with pytest.raises(DesktopMcpBrokerUnavailableError):
                await client.get(_URL)


class TestTransportAndExpiryUnits(DesktopProviderFixtureMixin):
    def test_transport_resolution_is_case_and_whitespace_insensitive(self) -> None:
        assert DesktopSecretTransport.resolve("  HTTP  ") is McpTransport.HTTP

    def test_epoch_boundary_below_the_floor_is_seconds(self) -> None:
        below = DesktopSecretExpiry.MILLISECONDS_FLOOR - 1
        assert DesktopSecretExpiry.resolve(below, now=_NOW) == datetime.fromtimestamp(
            below, UTC
        )

    def test_epoch_boundary_at_the_floor_is_milliseconds(self) -> None:
        floor = DesktopSecretExpiry.MILLISECONDS_FLOOR
        assert DesktopSecretExpiry.resolve(floor, now=_NOW) == datetime.fromtimestamp(
            floor / 1000, UTC
        )

    def test_a_boolean_is_never_read_as_epoch_one(self) -> None:
        # The wire model already refuses it; the normalizer refuses it too, so a
        # direct caller cannot reintroduce a 1970 expiry.
        with pytest.raises(DesktopMcpSecretUnreadableError):
            DesktopSecretExpiry.resolve(True, now=_NOW)

    def test_an_out_of_range_epoch_is_a_typed_safe_failure(self) -> None:
        with pytest.raises(DesktopMcpSecretUnreadableError):
            DesktopSecretExpiry.resolve(1e30, now=_NOW)

    async def test_the_reader_is_the_minted_token_fetch(self) -> None:
        # It is handed to the auth as a zero-argument callable; that contract is
        # what makes the credential un-redirectable.
        reader = DesktopMcpSecretReader(
            broker=self._broker(self._secret()),
            server_id=_SERVER_ID,
            clock=FakeClock(),
        )
        token = await reader()
        assert isinstance(token, MintedToken)
        assert token.value.get_secret_value() == _SECRET


class TestEnvironmentGate(DesktopProviderFixtureMixin):
    def test_absent_broker_config_yields_no_provider(self) -> None:
        # Not a desktop image: P2-8 selects a different provider rather than
        # failing at the first tool call.
        assert DesktopKeychainCredentialProvider.from_env(env={}) is None

    def test_a_configured_broker_yields_a_provider_without_touching_it(self) -> None:
        provider = DesktopKeychainCredentialProvider.from_env(
            env={
                "DESKTOP_WORKSPACE_BROKER_URL": "http://127.0.0.1:51234",
                "DESKTOP_WORKSPACE_BROKER_TOKEN": "per-boot-secret",
            }
        )
        assert isinstance(provider, DesktopKeychainCredentialProvider)
        assert "per-boot-secret" not in str(vars(provider))


class TestBrokerWireContract(DesktopProviderFixtureMixin):
    """Pin the literal body the TypeScript broker emits.

    The two halves of this seam were written in parallel against a prose
    contract, and every other test in this file hands the provider an
    already-constructed :class:`McpSecretResult` — so the parse from a
    TS-shaped dict is never exercised. A field rename on either side, or the
    loss of ``populate_by_name`` in a future pydantic bump, would keep both
    suites green and surface only at the P2-8 flip, as a token that silently
    expires in five minutes. This is the golden body from
    ``apps/desktop/main/capabilities/broker.ts`` (``mcpSecretWire``).
    """

    GOLDEN_BODY: dict[str, object] = {
        "url": "https://mcp.linear.app/mcp",
        "transport": "http",
        "token": _SECRET,
        "expires_at": 1_900_000_000_000,
        "headers": {"linear-workspace": "acme"},
    }

    def test_the_typescript_body_parses_field_for_field(self) -> None:
        result = McpSecretResult.model_validate(self.GOLDEN_BODY)
        assert result.url == "https://mcp.linear.app/mcp"
        assert result.transport == "http"
        assert result.token.get_secret_value() == _SECRET
        assert result.expires_at == 1_900_000_000_000
        assert dict(result.headers) == {"linear-workspace": "acme"}

    def test_a_body_omitting_the_optional_fields_still_parses(self) -> None:
        # The route omits ``headers`` when the record has none and sends
        # ``expires_at: null`` when the store does not state one.
        result = McpSecretResult.model_validate(
            {
                "url": "https://mcp.linear.app/mcp",
                "transport": "http",
                "token": _SECRET,
                "expires_at": None,
            }
        )
        assert result.expires_at is None
        assert dict(result.headers) == {}

    def test_the_golden_body_never_renders_the_token(self) -> None:
        result = McpSecretResult.model_validate(self.GOLDEN_BODY)
        for rendered in (repr(result), str(result), result.model_dump_json()):
            assert _SECRET not in rendered
