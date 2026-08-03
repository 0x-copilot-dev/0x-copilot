"""The proxy credential plane satisfies the per-tool seams without a vendor secret.

The claim under test is the security one: a connection built by this plane
reaches ``backend``, carries no vendor credential, and routes at the server the
directory was asked for. Everything else here defends that claim's edges.
"""

from __future__ import annotations

import httpx
import pytest

from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.proxy_plane import (
    ProxyConnectionDirectory,
    ProxyCredentialPlane,
    ProxyMcpClientFactory,
    ProxyPlaneValues,
    ProxyServiceCredentials,
)
from agent_runtime.capabilities.mcp.proxy_transport import (
    BackendProxyTransport,
    McpProxyTransportError,
)
from agent_runtime.capabilities.mcp.tool_source import McpConnectionBuilder

BACKEND_URL = "http://127.0.0.1:8100"
SERVICE_HEADERS = {
    "x-enterprise-service-token": "svc-token",
    "x-enterprise-org-id": "org-1",
    "x-enterprise-user-id": "user-1",
}


class PlaneFixture:
    """Build the plane the way the factory does."""

    @staticmethod
    def build() -> tuple[
        ProxyConnectionDirectory, ProxyServiceCredentials, ProxyMcpClientFactory
    ]:
        """Return the three collaborators for a fixed identity."""

        return ProxyCredentialPlane.build(
            backend_url=BACKEND_URL,
            org_id="org-1",
            user_id="user-1",
            service_headers=SERVICE_HEADERS,
            timeout_seconds=10.0,
        )

    @staticmethod
    async def connection(server_id: str) -> dict[str, object]:
        """Return the built ``Connection`` for ``server_id``, plane end to end."""

        directory, credentials, _ = PlaneFixture.build()
        config = await directory.connection_for(server_id)
        auth = await credentials.auth_for(server_id)
        return dict(
            McpConnectionBuilder.build(config=config, auth=auth, server_name="linear")
        )


class TestTheDirectoryAddressesTheProxy:
    """The endpoint is the proxy's, and it carries the routing key."""

    @pytest.mark.asyncio
    async def test_the_endpoint_is_streamable_http(self) -> None:
        directory = ProxyConnectionDirectory()

        config = await directory.connection_for("srv-1")

        assert config.transport is McpTransport.HTTP

    @pytest.mark.asyncio
    async def test_the_url_is_never_the_vendors(self) -> None:
        directory = ProxyConnectionDirectory()

        config = await directory.connection_for("srv-1")

        assert config.url.startswith(ProxyPlaneValues.SYNTHETIC_ORIGIN)

    @pytest.mark.asyncio
    async def test_the_url_round_trips_the_server_id(self) -> None:
        directory = ProxyConnectionDirectory()

        config = await directory.connection_for("srv-abc-123")

        assert ProxyPlaneValues.server_id_from(config.url) == "srv-abc-123"

    @pytest.mark.asyncio
    async def test_two_servers_get_two_endpoints(self) -> None:
        directory = ProxyConnectionDirectory()

        first = await directory.connection_for("srv-1")
        second = await directory.connection_for("srv-2")

        assert first.url != second.url


class TestTheCredentialIsTheServiceHop:
    """No vendor secret exists in this process, and none is invented."""

    @pytest.mark.asyncio
    async def test_the_credential_is_the_backend_service_headers(self) -> None:
        _, credentials, _ = PlaneFixture.build()

        auth = await credentials.auth_for("srv-1")

        assert auth == SERVICE_HEADERS

    @pytest.mark.asyncio
    async def test_the_headers_are_a_copy_a_caller_cannot_poison(self) -> None:
        _, credentials, _ = PlaneFixture.build()

        auth = dict(await credentials.auth_for("srv-1"))
        auth["x-enterprise-org-id"] = "org-attacker"

        assert (await credentials.auth_for("srv-1"))["x-enterprise-org-id"] == "org-1"

    @pytest.mark.asyncio
    async def test_the_built_connection_carries_no_vendor_bearer(self) -> None:
        connection = await PlaneFixture.connection("srv-1")

        # `auth` is httpx's credential slot and `authorization` the header one.
        # A vendor bearer could only arrive through one of the two.
        assert "auth" not in connection
        headers = dict(connection["headers"])  # type: ignore[arg-type]
        assert not any(key.lower() == "authorization" for key in headers)

    @pytest.mark.asyncio
    async def test_the_built_connection_authenticates_the_backend_hop(self) -> None:
        connection = await PlaneFixture.connection("srv-1")

        assert dict(connection["headers"]) == SERVICE_HEADERS  # type: ignore[arg-type]


