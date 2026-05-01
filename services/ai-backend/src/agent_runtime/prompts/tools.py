"""Model-visible tool descriptions used by runtime capabilities."""

from __future__ import annotations


AUTH_MCP_TOOL_DESCRIPTION = (
    "Request an authorization URL for an MCP server when the user has not "
    "authenticated it yet. Use this only when the server is needed."
)

CALL_MCP_TOOL_DESCRIPTION = (
    "Call a tool from an MCP server after load_mcp_server has returned that "
    "server's validated tool descriptors."
)

LOAD_MCP_SERVER_TOOL_DESCRIPTION = (
    "Load an authorized MCP server by stable name and return validated "
    "tool and resource descriptors."
)

LOAD_TOOL_SPEC_DESCRIPTION = (
    "Load the full schema and instructions for an authorized tool by stable name."
)

LOAD_SKILL_TOOL_DESCRIPTION = (
    "Load the full Markdown for an available Skill by stable skill_name. "
    "Use this only when a compact Skill card is relevant to the user request."
)
