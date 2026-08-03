"""Let ``langchain-mcp-adapters`` speak MCP *through* the backend proxy.

The per-tool MCP path was designed direct-connect: ai-backend would open
``https://mcp.linear.app/mcp`` itself, which means holding the vendor's bearer
in-process, which means a ``CredentialProvider``, which means the P2-7b mint.
That chain never completed, and its last link cannot serve the common case —
the mint refuses ``read``-mode connectors by design (PRD-06 D3(c)), and a
freshly projected connector is ``read``. So the new path could not have served
Linear even with its flag on.

None of that is required by the library. ``langchain-mcp-adapters`` wants a
``Connection`` — a URL, headers, and optionally an httpx client factory. It has
no opinion about *whose* URL. This module supplies a client whose transport
carries MCP's JSON-RPC over the proxy ``services/backend`` already exposes, so:

* the vendor credential stays in ``backend``, where it already lives;
* ``backend`` keeps policing every call, which is what lets ``read`` connectors
  work at all — the access-mode gate runs per request rather than once at mint;
* ai-backend gains no new secret, and no security gate is reversed;
* the library still does every byte of MCP protocol work.

Why a transport and not an endpoint
-----------------------------------
The alternative was to teach ``backend`` to speak MCP streamable-http natively.
That is a new public protocol surface on the service that holds the credentials,
and it would duplicate the admission sequence ``proxy_internal_rpc`` already
runs. Translating at the client keeps the trust boundary exactly where it is and
touches one service instead of two.

What this deliberately does NOT implement
-----------------------------------------
Streamable-http's SSE half. MCP allows a server to answer a POST with an event
stream; the backend proxy is request/response and answers one JSON-RPC result.
Every method the tool source actually issues — ``initialize``, ``tools/list``,
``tools/call`` — is unary, so the unary half is the whole requirement. A server
that *insists* on streaming would fail here rather than silently degrade, which
is the right shape for a capability we do not have.
"""

from __future__ import annotations

import json
from typing import Any, Final

import httpx

from agent_runtime.capabilities.mcp.constants import Keys, Values


class ProxyTransportValues:
    """Wire constants for the proxy hop. Local by construction."""

    JSON_MEDIA_TYPE: Final = "application/json"
    CONTENT_TYPE: Final = "content-type"
    SESSION_PATH: Final = "/client-session"
    POST: Final = "POST"
    #: MCP notifications carry no ``id`` and expect no reply. JSON-RPC says a
    #: notification gets no response at all; HTTP still needs a status, and
    #: 202 is what the MCP spec's streamable-http binding uses for exactly this.
    ACCEPTED: Final = 202
    OK: Final = 200
    FORBIDDEN: Final = 403


class McpProxyTransportError(Exception):
    """The proxy hop failed before any JSON-RPC result existed.

    Distinct from a JSON-RPC error *result*, which is a successful hop carrying
    a failure the model should read. This is the hop itself failing, and the
    library surfaces it as a connection error rather than a tool result.
    """


class McpProxyAccessDeniedError(McpProxyTransportError, PermissionError):
    """``backend``'s access-mode gate refused this call (PRD-06 D3(c)).

    Subclasses ``PermissionError`` deliberately: ``McpErrorTaxonomy`` classifies
    by exception type, and ``PermissionError`` is the kind that maps to
    ``PERMISSION_DENIED`` / ``retryable=False``. Left as a bare transport error
    a write refused on a ``read``-mode connector would read as "the MCP server
    could not be reached" — which is both wrong and un-actionable, since the fix
    is to change the connector's access mode, not to retry.
    """


