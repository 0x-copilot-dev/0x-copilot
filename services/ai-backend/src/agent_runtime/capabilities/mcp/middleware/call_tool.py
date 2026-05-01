"""Model-facing tool that invokes a selected MCP tool after discovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpLoadError,
    McpLoadErrorCode,
    McpRiskLevel,
    McpToolDescriptor,
    McpToolCallRequest,
    McpToolCallResult,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpTimeoutError,
)
from agent_runtime.capabilities.mcp.constants import Messages, Values
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry


@dataclass(frozen=True)
class CallMcpTool:
    """Invoke a tool from one previously discovered MCP server."""

    registry: DynamicMcpRegistry
    loader: McpLoader
    runtime_context: AgentRuntimeContext
    name: str = Values.ToolName.CALL_MCP_TOOL
    description: str = Messages.Middleware.CALL_MCP_TOOL_DESCRIPTION

    async def ainvoke(
        self,
        raw_input: McpToolCallRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        parsed_input = CallMcpToolInputParser.parse(
            raw_input,
            self.runtime_context.trace_id,
        )
        if isinstance(parsed_input, McpToolCallResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)

        loaded = await self.loader.load_server_by_name(
            server_name=parsed_input.server_name,
            runtime_context=self.runtime_context,
        )
        if loaded.error is not None:
            return McpToolCallResult.fail_from_load_error(
                loaded.error,
                tool_name=parsed_input.tool_name,
            ).model_dump(mode="json", exclude_none=True)

        loaded_server = loaded.loaded_server
        if loaded_server is None:
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        selected_tool = self._selected_tool(loaded_server, parsed_input.tool_name)
        if selected_tool is None:
            return McpToolCallResult.fail(
                McpLoadErrorCode.UNKNOWN_TOOL,
                Messages.Registry.REQUESTED_TOOL_UNKNOWN,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        approval = McpToolApprovalPolicy.approval_payload(
            runtime_context=self.runtime_context,
            loaded_server=loaded_server,
            tool=selected_tool,
            arguments=parsed_input.arguments,
        )
        if approval is not None:
            return approval

        resolution = self.registry.resolve_server(parsed_input.server_name)
        if isinstance(resolution, McpLoadError):
            return McpToolCallResult.fail(
                resolution.code,
                resolution.safe_message,
                retryable=resolution.retryable,
                server_name=resolution.server_name or parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        try:
            client = resolution.provider.create_client(resolution.card)
            output = await client.call_tool(
                tool_name=parsed_input.tool_name,
                arguments=parsed_input.arguments,
            )
        except (McpTimeoutError, TimeoutError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.TIMEOUT,
                Messages.Loader.TIMEOUT,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpAuthError, PermissionError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpConnectionError, ConnectionError):
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.CONNECTION_FAILED,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)
        except (McpClientError, Exception):
            return McpToolCallResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        return McpToolCallResult.ok(
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            output=output,
        ).model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _selected_tool(
        loaded_server: LoadedMcpServer,
        tool_name: str,
    ) -> McpToolDescriptor | None:
        return next(
            (tool for tool in loaded_server.tools if tool.name == tool_name),
            None,
        )

    async def __call__(
        self,
        raw_input: McpToolCallRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self.ainvoke(raw_input)


class CallMcpToolInputParser:
    """Parser for untrusted generic MCP tool invocation input."""

    @classmethod
    def parse(
        cls,
        raw_input: McpToolCallRequest | Mapping[str, Any],
        correlation_id: str,
    ) -> McpToolCallRequest | McpToolCallResult:
        if isinstance(raw_input, McpToolCallRequest):
            return raw_input
        try:
            return McpToolCallRequest.model_validate(raw_input)
        except ValidationError:
            return McpToolCallResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                correlation_id=correlation_id,
            )


class McpToolApprovalPolicy:
    """Build model-visible approval interrupts for MCP tool execution."""

    @classmethod
    def approval_payload(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        loaded_server: LoadedMcpServer,
        tool: McpToolDescriptor,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if cls._has_runtime_grant(
            runtime_context, loaded_server.server_card.name, tool
        ):
            return None

        server = loaded_server.server_card
        display_name = server.display_name or server.name
        risk_level = cls._risk_value(tool.risk_level)
        return {
            "api_event_type": "approval_requested",
            "event_type": "approval_requested",
            "approval_id": uuid4().hex,
            "approval_kind": "mcp_tool",
            "server_name": server.name,
            "server_id": server.server_id,
            "display_name": display_name,
            "tool_name": tool.name,
            "arguments": dict(arguments),
            "risk_level": risk_level,
            "read_only": risk_level in {"low", "medium"},
            "message": f"Approve {display_name} to run {tool.name}.",
            "reason": tool.description,
            "status": "pending",
            "grant_options": list(cls._grant_options(tool)),
        }

    @classmethod
    def _has_runtime_grant(
        cls,
        runtime_context: AgentRuntimeContext,
        server_name: str,
        tool: McpToolDescriptor,
    ) -> bool:
        grants = runtime_context.trace_metadata.get("mcp_approval_grants")
        if not isinstance(grants, list):
            return False
        exact = f"{server_name}:{tool.name}"
        server_wide = f"{server_name}:*"
        return exact in grants or (
            cls._risk_value(tool.risk_level) in {"low", "medium"}
            and server_wide in grants
        )

    @classmethod
    def _grant_options(cls, tool: McpToolDescriptor) -> tuple[str, ...]:
        if tool.risk_level in {McpRiskLevel.HIGH, McpRiskLevel.CRITICAL}:
            return ("allow_once",)
        return ("allow_once", "always_allow_tool")

    @staticmethod
    def _risk_value(risk_level: McpRiskLevel | str) -> str:
        return getattr(risk_level, "value", str(risk_level))
