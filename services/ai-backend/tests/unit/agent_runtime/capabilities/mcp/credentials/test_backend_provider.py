"""Unit tests for the P2-7b web / self-host credential plane.

The provider is additive and unwired, so these tests pin the properties P2-8 will
depend on when it injects it — and, more importantly, the ones a leak or a
mis-scope would make invisible:

* one object answers **both** seams (``CredentialProvider.auth_for`` and
  ``McpConnectionDirectory.connection_for``) off the same mint round-trip, and
  ``auth_for`` mints nothing until the auth is actually used;
* the request carries the service-token headers the backend's internal API
  requires, and the tenant identity travels on them;
* the bearer has exactly **one** route to the wire — the ``httpx.Auth`` — and
  never appears in the connection config, a ``repr``, a ``str``, a model dump, or
  a log line, even when the mint echoes it back;
* every mint outcome becomes a **typed** ``McpClientError`` with an authored safe
  message: 403 (the backend's access-mode gate) is an auth failure, 404 a
  not-found, 409 a rejected request that reconnecting will not fix, 5xx a
  retryable connection failure, and an unusable body a protocol error.

The backend is an ``httpx.MockTransport`` — a real ``httpx.AsyncClient`` all the
way down, so the request that would go on the wire is the request being asserted
on. Where the bearer's journey matters, httpx's own auth machinery drives it
rather than a re-implementation of the flow.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from pydantic import SecretStr

from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpNotFoundError,
    McpRequestRejectedError,
)
from agent_runtime.capabilities.mcp.connection import (
    McpConnectionDirectory,
    McpServerConnectionConfig,
    MintedToken,
)
from agent_runtime.capabilities.mcp.credentials.backend import (
    BackendCredentialMessages,
    BackendMcpConnectorNotFoundError,
    BackendMcpCredentialDeniedError,
    BackendMcpMintRejectedError,
    BackendMcpMintUnavailableError,
    BackendMcpMintUnreadableError,
    BackendMcpNoBearerError,
    BackendMintConfig,
    BackendMintedCredential,
    BackendScopedTokenCredentialProvider,
    BackendScopedTokenReader,
)
from agent_runtime.capabilities.mcp.credentials.refreshing_auth import (
    RefreshingBearerAuth,
)
from agent_runtime.capabilities.policy.contracts import CredentialProvider

_SERVER_ID = "srv_linear_01HXYZ"
_ORG_ID = "org_acme"
_USER_ID = "usr_sarah"
_SERVICE_TOKEN = "service-token-shared-with-the-backend"
_SECRET = "lin_oauth_backend-minted-secret-value"
_ROTATED_SECRET = "lin_oauth_backend-minted-rotated-value"
_URL = "https://mcp.linear.app/mcp"
_BASE_URL = "https://backend.internal:8100"
_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_LIFETIME = timedelta(minutes=5)
_EXPIRES_AT = _NOW + _LIFETIME
_SCOPES = ("issues:read", "issues:write")


class FakeMintEndpoint:
    """A scripted ``/access-token`` backend: one answer per call, or a status.

    Records every request it was sent, so "one mint per need", "the fetch cannot
    be pointed at another connector", and "the service headers were present" are
    all observable from the same fake.
    """

    def __init__(
        self,
        *bodies: dict[str, object],
        status: int = httpx.codes.OK,
        raw_body: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._bodies = list(bodies)
        self._status = status
        self._raw_body = raw_body
        self._error = error
        self.requests: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._raw_body is not None:
            return httpx.Response(self._status, text=self._raw_body)
        if self._status != httpx.codes.OK:
            return httpx.Response(self._status, json={"detail": "connector_access_off"})
        body = self._bodies[0] if len(self._bodies) == 1 else self._bodies.pop(0)
        return httpx.Response(self._status, json=body)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    @property
    def mints(self) -> int:
        return len(self.requests)

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


class FakeMcpEndpoint:
    """Replays scripted statuses and records every bearer it was sent.

    The header value is copied out at request time rather than the ``Headers``
    object being kept: on the 401 retry httpx re-yields the *same* ``Request``
    with its ``Authorization`` overwritten, so holding the object would make the
    first and second entries the same string and quietly hide whether the retry
    actually carried a different bearer.
    """

    def __init__(self, *statuses: int) -> None:
        self._statuses = list(statuses) or [httpx.codes.OK]
        self.seen_bearers: list[str] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.seen_bearers.append(request.headers.get("Authorization", ""))
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return httpx.Response(status)

    def client(self, auth: httpx.Auth) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), auth=auth)


class FakeClock:
    """A movable aware-UTC clock — expiry is proven without sleeping."""

    def __init__(self, now: datetime = _NOW) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class BackendProviderFixtureMixin:
    """Builders shared by the concrete test classes."""

    def _body(
        self,
        *,
        token: str = _SECRET,
        url: str = _URL,
        transport: str = "http",
        expires_at: str | None = None,
        scopes: Sequence[str] = _SCOPES,
        **extra: object,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "url": url,
            "transport": transport,
            "access_token": token,
            "expires_at": _EXPIRES_AT.isoformat() if expires_at is None else expires_at,
            "scopes": list(scopes),
        }
        body.update(extra)
        return body

    def _config(self) -> BackendMintConfig:
        return BackendMintConfig(
            base_url=_BASE_URL,
            org_id=_ORG_ID,
            user_id=_USER_ID,
            service_token=SecretStr(_SERVICE_TOKEN),
        )

    def _backend(
        self, *bodies: dict[str, object], **kwargs: object
    ) -> FakeMintEndpoint:
        return FakeMintEndpoint(*(bodies or (self._body(),)), **kwargs)  # type: ignore[arg-type]

    def _provider(
        self,
        backend: FakeMintEndpoint | None = None,
        *,
        clock: FakeClock | None = None,
        skew: timedelta | None = None,
    ) -> BackendScopedTokenCredentialProvider:
        endpoint = self._backend() if backend is None else backend
        return BackendScopedTokenCredentialProvider(
            config=self._config(),
            http_client=endpoint.client(),
            clock=FakeClock() if clock is None else clock,
            skew=skew,
        )

    def _failing_provider(
        self, *, status: int = httpx.codes.OK, **kwargs: object
    ) -> BackendScopedTokenCredentialProvider:
        return self._provider(self._backend(status=status, **kwargs))  # type: ignore[arg-type]

    async def _mint(self, backend: FakeMintEndpoint) -> MintedToken:
        """Run the bound reader — exactly what the auth calls on first use.

        Driving the reader rather than reaching into the auth keeps this on the
        public seam; that the provider hands the auth *this* reader is proven
        separately by the wire tests.
        """

        reader = BackendScopedTokenReader(
            config=self._config(),
            http_client=backend.client(),
            server_id=_SERVER_ID,
        )
        return await reader()

    def _bearer(self, secret: str) -> str:
        return f"Bearer {secret}"


class TestConnectionDirectory(BackendProviderFixtureMixin):
    async def test_connection_for_returns_the_endpoint_half(self) -> None:
        backend = self._backend(self._body(url=_URL, transport="http"))
        config = await self._provider(backend).connection_for(_SERVER_ID)
        assert isinstance(config, McpServerConnectionConfig)
        assert config.url == _URL
        assert config.transport is McpTransport.HTTP
        assert backend.mints == 1

    async def test_the_bearer_never_rides_the_connection(self) -> None:
        # The whole point of the split: a config that is logged, cached, or
        # rendered must be incapable of carrying the credential.
        config = await self._provider(self._backend()).connection_for(_SERVER_ID)
        assert config.headers == {}
        assert _SECRET not in str(config.model_dump())
        assert _SECRET not in repr(config)

    async def test_the_route_is_scoped_to_the_requested_connector(self) -> None:
        backend = self._backend()
        await self._provider(backend).connection_for(_SERVER_ID)
        assert backend.paths == [f"/internal/v1/mcp/servers/{_SERVER_ID}/access-token"]

    async def test_the_sse_transport_resolves(self) -> None:
        backend = self._backend(self._body(transport="sse"))
        config = await self._provider(backend).connection_for(_SERVER_ID)
        assert config.transport is McpTransport.SSE

    async def test_an_unknown_transport_is_a_typed_protocol_error(self) -> None:
        # No alias table on this lane: the producer is the backend's own
        # ``McpTransport``, so an unrecognised value means the two contracts
        # have drifted, and drift must fail rather than be guessed at.
        backend = self._backend(self._body(transport="carrier-pigeon"))
        with pytest.raises(BackendMcpMintUnreadableError):
            await self._provider(backend).connection_for(_SERVER_ID)


class TestServiceAuth(BackendProviderFixtureMixin):
    async def test_the_mint_carries_the_service_token_and_tenant_headers(self) -> None:
        backend = self._backend()
        await self._provider(backend).connection_for(_SERVER_ID)
        headers = backend.requests[0].headers
        assert headers[SERVICE_TOKEN_HEADER] == _SERVICE_TOKEN
        assert headers[ORG_HEADER] == _ORG_ID
        assert headers[USER_HEADER] == _USER_ID

    async def test_the_tenant_also_travels_in_the_query(self) -> None:
        # Belt and braces: the backend prefers the verified headers, but a dev
        # deployment with no service token configured reads the query scope.
        backend = self._backend()
        await self._provider(backend).connection_for(_SERVER_ID)
        query = backend.requests[0].url.params
        assert query["org_id"] == _ORG_ID
        assert query["user_id"] == _USER_ID

    def test_the_service_token_does_not_render(self) -> None:
        config = self._config()
        assert _SERVICE_TOKEN not in repr(config)
        assert _SERVICE_TOKEN not in config.model_dump_json()
        assert config.service_token.get_secret_value() == _SERVICE_TOKEN

    def test_neither_the_provider_nor_the_reader_renders_its_config(self) -> None:
        provider = self._provider()
        reader = BackendScopedTokenReader(
            config=self._config(),
            http_client=self._backend().client(),
            server_id=_SERVER_ID,
        )
        for rendered in (repr(provider), repr(reader)):
            assert _SERVICE_TOKEN not in rendered
            assert _ORG_ID not in rendered
            assert _SERVER_ID not in rendered


class TestCredentialProvider(BackendProviderFixtureMixin):
    async def test_auth_for_mints_nothing_until_the_auth_is_used(self) -> None:
        # Building an auth for a connector the run never calls must cost zero
        # mints — and therefore zero backend audit rows.
        backend = self._backend()
        auth = await self._provider(backend).auth_for(_SERVER_ID)
        assert isinstance(auth, RefreshingBearerAuth)
        assert backend.mints == 0

    async def test_the_bearer_reaches_the_wire_exactly_once_per_request(self) -> None:
        backend = self._backend()
        auth = await self._provider(backend).auth_for(_SERVER_ID)
        server = FakeMcpEndpoint()
        async with server.client(auth) as client:
            await client.get("https://mcp.linear.app/mcp")
        assert server.seen_bearers == [self._bearer(_SECRET)]
        assert backend.mints == 1

    async def test_a_cached_bearer_is_reused_without_a_second_mint(self) -> None:
        backend = self._backend()
        auth = await self._provider(backend).auth_for(_SERVER_ID)
        server = FakeMcpEndpoint()
        async with server.client(auth) as client:
            await client.get("https://mcp.linear.app/mcp")
            await client.get("https://mcp.linear.app/mcp")
        assert backend.mints == 1

    async def test_the_bearer_is_re_minted_once_the_cache_window_closes(self) -> None:
        # Re-minting is not just a token refresh: it is what re-runs the
        # backend's access-mode gate, so it has to actually happen.
        clock = FakeClock()
        backend = self._backend(self._body(), self._body(token=_ROTATED_SECRET))
        auth = await self._provider(backend, clock=clock).auth_for(_SERVER_ID)
        server = FakeMcpEndpoint()
        async with server.client(auth) as client:
            await client.get("https://mcp.linear.app/mcp")
            clock.advance(_LIFETIME)
            await client.get("https://mcp.linear.app/mcp")
        assert backend.mints == 2
        assert server.seen_bearers == [
            self._bearer(_SECRET),
            self._bearer(_ROTATED_SECRET),
        ]

    async def test_a_rejected_bearer_is_re_minted_once_and_never_looped(self) -> None:
        backend = self._backend(self._body(), self._body(token=_ROTATED_SECRET))
        auth = await self._provider(backend).auth_for(_SERVER_ID)
        server = FakeMcpEndpoint(httpx.codes.UNAUTHORIZED)
        async with server.client(auth) as client:
            response = await client.get("https://mcp.linear.app/mcp")
        assert response.status_code == httpx.codes.UNAUTHORIZED
        assert backend.mints == 2
        assert server.seen_bearers == [
            self._bearer(_SECRET),
            self._bearer(_ROTATED_SECRET),
        ]

    async def test_the_expiry_the_backend_stated_is_the_one_that_is_cached(
        self,
    ) -> None:
        backend = self._backend()
        minted = await self._mint(backend)
        assert isinstance(minted, MintedToken)
        assert minted.expires_at == _EXPIRES_AT
        assert minted.value.get_secret_value() == _SECRET

    async def test_the_minted_token_does_not_render(self) -> None:
        minted = await self._mint(self._backend())
        assert _SECRET not in repr(minted)
        assert _SECRET not in str(minted)
        assert _SECRET not in minted.model_dump_json()


class TestBothSeams(BackendProviderFixtureMixin):
    async def test_one_object_answers_both_protocols(self) -> None:
        # Neither Protocol is ``runtime_checkable``, so the annotations are the
        # static half of this check (basedpyright) and driving both seams
        # through the annotated names is the runtime half. P2-3 holds exactly
        # one object and calls exactly these two methods.
        backend = self._backend()
        provider = self._provider(backend)
        directory: McpConnectionDirectory = provider
        credentials: CredentialProvider = provider
        config = await directory.connection_for(_SERVER_ID)
        auth = await credentials.auth_for(_SERVER_ID)
        assert isinstance(config, McpServerConnectionConfig)
        assert isinstance(auth, httpx.Auth)
        # One call each: the directory minted, the credential seam did not.
        assert backend.mints == 1


class TestFailureTaxonomy(BackendProviderFixtureMixin):
    @pytest.mark.parametrize(
        ("status", "expected", "family"),
        [
            (httpx.codes.UNAUTHORIZED, BackendMcpCredentialDeniedError, McpAuthError),
            (httpx.codes.FORBIDDEN, BackendMcpCredentialDeniedError, McpAuthError),
            (httpx.codes.NOT_FOUND, BackendMcpConnectorNotFoundError, McpNotFoundError),
            (httpx.codes.CONFLICT, BackendMcpNoBearerError, McpRequestRejectedError),
            (
                httpx.codes.BAD_REQUEST,
                BackendMcpMintRejectedError,
                McpRequestRejectedError,
            ),
            (
                httpx.codes.INTERNAL_SERVER_ERROR,
                BackendMcpMintUnavailableError,
                McpConnectionError,
            ),
            (
                httpx.codes.SERVICE_UNAVAILABLE,
                BackendMcpMintUnavailableError,
                McpConnectionError,
            ),
        ],
    )
    async def test_each_status_lands_on_its_typed_rung(
        self, status, expected, family
    ) -> None:
        provider = self._failing_provider(status=status)
        with pytest.raises(expected) as exc:
            await provider.connection_for(_SERVER_ID)
        assert isinstance(exc.value, family)

    async def test_an_off_connector_is_an_auth_failure_with_safe_copy(self) -> None:
        # 403 is how the backend's PRD-06 D3 access-mode gate answers. The run
        # must be told it was refused — not that the connector is down — and the
        # wire reason code must not become model-visible copy.
        provider = self._failing_provider(status=httpx.codes.FORBIDDEN)
        with pytest.raises(BackendMcpCredentialDeniedError) as exc:
            await provider.connection_for(_SERVER_ID)
        assert str(exc.value) == BackendCredentialMessages.ACCESS_REFUSED
        assert "connector_access_off" not in str(exc.value)

    async def test_a_404_does_not_distinguish_missing_from_another_tenants(
        self,
    ) -> None:
        provider = self._failing_provider(status=httpx.codes.NOT_FOUND)
        with pytest.raises(BackendMcpConnectorNotFoundError) as exc:
            await provider.connection_for(_SERVER_ID)
        assert str(exc.value) == BackendCredentialMessages.NOT_CONNECTED
        assert _SERVER_ID not in str(exc.value)
        assert _ORG_ID not in str(exc.value)

    async def test_a_409_says_reconnecting_will_not_help(self) -> None:
        provider = self._failing_provider(status=httpx.codes.CONFLICT)
        with pytest.raises(BackendMcpNoBearerError) as exc:
            await provider.connection_for(_SERVER_ID)
        assert str(exc.value) == BackendCredentialMessages.NO_BEARER
        assert exc.value.status_code == httpx.codes.CONFLICT

    async def test_a_transport_failure_is_retryable(self) -> None:
        provider = self._failing_provider(
            error=httpx.ConnectError("connection refused")  # type: ignore[arg-type]
        )
        with pytest.raises(BackendMcpMintUnavailableError) as exc:
            await provider.connection_for(_SERVER_ID)
        assert isinstance(exc.value, McpConnectionError)
        assert str(exc.value) == BackendCredentialMessages.UNAVAILABLE

    async def test_a_timeout_is_retryable_too(self) -> None:
        provider = self._failing_provider(
            error=httpx.ReadTimeout("mint timed out")  # type: ignore[arg-type]
        )
        with pytest.raises(BackendMcpMintUnavailableError):
            await provider.connection_for(_SERVER_ID)

    async def test_a_non_json_body_is_a_typed_protocol_error(self) -> None:
        provider = self._failing_provider(raw_body="<html>gateway</html>")  # type: ignore[arg-type]
        with pytest.raises(BackendMcpMintUnreadableError) as exc:
            await provider.connection_for(_SERVER_ID)
        assert str(exc.value) == BackendCredentialMessages.UNREADABLE

    @pytest.mark.parametrize(
        "body_kwargs",
        [
            {"expires_at": "not-a-timestamp"},
            {"url": ""},
        ],
    )
    async def test_a_malformed_field_is_a_typed_protocol_error(
        self, body_kwargs
    ) -> None:
        backend = self._backend(self._body(**body_kwargs))
        with pytest.raises(BackendMcpMintUnreadableError):
            await self._provider(backend).connection_for(_SERVER_ID)

    async def test_a_missing_field_is_a_typed_protocol_error(self) -> None:
        body = self._body()
        del body["access_token"]
        with pytest.raises(BackendMcpMintUnreadableError):
            await self._provider(self._backend(body)).connection_for(_SERVER_ID)

    async def test_a_typed_error_never_carries_the_credential(self) -> None:
        # A pydantic failure echoes the offending value, and on this route that
        # value is the token. The conversion must not carry it through.
        backend = self._backend(self._body(expires_at="not-a-timestamp"))
        with pytest.raises(McpClientError) as exc:
            await self._provider(backend).connection_for(_SERVER_ID)
        assert _SECRET not in str(exc.value)
        assert _SECRET not in repr(exc.value)


class TestResponseContract(BackendProviderFixtureMixin):
    def test_a_refresh_token_has_nowhere_to_land(self) -> None:
        # Structural, not conventional. If the backend ever started returning
        # one, this side would reject the body rather than quietly hold a
        # credential it has no business holding.
        with pytest.raises(ValueError):
            BackendMintedCredential.model_validate(
                self._body(refresh_token="lin_oauth_refresh-value")
            )

    def test_the_parsed_credential_does_not_render(self) -> None:
        parsed = BackendMintedCredential.model_validate(self._body())
        assert _SECRET not in repr(parsed)
        assert _SECRET not in str(parsed)
        assert _SECRET not in parsed.model_dump_json()
        assert _SECRET not in json.dumps(parsed.model_dump(mode="json"), default=str)
        assert parsed.access_token.get_secret_value() == _SECRET

    def test_scopes_survive_the_round_trip(self) -> None:
        parsed = BackendMintedCredential.model_validate(self._body())
        assert parsed.scopes == _SCOPES

    def test_the_config_rejects_a_blank_tenant_or_base_url(self) -> None:
        for blank in ("base_url", "org_id", "user_id"):
            with pytest.raises(ValueError):
                BackendMintConfig(
                    **{
                        "base_url": _BASE_URL,
                        "org_id": _ORG_ID,
                        "user_id": _USER_ID,
                        "service_token": SecretStr(_SERVICE_TOKEN),
                        blank: "",
                    }
                )


class TestFromEnv(BackendProviderFixtureMixin):
    def test_an_unconfigured_lane_selects_no_provider(self) -> None:
        assert (
            BackendScopedTokenCredentialProvider.from_env(
                org_id=_ORG_ID, user_id=_USER_ID, env={}
            )
            is None
        )

    @pytest.mark.parametrize(
        "env",
        [
            {"BACKEND_BASE_URL": _BASE_URL},
            {"ENTERPRISE_SERVICE_TOKEN": _SERVICE_TOKEN},
            {"BACKEND_BASE_URL": "  ", "ENTERPRISE_SERVICE_TOKEN": _SERVICE_TOKEN},
        ],
    )
    def test_half_a_lane_is_no_lane(self, env) -> None:
        # Both-or-neither: a deployment with only one half must fall through to
        # another provider at wiring time, not fail at the first tool call.
        assert (
            BackendScopedTokenCredentialProvider.from_env(
                org_id=_ORG_ID, user_id=_USER_ID, env=env
            )
            is None
        )

    async def test_a_configured_lane_builds_a_working_provider(self) -> None:
        backend = self._backend()
        provider = BackendScopedTokenCredentialProvider.from_env(
            org_id=_ORG_ID,
            user_id=_USER_ID,
            env={
                "BACKEND_BASE_URL": f"{_BASE_URL}/",
                "ENTERPRISE_SERVICE_TOKEN": _SERVICE_TOKEN,
            },
            http_client=backend.client(),
            clock=FakeClock(),
        )
        assert isinstance(provider, BackendScopedTokenCredentialProvider)
        config = await provider.connection_for(_SERVER_ID)
        assert config.url == _URL
        # The trailing slash on the base URL must not double up in the route.
        assert backend.paths == [f"/internal/v1/mcp/servers/{_SERVER_ID}/access-token"]