class BackendProxyTransport(httpx.AsyncBaseTransport):
    """Carry one MCP session's JSON-RPC over ``backend``'s internal proxy.

    One instance per MCP session, because the lease it holds is per session.
    ``langchain-mcp-adapters`` builds a client per connection, so that lines up
    without any pooling of its own.

    The lease is acquired lazily on the first request rather than in the
    constructor: building a connection must not cost a backend round-trip for a
    server the model never calls, and the tool source builds connections for
    every authorized server.
    """

    def __init__(
        self,
        *,
        backend_url: str,
        server_id: str,
        org_id: str,
        user_id: str,
        service_headers: dict[str, str],
        timeout_seconds: float,
        client: httpx.AsyncClient,
    ) -> None:
        """Bind the one server this transport proxies for."""

        self._backend_url = backend_url.rstrip("/")
        self._server_id = server_id
        self._org_id = org_id
        self._user_id = user_id
        self._service_headers = dict(service_headers)
        self._timeout = timeout_seconds
        self._client = client
        self._lease: str | None = None

    @property
    def lease(self) -> str | None:
        """The backend-owned session lease, once acquired."""

        return self._lease

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Translate one MCP JSON-RPC POST into a proxied backend call."""

        if request.method.upper() != ProxyTransportValues.POST:
            # Streamable-http's non-POST verbs are session lifecycle, not
            # JSON-RPC: GET opens the server's event stream, DELETE terminates
            # the session. The proxy owns the session on our behalf and
            # ``aclose`` already releases it, so answering here is both correct
            # and keeps a teardown DELETE from acquiring a lease to discard.
            return httpx.Response(
                ProxyTransportValues.ACCEPTED, request=request, content=b""
            )
        payload = self._json_rpc_body(request)
        lease = await self._ensure_lease()
        result = await self._post_rpc(payload, lease=lease)
        if result is None:
            # A notification. Nothing to return, and returning an empty JSON
            # body would be a malformed JSON-RPC response rather than silence.
            return httpx.Response(
                ProxyTransportValues.ACCEPTED, request=request, content=b""
            )
        body = json.dumps(result).encode(Keys.Encoding.UTF_8)
        return httpx.Response(
            ProxyTransportValues.OK,
            request=request,
            content=body,
            headers={
                ProxyTransportValues.CONTENT_TYPE: ProxyTransportValues.JSON_MEDIA_TYPE
            },
        )

    async def aclose(self) -> None:
        """Release the lease so the backend's session pool does not leak one.

        Called by ``httpx.AsyncClient.aclose``, which the MCP SDK runs via
        ``async with client:`` around every session it opens — so this is the
        real teardown path, not a hook nobody pulls.
        """

        lease = self._lease
        self._lease = None
        try:
            if lease is None:
                return
            await self._client.post(
                f"{self._backend_url}"
                f"{Values.Route.INTERNAL_MCP_CLIENT_SESSION_RELEASE.format(server_id=self._server_id)}",
                # ``InternalMcpSessionReleaseRequest`` requires the identity as
                # well as the lease. Sending the lease alone is a 422 that
                # ``post`` does not raise for — the release would look like it
                # happened and the backend would hold the session until expiry.
                json={
                    Keys.Field.ORG_ID: self._org_id,
                    Keys.Field.USER_ID: self._user_id,
                    Keys.Field.LEASE: lease,
                },
                headers=self._service_headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            # Release is best-effort on purpose: the backend expires a lease on
            # its own, and raising here would turn a completed run into a failed
            # one at teardown.
            return
        finally:
            # The backend-facing client is ours, not the SDK's — closing the
            # outer client does not reach it, so it would leak a connection
            # pool per MCP session.
            await self._client.aclose()

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _json_rpc_body(request: httpx.Request) -> dict[str, Any]:
        """Read the JSON-RPC envelope the MCP client is POSTing."""

        try:
            payload = json.loads(request.content or b"{}")
        except ValueError as error:
            raise McpProxyTransportError("MCP client sent a non-JSON body") from error
        if not isinstance(payload, dict):
            raise McpProxyTransportError("MCP client sent a non-object JSON-RPC body")
        return payload

    async def _ensure_lease(self) -> str:
        """Return this session's lease, acquiring one on first use."""

        if self._lease is not None:
            return self._lease
        try:
            response = await self._client.post(
                f"{self._backend_url}/internal/v1/mcp/servers/"
                f"{self._server_id}{ProxyTransportValues.SESSION_PATH}",
                params={
                    Keys.Field.ORG_ID: self._org_id,
                    Keys.Field.USER_ID: self._user_id,
                },
                headers=self._service_headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise McpProxyTransportError(
                "The MCP proxy session could not be opened"
            ) from error
        if response.status_code == ProxyTransportValues.FORBIDDEN:
            # The gate can refuse at session-open too, when a connector's access
            # is off entirely rather than merely read-only.
            raise McpProxyAccessDeniedError(
                "This connector's access mode does not permit that call"
            )
        if response.status_code >= 400:
            raise McpProxyTransportError(
                f"The MCP proxy refused a session ({response.status_code})"
            )
        lease = self._lease_from(response)
        self._lease = lease
        return lease

    @staticmethod
    def _lease_from(response: httpx.Response) -> str:
        """Pull the opaque lease out of a client-session response."""

        try:
            payload = response.json()
        except ValueError as error:
            raise McpProxyTransportError(
                "The MCP proxy session response was not JSON"
            ) from error
        lease = payload.get(Keys.Field.LEASE) if isinstance(payload, dict) else None
        if not isinstance(lease, str) or not lease.strip():
            raise McpProxyTransportError("The MCP proxy returned no session lease")
        return lease

    async def _post_rpc(
        self, payload: dict[str, Any], *, lease: str
    ) -> dict[str, Any] | None:
        """POST one JSON-RPC envelope through the proxy and unwrap the result.

        Returns ``None`` for a notification — a request with no ``id`` — which
        JSON-RPC says gets no response.
        """

        is_notification = Keys.JsonRpc.ID not in payload
        try:
            response = await self._client.post(
                f"{self._backend_url}"
                f"{Values.Route.INTERNAL_MCP_RPC.format(server_id=self._server_id)}",
                json={
                    Keys.Field.ORG_ID: self._org_id,
                    Keys.Field.USER_ID: self._user_id,
                    Keys.Field.LEASE: lease,
                    Keys.JsonRpc.PAYLOAD: payload,
                },
                headers=self._service_headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise McpProxyTransportError("The MCP proxy call failed") from error
        if response.status_code == ProxyTransportValues.FORBIDDEN:
            raise McpProxyAccessDeniedError(
                "This connector's access mode does not permit that call"
            )
        if response.status_code >= 400:
            raise McpProxyTransportError(
                f"The MCP proxy rejected the call ({response.status_code})"
            )
        if is_notification:
            return None
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        """Return the JSON-RPC envelope the proxy wrapped.

        The proxy answers ``{"payload": {...}}`` where the inner object is the
        connector's own JSON-RPC response. Handing the OUTER object back would
        give the MCP client an envelope with no ``jsonrpc`` field, which it
        rejects as a protocol violation rather than as the proxy shape it is.
        """

        try:
            body = response.json()
        except ValueError as error:
            raise McpProxyTransportError(
                "The MCP proxy returned a non-JSON body"
            ) from error
        if not isinstance(body, dict):
            raise McpProxyTransportError("The MCP proxy returned a non-object body")
        inner = body.get(Keys.JsonRpc.PAYLOAD)
        # The `jsonrpc` member is what makes this a response rather than merely
        # a dict. Accepting any object here let an EMPTY payload through as a
        # successful result, which the MCP client would then fail to parse far
        # from the cause — the proxy hop is where that has to be caught.
        if isinstance(inner, dict) and Keys.JsonRpc.JSONRPC in inner:
            return inner
        # Some proxy responses are already the bare envelope. Accept both rather
        # than depending on which one this deployment happens to return.
        if Keys.JsonRpc.JSONRPC in body:
            return body
        raise McpProxyTransportError("The MCP proxy returned no JSON-RPC payload")


__all__ = (
    "BackendProxyTransport",
    "McpProxyAccessDeniedError",
    "McpProxyTransportError",
    "ProxyTransportValues",
)
