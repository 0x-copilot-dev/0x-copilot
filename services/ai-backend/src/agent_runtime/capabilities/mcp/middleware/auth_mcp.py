"""Model-facing tool that requests user authentication for an MCP server."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.capabilities.mcp.cards import McpLoadErrorCode, McpLoadResult
from agent_runtime.capabilities.mcp.constants import Keys, Messages, Values


class McpAuthSessionCreator(Protocol):
    """Creates MCP auth sessions without returning any tokens."""

    def create_auth_session(
        self,
        *,
        server_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> "McpAuthSession":
        """Return a safe user-facing auth URL for a registered MCP server."""


class McpAuthSession(RuntimeContract):
    server_id: str
    server_name: str
    display_name: str
    auth_url: str
    expires_at: datetime


class AuthMcpInput(RuntimeContract):
    server_name: str = Field(min_length=1)
    server_id: str | None = None


@dataclass(frozen=True)
class AuthMcpTool:
    """Small adapter that can be wrapped by LangChain tool primitives."""

    auth_session_creator: McpAuthSessionCreator
    runtime_context: AgentRuntimeContext
    name: str = Values.ToolName.AUTH_MCP
    description: str = Messages.Middleware.AUTH_MCP_TOOL_DESCRIPTION

    async def ainvoke(self, raw_input: AuthMcpInput | Mapping[str, Any] | str) -> dict[str, Any]:
        parsed_input = AuthMcpInputParser.parse(raw_input, self.runtime_context.trace_id)
        if isinstance(parsed_input, McpLoadResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)
        session = self.auth_session_creator.create_auth_session(
            server_id=parsed_input.server_id or parsed_input.server_name,
            runtime_context=self.runtime_context,
        )
        return {
            "api_event_type": "mcp_auth_required",
            "event_type": "mcp_auth_required",
            "server_id": session.server_id,
            "server_name": session.server_name,
            "display_name": session.display_name,
            "auth_url": session.auth_url,
            "expires_at": session.expires_at.isoformat(),
            "message": f"Authenticate {session.display_name} to continue using this MCP server.",
        }

    async def __call__(self, raw_input: AuthMcpInput | Mapping[str, Any] | str) -> dict[str, Any]:
        return await self.ainvoke(raw_input)


class AuthMcpInputParser:
    """Parser for untrusted auth_mcp tool input."""

    @classmethod
    def parse(
        cls,
        raw_input: AuthMcpInput | Mapping[str, Any] | str,
        correlation_id: str,
    ) -> AuthMcpInput | McpLoadResult:
        if isinstance(raw_input, AuthMcpInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {Keys.Field.SERVER_NAME: raw_input}
        try:
            return AuthMcpInput.model_validate(raw_input)
        except ValidationError:
            return McpLoadResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                correlation_id=correlation_id,
            )
