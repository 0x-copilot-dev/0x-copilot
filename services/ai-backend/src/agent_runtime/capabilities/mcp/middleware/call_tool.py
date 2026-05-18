"""Model-facing tool that invokes a selected MCP tool after discovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_runtime.capabilities.citation_capturing_tool import _CitationHint
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
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
from agent_runtime.capabilities.mcp.middleware.cite_mcp import (
    CitationProjectingMcpMiddleware,
)
from agent_runtime.capabilities.mcp.outcomes import McpToolCallOutcome
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.execution.contracts import AgentRuntimeContext

_LOGGER = logging.getLogger(__name__)


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
        """Validate input, re-check permissions, call the tool, and annotate with a citation hint."""
        parsed_input = CallMcpToolInputParser.parse(
            raw_input,
            self.runtime_context.trace_id,
        )
        if isinstance(parsed_input, McpToolCallResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)

        resolution = await self.registry.resolve_server(parsed_input.server_name)
        if isinstance(resolution, McpLoadError):
            return McpToolCallResult.fail(
                resolution.code,
                resolution.safe_message,
                retryable=resolution.retryable,
                server_name=resolution.server_name or parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
            ).model_dump(mode="json", exclude_none=True)

        # Defense-in-depth: re-check authorization after registry resolve so a stale
        # tool reference from an earlier turn can't bypass per-chat pausing.
        if not McpPermissionPolicy.is_server_card_authorized(
            self.runtime_context, resolution.card
        ):
            return McpToolCallResult.fail(
                McpLoadErrorCode.PERMISSION_DENIED,
                Messages.Loader.UNAUTHORIZED_SERVER,
                server_name=parsed_input.server_name,
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

        # Project citation sources from the structured output. Best-effort;
        # the original output shape is preserved for JSON consumers.
        await CitationProjectingMcpMiddleware.project(
            connector=parsed_input.server_name,
            tool_call_id=self.runtime_context.trace_id,
            result=output,
        )

        # Classify protocol-level failures per the MCP spec: a successful HTTP
        # response carrying ``isError: true`` is a failure, not a "completed"
        # result. Preserve the full ``output`` envelope on the failure result so
        # the model can read the inner error text and self-correct.
        if McpToolCallOutcome.is_protocol_error(output):
            return McpToolCallResult.fail(
                McpLoadErrorCode.MCP_PROTOCOL_ERROR,
                McpToolCallOutcome.extract_error_text(output),
                retryable=False,
                server_name=parsed_input.server_name,
                tool_name=parsed_input.tool_name,
                correlation_id=self.runtime_context.trace_id,
                output=output,
            ).model_dump(mode="json", exclude_none=True)

        # Allocate a conversation-scoped ordinal bound to tool_call_id so the
        # citation resolver can stamp source_tool_call_id on citation_made events.
        # Best-effort: when no allocator is bound (replay/eval) or no tool_call_id
        # was injected (manual call sites), the output is returned unchanged.
        try:
            allocator = ConversationOrdinalAllocator.active()
            if allocator is None:
                _LOGGER.warning(
                    "[citations] mcp.hint_skipped server=%s tool=%s "
                    "reason=no_allocator_bound",
                    parsed_input.server_name,
                    parsed_input.tool_name,
                )
            elif not parsed_input.tool_call_id:
                _LOGGER.warning(
                    "[citations] mcp.hint_skipped server=%s tool=%s "
                    "reason=no_tool_call_id_injected (replay/eval path)",
                    parsed_input.server_name,
                    parsed_input.tool_name,
                )
            else:
                qualified_tool_name = (
                    f"{parsed_input.server_name}.{parsed_input.tool_name}"
                )
                ordinal = await allocator.allocate_for_tool_call(
                    tool_call_id=parsed_input.tool_call_id,
                    tool_name=qualified_tool_name,
                )
                hinted = _CitationHint.append_to(
                    output,
                    ordinal=ordinal,
                    tool_name=qualified_tool_name,
                )
                if isinstance(hinted, dict):
                    output = hinted
                _LOGGER.info(
                    "[citations] mcp.hint_appended server=%s tool=%s "
                    "ordinal=%d call_id=%s",
                    parsed_input.server_name,
                    parsed_input.tool_name,
                    ordinal,
                    parsed_input.tool_call_id,
                )
        except Exception:  # noqa: BLE001 - best-effort; never break MCP results
            _LOGGER.warning(
                "[citations] mcp.hint_raised server=%s tool=%s",
                parsed_input.server_name,
                parsed_input.tool_name,
                exc_info=True,
            )

        return McpToolCallResult.ok(
            server_name=parsed_input.server_name,
            tool_name=parsed_input.tool_name,
            output=output,
        ).model_dump(mode="json", exclude_none=True)

    async def __call__(
        self,
        raw_input: McpToolCallRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)


class CallMcpToolInputParser:
    """Parser for untrusted generic MCP tool invocation input."""

    @classmethod
    def parse(
        cls,
        raw_input: McpToolCallRequest | Mapping[str, Any],
        correlation_id: str,
    ) -> McpToolCallRequest | McpToolCallResult:
        """Validate ``raw_input`` into a typed request; return a failure result on error."""
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
