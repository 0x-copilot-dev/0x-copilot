"""Integration-style unit tests for :class:`CallMcpTool` protocol-error handling."""

from __future__ import annotations

import asyncio

from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpLoadErrorCode,
    McpLoader,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


class CallMcpToolProtocolErrorMixin(DynamicMcpLoadingMixin):
    """Helpers for asserting on the failure shape returned by the dispatcher."""

    class TestValues(DynamicMcpLoadingMixin.TestValues):
        ERROR_TEXT = (
            "MCP error -32602: Invalid arguments for tool drive_search: "
            "unrecognized_keys: ['parameters']"
        )

    def make_tool_with_protocol_error(
        self,
        runtime_context: AgentRuntimeContext,
    ) -> CallMcpTool:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                    tool_outputs={
                        self.TestValues.Names.DRIVE_SEARCH: {
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
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert "error" in result
        assert result["error"]["code"] == McpLoadErrorCode.MCP_PROTOCOL_ERROR.value
        assert result["error"]["retryable"] is False
        assert result["server_name"] == self.TestValues.Names.DRIVE_MCP
        assert result["tool_name"] == self.TestValues.Names.DRIVE_SEARCH

    def test_inner_error_text_reaches_failure_result(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        tool = self.make_tool_with_protocol_error(runtime_context_admin)

        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        # Safe summary carries the underlying server message verbatim so the
        # model can read why the call failed and self-correct.
        assert self.TestValues.ERROR_TEXT in result["error"]["safe_message"]
        # Full structured envelope is preserved on the failure result so
        # downstream consumers can inspect content blocks and the isError flag.
        assert result["output"]["isError"] is True
        assert result["output"]["content"][0]["text"] == self.TestValues.ERROR_TEXT

    def test_happy_path_remains_a_success_when_no_is_error_flag(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                    tool_outputs={
                        self.TestValues.Names.DRIVE_SEARCH: {
                            "content": [{"type": "text", "text": "found tasks"}]
                        }
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
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "arguments": {"query": "tasks"},
                }
            )
        )

        assert "error" not in result
        assert result["output"]["content"][0]["text"] == "found tasks"

    def test_wrapped_parameters_call_succeeds_after_normalization(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
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
                    "server_name": self.TestValues.Names.DRIVE_MCP,
                    "tool_name": self.TestValues.Names.DRIVE_SEARCH,
                    "parameters": {"query": "tasks"},
                }
            )
        )

        # The normalized call reaches the server with unwrapped arguments; the
        # fake echoes the dict it received, proving ``parameters`` was unwrapped.
        assert "error" not in result
        assert (
            result["output"]["content"][0]["text"]
            == "called drive_search with {'query': 'tasks'}"
        )
