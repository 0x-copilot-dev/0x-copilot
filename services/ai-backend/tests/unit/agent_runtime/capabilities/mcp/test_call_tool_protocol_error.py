"""Integration-style unit tests for :class:`CallMcpTool` protocol-error handling."""

from __future__ import annotations

import asyncio

from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpLoader,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


_SERVER = "linear"
_TOOL = "list_issues"


class CallMcpToolProtocolErrorMixin(DynamicMcpLoadingMixin):
    """Helpers for asserting on the failure shape returned by the dispatcher."""

    class TestValues(DynamicMcpLoadingMixin.TestValues):
        ERROR_TEXT = (
            "MCP error -32602: Invalid arguments for tool list_issues: "
            "unrecognized_keys: ['parameters']"
        )

    def make_tool_with_protocol_error(
        self,
        runtime_context: AgentRuntimeContext,
    ) -> CallMcpTool:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=_SERVER),),
            clients={
                _SERVER: self.FakeMcpClient(
                    tools=(self.make_tool(name=_TOOL),),
                    resources=(),
                    tool_outputs={
                        _TOOL: {
                            "content": [
                                {"type": "text", "text": self.TestValues.ERROR_TEXT}
                            ],
                            "isError": True,
                        }
                    },
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        return CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context,
        )


class TestCallMcpToolProtocolError(CallMcpToolProtocolErrorMixin):
    def test_classifies_is_error_response_as_protocol_failure(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        tool = self.make_tool_with_protocol_error(runtime_context_admin)

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": _SERVER,
                    "tool_name": _TOOL,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert result["output"]["status"] == "held"
        assert result["server_name"] == _SERVER
        assert result["tool_name"] == _TOOL

    def test_inner_error_text_reaches_failure_result(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        tool = self.make_tool_with_protocol_error(runtime_context_admin)

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": _SERVER,
                    "tool_name": _TOOL,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert result["output"]["status"] == "held"

    def test_happy_path_remains_a_success_when_no_is_error_flag(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=_SERVER),),
            clients={
                _SERVER: self.FakeMcpClient(
                    tools=(self.make_tool(name=_TOOL),),
                    resources=(),
                    tool_outputs={
                        _TOOL: {"content": [{"type": "text", "text": "found tasks"}]}
                    },
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": _SERVER,
                    "tool_name": _TOOL,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert "error" not in result
        assert result["output"]["status"] == "held"

    def test_wrapped_parameters_call_succeeds_after_normalization(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=_SERVER),),
            clients={
                _SERVER: self.FakeMcpClient(
                    tools=(self.make_tool(name=_TOOL),),
                    resources=(),
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        tool = CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context_admin,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": _SERVER,
                    "tool_name": _TOOL,
                    "parameters": {"query": "tasks"},
                }
            )
        )

        assert "error" not in result
        assert result["output"]["status"] == "held"
