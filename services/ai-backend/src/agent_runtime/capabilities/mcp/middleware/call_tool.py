"""Model-facing tool that invokes a selected MCP tool after discovery."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.mcp.cards import (
    McpLoadError,
    McpLoadErrorCode,
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
            output = await asyncio.wait_for(
                client.call_tool(
                    tool_name=parsed_input.tool_name,
                    arguments=parsed_input.arguments,
                ),
                timeout=self.loader.timeout_seconds,
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
