from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
import httpx

from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)

from agent_runtime.capabilities.mcp.backend_provider import (
    BackendMcpClient,
    BackendMcpServiceAuth,
)
from agent_runtime.capabilities.mcp.client import (
    McpAmbiguousDispatchError,
    McpAuthError,
    McpLeaseError,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.mcp import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.middleware.auth_mcp import (
    AuthMcpTool,
    McpAuthSession,
)


@dataclass(frozen=True)
class FakeAuthSessionCreator:
    async def create_auth_session(
        self,
        *,
        server_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> McpAuthSession:
        return McpAuthSession(
            server_id=server_id,
            server_name="drive_mcp",
            display_name="Drive MCP",
            auth_url=f"https://auth.example.com/{runtime_context.user_id}/{server_id}",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )


def test_mcp_server_card_exposes_safe_auth_state() -> None:
    card = McpServerCard(
        server_id="server_123",
        name="drive_mcp",
        display_name="Drive MCP",
        short_description="Search Drive through MCP.",
        transport="http",
        auth_mode="oauth2",
        auth_state="unauthenticated",
        health="healthy",
        load_cost=1,
    )

    assert card.server_id == "server_123"
    assert card.display_name == "Drive MCP"
    assert card.auth_state == McpAuthState.UNAUTHENTICATED


def test_auth_mcp_tool_returns_safe_auth_card_payload(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    captured: dict[str, object] = {}

    def fake_interrupt(payload: dict[str, object]) -> dict[str, object]:
        captured.update(payload)
        return {"decision": "approved"}

    tool = AuthMcpTool(
        auth_session_creator=FakeAuthSessionCreator(),
        runtime_context=runtime_context_admin,
        interrupt_handler=fake_interrupt,
    )

    result = asyncio.run(
        tool.ainvoke({"server_name": "drive_mcp", "server_id": "server_123"})
    )

    assert captured["api_event_type"] == "mcp_auth_required"
    assert captured["approval_id"] == (
        f"mcp_auth:{runtime_context_admin.run_id}:server_123"
    )
    assert captured["action_id"] == captured["approval_id"]
    assert captured["approval_kind"] == "mcp_auth"
    assert captured["server_id"] == "server_123"
    assert captured["display_name"] == "Drive MCP"
    assert "auth.example.com" in str(captured["auth_url"])
    assert result["status"] == "connected"
    assert "token" not in str(captured)


def test_backend_mcp_service_auth_includes_trusted_scope_headers(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "service-token")

    headers = BackendMcpServiceAuth.headers(runtime_context_admin)

    assert headers[SERVICE_TOKEN_HEADER] == "service-token"
    assert headers[ORG_HEADER] == runtime_context_admin.org_id
    assert headers[USER_HEADER] == runtime_context_admin.user_id


async def test_backend_mcp_provider_does_not_filter_remote_oauth_scopes(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider

    responses = [
        FakeHttpResponse(
            {
                "servers": [
                    {
                        "server_id": "server_123",
                        "name": "mcp_clickup_com",
                        "display_name": "Mcp Clickup Com",
                        "short_description": "ClickUp MCP server.",
                        "transport": "http",
                        "auth_mode": "oauth2",
                        "auth_state": "authenticated",
                        "required_scopes": ["read", "write"],
                        "health": "healthy",
                        "load_cost": 1,
                    }
                ]
            }
        ),
    ]
    calls: list[dict[str, object]] = []
    provider = BackendMcpProvider(
        backend_url="http://backend.local",
        runtime_context=runtime_context_admin,
        auth_redirect_uri="http://localhost/callback",
        http_client=FakeAsyncClient(responses, calls),
    )

    cards = await provider.list_server_cards()

    assert cards[0].name == "mcp_clickup_com"
    assert cards[0].required_scopes == frozenset()


async def test_backend_mcp_provider_resolves_stable_name_before_auth_start(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider

    responses = [
        FakeHttpResponse(
            {
                "servers": [
                    {
                        "server_id": "server_123",
                        "name": "mcp_clickup_com",
                        "display_name": "Mcp Clickup Com",
                        "short_description": "ClickUp MCP server.",
                        "transport": "http",
                        "auth_mode": "oauth2",
                        "auth_state": "unauthenticated",
                        "required_scopes": [],
                        "health": "healthy",
                        "load_cost": 1,
                    }
                ]
            }
        ),
        FakeHttpResponse(
            {
                "server_id": "server_123",
                "auth_url": "https://auth.example.com/authorize",
                "expires_at": "2026-05-01T06:00:00+00:00",
            }
        ),
    ]
    calls: list[dict[str, object]] = []
    provider = BackendMcpProvider(
        backend_url="http://backend.local",
        runtime_context=runtime_context_admin,
        auth_redirect_uri="http://localhost/callback",
        http_client=FakeAsyncClient(responses, calls),
    )

    session = await provider.create_auth_session(
        server_id="mcp_clickup_com",
        runtime_context=runtime_context_admin,
    )

    auth_call = next(c for c in calls if c.get("method") == "POST")
    assert auth_call["url"].endswith("/internal/v1/mcp/servers/server_123/auth/start")
    assert session.server_id == "server_123"
    assert session.server_name == "mcp_clickup_com"


def test_backend_mcp_client_loads_tools_through_json_rpc_proxy(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse(
            {
                "lease": "lease_1234567890",
            }
        ),
        FakeHttpResponse({"payload": {"jsonrpc": "2.0", "id": 1, "result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse(
            {
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "search_tasks",
                                "description": "Search tasks.",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                }
            }
        ),
    ]

    card = McpServerCard(
        server_id="server_123",
        name="clickup",
        display_name="ClickUp",
        short_description="ClickUp MCP server.",
        transport="http",
        auth_mode="oauth2",
        auth_state="authenticated",
        health="healthy",
        load_cost=1,
    )
    client = BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=runtime_context_admin,
        card=card,
        http_client=FakeAsyncClient(responses, calls),
    )

    tools = asyncio.run(client.list_tools())

    assert tools[0].name == "search_tasks"
    assert tools[0].input_schema == {"type": "object"}
    assert calls[1]["json"]["payload"]["method"] == "initialize"
    assert calls[3]["json"]["payload"]["method"] == "tools/list"


def test_backend_mcp_client_treats_missing_resources_as_empty(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse(
            {
                "lease": "lease_1234567890",
            }
        ),
        FakeHttpResponse({"payload": {"jsonrpc": "2.0", "id": 1, "result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse(
            {
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            }
        ),
    ]

    card = McpServerCard(
        server_id="server_123",
        name="clickup",
        display_name="ClickUp",
        short_description="ClickUp MCP server.",
        transport="http",
        auth_mode="oauth2",
        auth_state="authenticated",
        health="healthy",
        load_cost=1,
    )
    client = BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=runtime_context_admin,
        card=card,
        http_client=FakeAsyncClient(responses, calls),
    )

    resources = asyncio.run(client.list_resources())

    assert resources == ()
    assert calls[3]["json"]["payload"]["method"] == "resources/list"


def test_backend_mcp_client_calls_tool_through_json_rpc_proxy(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse(
            {
                "lease": "lease_1234567890",
            }
        ),
        FakeHttpResponse({"payload": {"jsonrpc": "2.0", "id": 1, "result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse(
            {
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "task list"}],
                    },
                }
            }
        ),
    ]

    card = McpServerCard(
        server_id="server_123",
        name="clickup",
        display_name="ClickUp",
        short_description="ClickUp MCP server.",
        transport="http",
        auth_mode="oauth2",
        auth_state="authenticated",
        health="healthy",
        load_cost=1,
    )
    client = BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=runtime_context_admin,
        card=card,
        http_client=FakeAsyncClient(responses, calls),
    )

    output = asyncio.run(
        client.call_tool(tool_name="list_tasks", arguments={"include_closed": True})
    )

    assert output["content"][0]["text"] == "task list"
    assert calls[3]["json"]["payload"] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "list_tasks",
            "arguments": {"include_closed": True},
        },
    }
    assert calls[1]["json"]["lease"] == "lease_1234567890"
    assert calls[3]["json"]["lease"] == "lease_1234567890"


def test_backend_mcp_client_releases_the_opaque_lease(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse({"lease": "lease_1234567890"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse({"payload": {"result": {"tools": []}}}),
        FakeHttpResponse({"outcome": "released"}),
    ]
    client = _backend_client(runtime_context_admin, responses, calls)

    async def run() -> None:
        await client.list_tools()
        await client.aclose()

    asyncio.run(run())

    rpc_calls = [call for call in calls if call["url"].endswith("/rpc")]
    assert {call["json"]["lease"] for call in rpc_calls} == {"lease_1234567890"}
    assert all(
        "url" not in call.get("json", {})
        and "credential_ref" not in call.get("json", {})
        for call in calls
    )
    assert calls[-1]["url"].endswith("/client-session/release")
    assert calls[-1]["json"]["lease"] == "lease_1234567890"
    assert calls[-1]["json"]["cancel"] is False


def test_backend_mcp_client_reacquires_once_for_explicit_safe_stale_lease(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse({"lease": "lease_1234567890"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse(
            {"detail": {"code": "lease_stale_pre_dispatch", "redispatch_safe": True}},
            status_code=409,
        ),
        FakeHttpResponse({"outcome": "stale"}),
        FakeHttpResponse({"lease": "lease_abcdefghijkl"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse({"payload": {"result": {"content": []}}}),
    ]
    client = _backend_client(runtime_context_admin, responses, calls)

    asyncio.run(client.call_tool(tool_name="write_once", arguments={}))

    rpc_calls = [call for call in calls if call["url"].endswith("/rpc")]
    tool_calls = [
        call for call in rpc_calls if call["json"]["payload"]["method"] == "tools/call"
    ]
    assert len(tool_calls) == 2
    assert tool_calls[0]["json"]["lease"] == "lease_1234567890"
    assert tool_calls[1]["json"]["lease"] == "lease_abcdefghijkl"
    assert len([call for call in calls if call["url"].endswith("/client-session")]) == 2


def test_backend_mcp_client_does_not_replay_non_safe_lease_failure(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse({"lease": "lease_1234567890"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse(
            {"detail": {"code": "ambiguous_transport_failure"}}, status_code=503
        ),
    ]
    client = _backend_client(runtime_context_admin, responses, calls)

    with pytest.raises(McpLeaseError) as exc_info:
        asyncio.run(client.call_tool(tool_name="write_once", arguments={}))

    assert exc_info.value.code == "ambiguous_transport_failure"
    assert len([call for call in calls if call["url"].endswith("/client-session")]) == 1
    assert (
        len(
            [
                call
                for call in calls
                if call["url"].endswith("/rpc")
                and call["json"]["payload"]["method"] == "tools/call"
            ]
        )
        == 1
    )


def test_backend_mcp_client_recovers_stale_initialize_and_initialized_notification(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    init_calls: list[dict[str, object]] = []
    init_responses = [
        FakeHttpResponse({"lease": "lease_1234567890"}),
        FakeHttpResponse(
            {"detail": {"code": "lease_stale_pre_dispatch", "redispatch_safe": True}},
            status_code=409,
        ),
        FakeHttpResponse({"outcome": "stale"}),
        FakeHttpResponse({"lease": "lease_abcdefghijkl"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse({"payload": {}}),
    ]
    asyncio.run(
        _backend_client(runtime_context_admin, init_responses, init_calls).connect()
    )
    assert [
        call["json"]["payload"]["method"]
        for call in init_calls
        if call["url"].endswith("/rpc")
    ] == ["initialize", "initialize", "notifications/initialized"]

    notification_calls: list[dict[str, object]] = []
    notification_responses = [
        FakeHttpResponse({"lease": "lease_1234567890"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse(
            {"detail": {"code": "lease_stale_pre_dispatch", "redispatch_safe": True}},
            status_code=409,
        ),
        FakeHttpResponse({"outcome": "stale"}),
        FakeHttpResponse({"lease": "lease_abcdefghijkl"}),
        FakeHttpResponse({"payload": {"result": {}}}),
        FakeHttpResponse({"payload": {}}),
        FakeHttpResponse({"payload": {}}),
    ]
    asyncio.run(
        _backend_client(
            runtime_context_admin, notification_responses, notification_calls
        ).connect()
    )
    assert [
        call["json"]["payload"]["method"]
        for call in notification_calls
        if call["url"].endswith("/rpc")
    ] == [
        "initialize",
        "notifications/initialized",
        "initialize",
        "notifications/initialized",
    ]


def test_backend_mcp_client_parses_bounded_lease_failures_across_statuses() -> None:
    auth = BackendMcpClient._lease_error_from_response(
        FakeHttpResponse({"detail": {"code": "auth_required"}}, status_code=401)
    )
    saturated = BackendMcpClient._lease_error_from_response(
        FakeHttpResponse({"detail": {"code": "pool_saturated"}}, status_code=429)
    )
    unavailable = BackendMcpClient._lease_error_from_response(
        FakeHttpResponse({"detail": {"code": "server_unavailable"}}, status_code=503)
    )
    invalid = BackendMcpClient._lease_error_from_response(
        FakeHttpResponse({"detail": {"code": "lease_invalid"}}, status_code=400)
    )
    wrong_owner = BackendMcpClient._lease_error_from_response(
        FakeHttpResponse({"detail": {"code": "lease_wrong_owner"}}, status_code=403)
    )
    mismatch = BackendMcpClient._lease_error_from_response(
        FakeHttpResponse(
            {"detail": {"code": "lease_stale_pre_dispatch", "redispatch_safe": True}},
            status_code=503,
        )
    )

    assert isinstance(auth, McpAuthError)
    assert isinstance(saturated, McpLeaseError)
    assert saturated.code == "pool_saturated"
    assert saturated.redispatch_safe is False
    assert isinstance(unavailable, McpLeaseError)
    assert unavailable.code == "server_unavailable"
    assert isinstance(invalid, McpLeaseError)
    assert invalid.code == "lease_invalid"
    assert isinstance(wrong_owner, McpLeaseError)
    assert wrong_owner.code == "lease_wrong_owner"
    assert isinstance(mismatch, McpLeaseError)
    assert mismatch.code == "lease_protocol_error"
    assert mismatch.redispatch_safe is False


@pytest.mark.parametrize("code", ("pool_saturated", "server_unavailable"))
def test_backend_mcp_client_marks_only_acquisition_capacity_failures_safe(
    runtime_context_admin: AgentRuntimeContext,
    code: str,
) -> None:
    acquisition_calls: list[dict[str, object]] = []
    acquisition_status = 429 if code == "pool_saturated" else 503
    acquisition_client = _backend_client(
        runtime_context_admin,
        [FakeHttpResponse({"detail": {"code": code}}, status_code=acquisition_status)],
        acquisition_calls,
    )

    with pytest.raises(McpLeaseError) as acquisition_error:
        asyncio.run(acquisition_client.connect())

    assert acquisition_error.value.code == code
    assert acquisition_error.value.acquisition_safe is True

    rpc_calls: list[dict[str, object]] = []
    rpc_client = _backend_client(
        runtime_context_admin,
        [
            FakeHttpResponse({"lease": "lease_1234567890"}),
            FakeHttpResponse({"payload": {"result": {}}}),
            FakeHttpResponse({"payload": {}}),
            FakeHttpResponse(
                {"detail": {"code": code}}, status_code=acquisition_status
            ),
        ],
        rpc_calls,
    )

    with pytest.raises(McpLeaseError) as rpc_error:
        asyncio.run(rpc_client.call_tool(tool_name="list_tasks", arguments={}))

    assert rpc_error.value.code == code
    assert rpc_error.value.acquisition_safe is False
    assert (
        len(
            [
                call
                for call in rpc_calls
                if call["url"].endswith("/rpc")
                and call["json"]["payload"]["method"] == "tools/call"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "failure_kind",
    (
        "timeout",
        "untyped_5xx",
        "invalid_envelope",
        "json_rpc_error",
    ),
)
def test_backend_mcp_client_marks_rpc_uncertainty_ambiguous_and_never_replays(
    runtime_context_admin: AgentRuntimeContext,
    failure_kind: str,
) -> None:
    failure: object
    if failure_kind == "timeout":
        failure = httpx.TimeoutException("timed out")
    elif failure_kind == "untyped_5xx":
        failure = FakeHttpResponse({}, status_code=500)
    elif failure_kind == "invalid_envelope":
        failure = FakeHttpResponse({})
    else:
        failure = FakeHttpResponse({"payload": {"error": {"code": -32000}}})
    calls: list[dict[str, object]] = []
    client = _backend_client(
        runtime_context_admin,
        [
            FakeHttpResponse({"lease": "lease_1234567890"}),
            FakeHttpResponse({"payload": {"result": {}}}),
            FakeHttpResponse({"payload": {}}),
            failure,
        ],
        calls,
    )

    with pytest.raises(McpAmbiguousDispatchError):
        asyncio.run(client.call_tool(tool_name="write_once", arguments={}))

    assert (
        len(
            [
                call
                for call in calls
                if call["url"].endswith("/rpc")
                and call["json"]["payload"]["method"] == "tools/call"
            ]
        )
        == 1
    )


def _backend_client(
    runtime_context: AgentRuntimeContext,
    responses: list[object],
    calls: list[dict[str, object]],
) -> BackendMcpClient:
    return BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=runtime_context,
        card=McpServerCard(
            server_id="server_123",
            name="clickup",
            display_name="ClickUp",
            short_description="ClickUp MCP server.",
            transport="http",
            auth_mode="oauth2",
            auth_state="authenticated",
            health="healthy",
            load_cost=1,
        ),
        http_client=FakeAsyncClient(responses, calls),
    )


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://backend.local")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "request failed", request=request, response=response
            )


class FakeAsyncClient:
    def __init__(
        self,
        responses: list[object],
        calls: list[dict[str, object]],
    ) -> None:
        self.responses = responses
        self.calls = calls

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> FakeHttpResponse:
        self.calls.append({"url": url, "method": "POST", **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, FakeHttpResponse)
        return response

    async def get(self, url: str, **kwargs: object) -> FakeHttpResponse:
        self.calls.append({"url": url, "method": "GET", **kwargs})
        return self.responses.pop(0)
