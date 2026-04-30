from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)

from agent_runtime.capabilities.mcp.backend_provider import (
    BackendMcpClient,
    BackendMcpServiceAuth,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.mcp import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.middleware.auth_mcp import (
    AuthMcpTool,
    McpAuthSession,
)


@dataclass(frozen=True)
class FakeAuthSessionCreator:
    def create_auth_session(
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
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
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
    tool = AuthMcpTool(
        auth_session_creator=FakeAuthSessionCreator(),
        runtime_context=runtime_context_admin,
    )

    result = asyncio.run(
        tool.ainvoke({"server_name": "drive_mcp", "server_id": "server_123"})
    )

    assert result["api_event_type"] == "mcp_auth_required"
    assert result["server_id"] == "server_123"
    assert result["display_name"] == "Drive MCP"
    assert "auth.example.com" in result["auth_url"]
    assert "token" not in str(result)


def test_backend_mcp_service_auth_includes_trusted_scope_headers(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "service-token")

    headers = BackendMcpServiceAuth.headers(runtime_context_admin)

    assert headers[SERVICE_TOKEN_HEADER] == "service-token"
    assert headers[ORG_HEADER] == runtime_context_admin.org_id
    assert headers[USER_HEADER] == runtime_context_admin.user_id


def test_backend_mcp_client_loads_tools_through_json_rpc_proxy(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        FakeHttpResponse(
            {
                "server_id": "server_123",
                "url": "https://mcp.example.com/mcp",
                "transport": "http",
                "auth_state": "authenticated",
                "credential_ref": "credential_123",
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

    monkeypatch.setattr(
        "agent_runtime.capabilities.mcp.backend_provider.httpx.AsyncClient",
        lambda timeout: FakeAsyncClient(responses, calls),
    )
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
    )

    tools = asyncio.run(client.list_tools())

    assert tools[0].name == "search_tasks"
    assert tools[0].input_schema == {"type": "object"}
    assert calls[1]["json"]["payload"]["method"] == "initialize"
    assert calls[3]["json"]["payload"]["method"] == "tools/list"


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeAsyncClient:
    def __init__(
        self,
        responses: list[FakeHttpResponse],
        calls: list[dict[str, object]],
    ) -> None:
        self.responses = responses
        self.calls = calls

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> FakeHttpResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)
