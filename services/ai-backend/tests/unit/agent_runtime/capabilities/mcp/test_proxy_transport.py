"""The proxy hop, driven by the shapes `langchain-mcp-adapters` actually sends.

These assert the translation, not a restatement of it: a JSON-RPC request goes
in, a backend proxy envelope comes out, and the connector's own reply comes back
as the JSON-RPC response the MCP client expects to parse.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent_runtime.capabilities.mcp.proxy_transport import (
    BackendProxyTransport,
    McpProxyTransportError,
)


class ProxyStubMixin:
    """A stand-in for ``services/backend``'s two internal MCP routes."""

    BACKEND = "http://127.0.0.1:8100"
    SERVER_ID = "srv_linear"
    LEASE = "lease-abc123456789"

    def stub(
        self,
        *,
        rpc_result: dict[str, Any] | None = None,
        rpc_status: int = 200,
        session_status: int = 200,
        session_body: dict[str, Any] | None = None,
    ) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/client-session"):
                body = (
                    session_body if session_body is not None else {"lease": self.LEASE}
                )
                return httpx.Response(session_status, json=body)
            if request.url.path.endswith("/release"):
                return httpx.Response(200, json={})
            if rpc_status >= 400:
                return httpx.Response(rpc_status, json={})
            return httpx.Response(200, json={"payload": rpc_result or {}})

        return httpx.AsyncClient(transport=httpx.MockTransport(handle)), seen

    def transport(self, client: httpx.AsyncClient) -> BackendProxyTransport:
        return BackendProxyTransport(
            backend_url=self.BACKEND,
            server_id=self.SERVER_ID,
            org_id="org_1",
            user_id="usr_1",
            service_headers={"x-enterprise-service-token": "tok"},
            timeout_seconds=5.0,
            client=client,
        )

    @staticmethod
    def request(payload: dict[str, Any]) -> httpx.Request:
        return httpx.Request(
            "POST",
            "https://mcp.invalid/mcp",
            content=json.dumps(payload).encode("utf-8"),
        )


class TestTheProxyHopCarriesJsonRpc(ProxyStubMixin):
    async def test_a_tool_call_reaches_the_backend_rpc_route(self) -> None:
        reply = {"jsonrpc": "2.0", "id": 3, "result": {"content": []}}
        client, seen = self.stub(rpc_result=reply)
        transport = self.transport(client)

        await transport.handle_async_request(
            self.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call"})
        )

        rpc = [r for r in seen if r.url.path.endswith("/rpc")]
        assert len(rpc) == 1
        body = json.loads(rpc[0].content)
        # The connector payload rides INSIDE the proxy envelope, with the
        # tenant identity the backend authorises against.
        assert body["payload"]["method"] == "tools/call"
        assert body["org_id"] == "org_1"
        assert body["user_id"] == "usr_1"
        assert body["lease"] == self.LEASE

    async def test_the_connectors_own_envelope_comes_back_not_the_proxys(self) -> None:
        reply = {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text"}]}}
        client, _ = self.stub(rpc_result=reply)

        response = await self.transport(client).handle_async_request(
            self.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call"})
        )

        # Handing back the OUTER `{"payload": ...}` would give the MCP client an
        # envelope with no `jsonrpc` field, which it rejects as a protocol
        # violation rather than as the proxy shape it actually is.
        assert json.loads(response.content) == reply
        assert response.status_code == 200

    async def test_the_lease_is_acquired_once_and_reused(self) -> None:
        client, seen = self.stub(rpc_result={"jsonrpc": "2.0", "id": 1, "result": {}})
        transport = self.transport(client)

        for call_id in (1, 2, 3):
            await transport.handle_async_request(
                self.request({"jsonrpc": "2.0", "id": call_id, "method": "tools/list"})
            )

        sessions = [r for r in seen if r.url.path.endswith("/client-session")]
        assert len(sessions) == 1, "one lease per session, not per call"
        assert transport.lease == self.LEASE

    async def test_no_lease_is_taken_until_the_first_call(self) -> None:
        """Building a connection must not cost a round-trip per authorized server."""

        client, seen = self.stub()

        transport = self.transport(client)

        assert seen == []
        assert transport.lease is None


class TestNotificationsGetNoBody(ProxyStubMixin):
    async def test_a_notification_returns_202_and_no_content(self) -> None:
        client, _ = self.stub(rpc_result={"jsonrpc": "2.0", "result": {}})

        response = await self.transport(client).handle_async_request(
            self.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

        # JSON-RPC says a notification gets no response. An empty JSON body
        # would be a malformed response rather than silence.
        assert response.status_code == 202
        assert response.content == b""


class TestFailuresAreTypedNotSilent(ProxyStubMixin):
    async def test_a_refused_session_raises_rather_than_returning_a_bad_lease(
        self,
    ) -> None:
        client, _ = self.stub(session_status=403)

        with pytest.raises(McpProxyTransportError):
            await self.transport(client).handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            )

    async def test_a_session_without_a_lease_raises(self) -> None:
        client, _ = self.stub(session_body={"lease": ""})

        with pytest.raises(McpProxyTransportError):
            await self.transport(client).handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            )

    async def test_a_rejected_call_raises(self) -> None:
        client, _ = self.stub(rpc_status=502)

        with pytest.raises(McpProxyTransportError):
            await self.transport(client).handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )

    async def test_a_payload_free_proxy_response_raises(self) -> None:
        """An empty proxy body is not a JSON-RPC result and must not pass as one."""

        client, _ = self.stub(rpc_result={})

        with pytest.raises(McpProxyTransportError):
            await self.transport(client).handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )


