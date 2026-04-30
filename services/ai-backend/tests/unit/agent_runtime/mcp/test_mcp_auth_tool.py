from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from enterprise_service_contracts.headers import ORG_HEADER, SERVICE_TOKEN_HEADER, USER_HEADER

from agent_runtime.capabilities.mcp.backend_provider import BackendMcpServiceAuth
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.mcp import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.middleware.auth_mcp import AuthMcpTool, McpAuthSession


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

    result = asyncio.run(tool.ainvoke({"server_name": "drive_mcp", "server_id": "server_123"}))

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
