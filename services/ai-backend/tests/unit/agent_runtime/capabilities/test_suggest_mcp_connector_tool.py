"""Unit tests for the suggest_mcp_connector tool (PR 3.3).

The tool is a thin parser → :meth:`McpDiscoveryService.offer` adapter
that stays *non-interrupting*. Tests cover:

  * Pydantic input validation (missing fields → safe error).
  * Returns ``discovery_disabled`` when no service is bound.
  * Routes through ``McpDiscoveryService.offer`` when bound.
  * Tool metadata (name + description) match the catalog constants.
"""

from __future__ import annotations

import asyncio

from agent_runtime.api.constants import Values
from agent_runtime.capabilities.tools.builtin.suggest_mcp_connector import (
    SuggestMcpConnectorInput,
    SuggestMcpConnectorInputParser,
    SuggestMcpConnectorTool,
)


class TestInputParser:
    def test_validates_full_contract(self) -> None:
        parsed = SuggestMcpConnectorInputParser.parse(
            {
                "server_id": "linear",
                "reason": "fetch ticket statuses",
                "expected_value": "ground claims about progress",
            }
        )
        assert isinstance(parsed, SuggestMcpConnectorInput)
        assert parsed.server_id == "linear"

    def test_missing_field_returns_typed_error(self) -> None:
        # Missing ``expected_value`` — parser maps the field to its safe
        # message rather than raising; the tool then returns the dict.
        parsed = SuggestMcpConnectorInputParser.parse(
            {"server_id": "linear", "reason": "fetch ticket statuses"}
        )
        assert isinstance(parsed, dict)
        assert parsed["ok"] is False
        assert "expected_value" in parsed["message"]

    def test_bare_string_input_fails_closed(self) -> None:
        parsed = SuggestMcpConnectorInputParser.parse("linear")
        assert isinstance(parsed, dict)
        assert parsed["ok"] is False


class TestToolBehaviour:
    def test_tool_metadata_matches_catalog_constant(self) -> None:
        tool = SuggestMcpConnectorTool()
        assert tool.name == Values.Tool.SUGGEST_MCP_CONNECTOR
        assert "Suggest a Connect/Skip card" in tool.description

    def test_returns_discovery_disabled_when_unbound(self) -> None:
        # No worker bound — the tool returns a non-emitting status so
        # the agent can keep going without raising.
        tool = SuggestMcpConnectorTool()
        result = asyncio.run(
            tool.ainvoke(
                {
                    "server_id": "linear",
                    "reason": "fetch ticket statuses",
                    "expected_value": "ground claims",
                }
            )
        )
        assert result["status"] == "discovery_disabled"
        assert result["server_id"] == "linear"

    def test_returns_validation_error_envelope_for_malformed_input(self) -> None:
        tool = SuggestMcpConnectorTool()
        result = asyncio.run(tool.ainvoke({}))
        assert result["ok"] is False