class TestTheLeaseIsReleased(ProxyStubMixin):
    async def test_close_releases_the_lease(self) -> None:
        client, seen = self.stub(rpc_result={"jsonrpc": "2.0", "id": 1, "result": {}})
        transport = self.transport(client)
        await transport.handle_async_request(
            self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        )

        await transport.aclose()

        assert any(r.url.path.endswith("/release") for r in seen)
        assert transport.lease is None

    async def test_release_failure_does_not_raise_at_teardown(self) -> None:
        """A completed run must not fail because the pool cleanup did."""

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/client-session"):
                return httpx.Response(200, json={"lease": self.LEASE})
            if request.url.path.endswith("/release"):
                raise httpx.ConnectError("backend gone")
            return httpx.Response(200, json={"payload": {"jsonrpc": "2.0", "id": 1}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        transport = self.transport(client)
        await transport.handle_async_request(
            self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        )

        await transport.aclose()

        assert transport.lease is None


class TestTheGateRefusalIsTypedNotGeneric(ProxyStubMixin):
    """A 403 is ``backend``'s access-mode gate, and must classify as such.

    ``McpErrorTaxonomy`` classifies by exception type. A refusal that arrives as
    a bare transport error is reported to the user as "the MCP server could not
    be reached" — wrong, and un-actionable: the fix is the connector's access
    mode, not a retry.
    """

    async def test_a_refused_call_raises_a_permission_error(self) -> None:
        client, _ = self.stub(rpc_status=403)
        transport = self.transport(client)

        with pytest.raises(PermissionError):
            await transport.handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )

    async def test_a_refused_session_raises_a_permission_error(self) -> None:
        """Access turned off entirely refuses at session-open, not at the call."""

        client, _ = self.stub(session_status=403)
        transport = self.transport(client)

        with pytest.raises(PermissionError):
            await transport.handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )

    async def test_the_refusal_names_no_internal_detail(self) -> None:
        client, _ = self.stub(rpc_status=403)
        transport = self.transport(client)

        with pytest.raises(PermissionError) as caught:
            await transport.handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )

        message = str(caught.value)
        assert self.BACKEND not in message
        assert "403" not in message

    async def test_another_failure_is_still_a_plain_transport_error(self) -> None:
        """Only 403 is the gate. A 500 must not be reported as a permission problem."""

        client, _ = self.stub(rpc_status=500)
        transport = self.transport(client)

        with pytest.raises(McpProxyTransportError) as caught:
            await transport.handle_async_request(
                self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
            )

        assert not isinstance(caught.value, PermissionError)


class TestSessionLifecycleVerbs(ProxyStubMixin):
    """Streamable-http's non-POST verbs are session lifecycle, not JSON-RPC."""

    async def test_a_teardown_delete_does_not_open_a_session(self) -> None:
        client, seen = self.stub()
        transport = self.transport(client)

        response = await transport.handle_async_request(
            httpx.Request("DELETE", "https://mcp.invalid/mcp")
        )

        assert response.status_code == 202
        assert seen == []

    async def test_a_stream_get_does_not_open_a_session(self) -> None:
        client, seen = self.stub()
        transport = self.transport(client)

        response = await transport.handle_async_request(
            httpx.Request("GET", "https://mcp.invalid/mcp")
        )

        assert response.status_code == 202
        assert seen == []


class TestTheLeaseIsActuallyReleased(ProxyStubMixin):
    """``InternalMcpSessionReleaseRequest`` requires the identity, not just the lease.

    ``post`` does not raise for a 422, so an under-specified release looks like
    it succeeded while the backend holds the session until it expires.
    """

    async def test_the_release_carries_everything_the_contract_requires(self) -> None:
        client, seen = self.stub(rpc_result={"jsonrpc": "2.0", "id": 1, "result": {}})
        transport = self.transport(client)
        await transport.handle_async_request(
            self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"})
        )

        await transport.aclose()

        release = [r for r in seen if r.url.path.endswith("/release")]
        assert len(release) == 1
        body = json.loads(release[0].content)
        assert set(body) == {"org_id", "user_id", "lease"}
        assert body["lease"] == self.LEASE

    async def test_nothing_is_released_when_no_session_was_opened(self) -> None:
        client, seen = self.stub()
        transport = self.transport(client)

        await transport.aclose()

        assert seen == []

    async def test_the_backend_facing_client_is_closed_too(self) -> None:
        """It is ours, not the SDK's — the outer close does not reach it."""

        client, _ = self.stub()
        transport = self.transport(client)

        await transport.aclose()

        assert client.is_closed
