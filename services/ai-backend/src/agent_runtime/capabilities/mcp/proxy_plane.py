"""The credential plane for MCP-over-proxy: directory, auth, and client.

``McpPerToolCollaborators`` asks for three things the per-tool tool source cannot
invent — a :class:`McpConnectionDirectory`, a ``CredentialProvider``, and
(optionally) a client factory. Those names were written for DIRECT-CONNECT,
where ai-backend would open the vendor's server itself and therefore needs the
vendor's bearer. Over the proxy the same three seams are satisfied far more
simply, and two of them look degenerate until you know why:

* the **directory** hands back a per-server URL that is never dialled. The
  library needs a URL to build a ``StreamableHttpConnection``; ours carries the
  ``server_id`` the proxy routes on, so the address and the routing key are one
  value and cannot drift apart.
* the **credential provider** returns ``backend``'s service headers, not a
  vendor credential. That is the honest answer to "what authenticates this
  hop" — the hop is ai-backend → backend, and no vendor secret exists in this
  process at all. It was direct-connect that required one.
* the **client factory** is where the real work is: it injects
  ``httpx_client_factory`` per connection so every MCP request rides
  :class:`~agent_runtime.capabilities.mcp.proxy_transport.BackendProxyTransport`.

The consequence that matters for permissions: ``backend`` still sees, and still
authorises, every single MCP call. The PRD-06 D3(c) access-mode gate runs per
request exactly as it does today, which is why ``read``-mode connectors keep
working — the thing direct-connect could not do, because a token minted once is
answerable to nobody afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import httpx

from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.connection import McpServerConnectionConfig
from agent_runtime.capabilities.mcp.proxy_transport import (
    BackendProxyTransport,
    McpProxyTransportError,
)


class ProxyPlaneValues:
    """Wire constants for the synthetic endpoint."""

    #: A host the library renders into requests our transport intercepts before
    #: any socket is opened. Deliberately not routable: if the transport is ever
    #: bypassed, the call fails loudly instead of reaching something real.
    SYNTHETIC_ORIGIN: Final = "https://mcp-proxy.invalid"
    PATH_PREFIX: Final = "/servers/"
    MCP_PATH: Final = "/mcp"
    URL_KEY: Final = "url"
    CLIENT_FACTORY_KEY: Final = "httpx_client_factory"
    AUTH_KEY: Final = "auth"

    @classmethod
    def url_for(cls, server_id: str) -> str:
        """Render the synthetic endpoint for ``server_id``."""

        return f"{cls.SYNTHETIC_ORIGIN}{cls.PATH_PREFIX}{server_id}{cls.MCP_PATH}"

    @classmethod
    def server_id_from(cls, url: str) -> str:
        """Recover the ``server_id`` this plane wrote into ``url``.

        Raising on a foreign URL is the point. A connection reaching the factory
        without having come from :class:`ProxyConnectionDirectory` would
        otherwise be bound to a transport routing at some *other* server — an
        authorization mix-up, not a formatting one.
        """

        prefix = f"{cls.SYNTHETIC_ORIGIN}{cls.PATH_PREFIX}"
        if not url.startswith(prefix):
            raise McpProxyTransportError(
                "An MCP connection did not come from the proxy directory"
            )
        server_id = url[len(prefix) :].removesuffix(cls.MCP_PATH)
        if not server_id or "/" in server_id:
            raise McpProxyTransportError("An MCP proxy URL carried no usable server id")
        return server_id


@dataclass(frozen=True)
class ProxyConnectionDirectory:
    """Endpoint config per server, addressed by the id the proxy routes on.

    Stateless on purpose. There is no registry to consult because there is no
    per-server endpoint to look up: every server is reached through the same
    backend proxy, and the only thing that varies is the id in the path.
    """

    async def connection_for(self, server_id: str) -> McpServerConnectionConfig:
        """Return the synthetic endpoint that encodes ``server_id``."""

        return McpServerConnectionConfig(
            url=ProxyPlaneValues.url_for(server_id),
            transport=McpTransport.HTTP,
            headers={},
        )


@dataclass(frozen=True)
class ProxyServiceCredentials:
    """What authenticates the ai-backend → backend hop.

    Returns a header mapping rather than an ``httpx.Auth`` because there is no
    rotation to perform: the service token is process configuration and the
    per-user identity rides beside it. Nothing here is a vendor secret, which is
    the whole point — this process never holds one.
    """

    headers: Mapping[str, str]

    async def auth_for(self, server_id: str) -> Mapping[str, str]:
        """Return the service headers for ``server_id`` (identical per server)."""

        del server_id  # the hop is the same one for every connector
        return dict(self.headers)


@dataclass(frozen=True)
class ProxyMcpClientFactory:
    """Build the library's client with every connection bound to the proxy.

    This is the seam that makes the approach work at all: the MCP SDK
    constructs its own ``httpx.AsyncClient`` per session unless handed a
    factory, and ``StreamableHttpConnection`` exposes exactly that field. The
    SDK wraps the client it builds in ``async with``, so the transport's
    ``aclose`` — and with it the backend lease release — happens on the SDK's
    own teardown rather than needing a lifecycle of our own.
    """

    backend_url: str
    org_id: str
    user_id: str
    service_headers: Mapping[str, str]
    timeout_seconds: float

    def create(self, connections: Mapping[str, Any]) -> Any:
        """Return a ``MultiServerMCPClient`` whose every hop goes via the proxy."""

        from langchain_mcp_adapters.client import (  # noqa: PLC0415
            MultiServerMCPClient,
        )

        return MultiServerMCPClient(
            {name: self._bound(connection) for name, connection in connections.items()}
        )

    def _bound(self, connection: Mapping[str, Any]) -> dict[str, Any]:
        """Attach a proxy-backed client factory to one built connection."""

        bound = dict(connection)
        server_id = ProxyPlaneValues.server_id_from(
            str(bound.get(ProxyPlaneValues.URL_KEY, ""))
        )
        bound[ProxyPlaneValues.CLIENT_FACTORY_KEY] = self._factory_for(server_id)
        # The bearer slot is emptied deliberately. Direct-connect put the
        # vendor's credential here; over the proxy there is none to put, and
        # leaving a value would imply this process holds one.
        bound.pop(ProxyPlaneValues.AUTH_KEY, None)
        return bound

    def _factory_for(self, server_id: str):
        """Return an ``McpHttpClientFactory`` bound to one server."""

        def build(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            del auth  # the hop is authenticated by the service headers below
            transport = BackendProxyTransport(
                backend_url=self.backend_url,
                server_id=server_id,
                org_id=self.org_id,
                user_id=self.user_id,
                service_headers=dict(self.service_headers),
                timeout_seconds=self.timeout_seconds,
                client=httpx.AsyncClient(timeout=self.timeout_seconds),
            )
            return httpx.AsyncClient(
                transport=transport,
                headers=headers or {},
                timeout=timeout if timeout is not None else self.timeout_seconds,
            )

        return build


class ProxyCredentialPlane:
    """Assemble the three collaborators for one run's identity."""

    @classmethod
    def build(
        cls,
        *,
        backend_url: str,
        org_id: str,
        user_id: str,
        service_headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> tuple[
        ProxyConnectionDirectory, ProxyServiceCredentials, ProxyMcpClientFactory
    ]:
        """Return ``(directory, credentials, client_factory)``."""

        return (
            ProxyConnectionDirectory(),
            ProxyServiceCredentials(headers=dict(service_headers)),
            ProxyMcpClientFactory(
                backend_url=backend_url,
                org_id=org_id,
                user_id=user_id,
                service_headers=dict(service_headers),
                timeout_seconds=timeout_seconds,
            ),
        )


__all__ = (
    "ProxyConnectionDirectory",
    "ProxyCredentialPlane",
    "ProxyMcpClientFactory",
    "ProxyPlaneValues",
    "ProxyServiceCredentials",
)