class TestTheFactoryBindsEveryHopToTheProxy:
    """The client the library gets can only reach ``backend``."""

    @pytest.mark.asyncio
    async def test_each_connection_gains_a_client_factory(self) -> None:
        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-1")

        bound = factory._bound(connection)

        assert callable(bound[ProxyPlaneValues.CLIENT_FACTORY_KEY])

    @pytest.mark.asyncio
    async def test_the_client_it_builds_speaks_through_our_transport(self) -> None:
        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-1")

        bound = factory._bound(connection)
        client = bound[ProxyPlaneValues.CLIENT_FACTORY_KEY]()

        assert isinstance(client._transport, BackendProxyTransport)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_the_transport_routes_at_the_server_the_url_named(self) -> None:
        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-abc-123")

        client = factory._bound(connection)[ProxyPlaneValues.CLIENT_FACTORY_KEY]()

        assert client._transport._server_id == "srv-abc-123"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_the_transport_targets_the_backend_not_the_vendor(self) -> None:
        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-1")

        client = factory._bound(connection)[ProxyPlaneValues.CLIENT_FACTORY_KEY]()

        assert client._transport._backend_url == BACKEND_URL
        await client.aclose()

    @pytest.mark.asyncio
    async def test_an_auth_object_is_stripped_before_the_hop(self) -> None:
        """A vendor ``httpx.Auth`` must not survive into the proxy connection.

        Nothing sets one today. The assertion is about what happens if a future
        directory does: the bearer must be dropped, not forwarded to a transport
        that would attach it to a ``backend`` call.
        """

        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-1")
        connection[ProxyPlaneValues.AUTH_KEY] = httpx.BasicAuth("u", "p")

        assert ProxyPlaneValues.AUTH_KEY not in factory._bound(connection)

    @pytest.mark.asyncio
    async def test_a_foreign_url_is_refused_rather_than_misrouted(self) -> None:
        """A connection from anywhere else must not be bound to a guessed server."""

        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-1")
        connection[ProxyPlaneValues.URL_KEY] = "https://mcp.linear.app/mcp"

        with pytest.raises(McpProxyTransportError):
            factory._bound(connection)

    @pytest.mark.asyncio
    async def test_the_caller_supplied_headers_survive(self) -> None:
        """The SDK passes the connection's headers in; dropping them would
        strip the very service token that authenticates the hop."""

        _, _, factory = PlaneFixture.build()
        connection = await PlaneFixture.connection("srv-1")

        build = factory._bound(connection)[ProxyPlaneValues.CLIENT_FACTORY_KEY]
        client = build(headers=dict(SERVICE_HEADERS))

        assert client.headers["x-enterprise-service-token"] == "svc-token"
        await client.aclose()


class TestTheUrlGrammarIsClosed:
    """``server_id_from`` is an authorization boundary, not a parser."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://mcp.linear.app/mcp",
            "http://127.0.0.1:8100/servers/srv-1/mcp",
            "",
            "https://mcp-proxy.invalid/servers//mcp",
            "https://mcp-proxy.invalid/servers/a/b/mcp",
        ],
    )
    def test_a_url_this_plane_did_not_write_is_refused(self, url: str) -> None:
        with pytest.raises(McpProxyTransportError):
            ProxyPlaneValues.server_id_from(url)

    def test_the_synthetic_origin_is_unroutable(self) -> None:
        """``.invalid`` is reserved by RFC 2606 and resolves nowhere.

        If the transport is ever bypassed, the request must fail rather than
        reach a host someone could register.
        """

        assert ProxyPlaneValues.SYNTHETIC_ORIGIN.endswith(".invalid")
