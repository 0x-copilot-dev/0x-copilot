"""Helper for resolving the user-meaningful tool / server name from a
``call_mcp_tool`` dispatcher event.

All MCP tool invocations flow through a single dispatcher tool whose name is
:data:`agent_runtime.capabilities.mcp.constants.Values.ToolName.CALL_MCP_TOOL`.
Stream events therefore carry ``payload.tool_name = "call_mcp_tool"`` and
nest the *actual* MCP tool name (and server) inside ``payload.args``.

Every consumer that needs the inner name (event projector, presentation
generator, future renderers) MUST delegate here so the dispatcher-unwrap
logic stays in one place. Two consumers used to recompute the unwrap
independently — one of them silently omitted the unwrap, producing
"Calling call_mcp_tool" rows on every MCP tool stream. Centralising
the helper removes that drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.capabilities.mcp.constants import Keys as McpKeys
from agent_runtime.capabilities.mcp.constants import Values as McpValues


class McpDispatcherUnwrap:
    """Resolve the user-meaningful tool / server name for stream-event payloads.

    Stream event payloads come from two distinct emitters:

    1. Regular tools — ``payload.tool_name`` IS the user-meaningful name.
    2. The MCP dispatcher (:attr:`McpValues.ToolName.CALL_MCP_TOOL`) —
       ``payload.tool_name`` is the dispatcher's own name; the real MCP tool
       name and server name live nested inside ``payload.args``.

    Use :meth:`effective_tool_name` to project the user-meaningful name for
    rendering (display titles, summaries, template lookup, etc.) and
    :meth:`effective_server_name` to surface the connector that hosts a
    dispatcher call. Both methods accept any read-only mapping shape, so
    callers can pass raw event payloads, validated Pydantic dumps, or
    ``JsonObject`` dicts interchangeably.
    """

    # The dispatcher's outer JSON shape nests the call's actual arguments
    # under this top-level key. It's part of the dispatcher tool's own
    # schema (not a general payload field), so the constant lives here
    # rather than in :class:`McpKeys.Field`.
    _ARGS_WRAPPER_KEY = "args"

    @classmethod
    def effective_tool_name(cls, payload: Mapping[str, Any]) -> str | None:
        """Return the user-meaningful tool name for a stream-event payload.

        * Returns ``None`` when ``payload.tool_name`` is missing, blank, or
          non-string — i.e. the event carries no tool identity at all.
        * Returns ``payload.tool_name`` verbatim for every non-dispatcher event.
        * For dispatcher events (``tool_name == "call_mcp_tool"``):
            * Returns the trimmed string at ``payload.args.tool_name`` when
              present, so renderers show the underlying tool
              (e.g. ``"list_issues"`` instead of ``"call_mcp_tool"``).
            * Returns the dispatcher name itself as a fallback when the
              args have not yet streamed in (``tool_call_started``) or
              the inner value is missing / non-string. ``"Calling
              call_mcp_tool"`` is informative; ``"Action connector"`` is not.
        """

        tool_name = payload.get(McpKeys.Field.TOOL_NAME)
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        name = tool_name.strip()
        if name != McpValues.ToolName.CALL_MCP_TOOL:
            return name
        return cls._inner_tool_name(payload) or name

    @classmethod
    def effective_server_name(cls, payload: Mapping[str, Any]) -> str | None:
        """Return the MCP server name for a dispatcher event, else ``None``.

        Only dispatcher events carry a meaningful server name (regular
        tools have no concept of a hosting server). Returns the trimmed
        ``payload.args.server_name`` when the event is a dispatcher event
        and the inner mapping carries a non-blank string under that key.
        """

        tool_name = payload.get(McpKeys.Field.TOOL_NAME)
        if (
            not isinstance(tool_name, str)
            or tool_name.strip() != McpValues.ToolName.CALL_MCP_TOOL
        ):
            return None
        args = payload.get(cls._ARGS_WRAPPER_KEY)
        if not isinstance(args, Mapping):
            return None
        server_name = args.get(McpKeys.Field.SERVER_NAME)
        if not isinstance(server_name, str) or not server_name.strip():
            return None
        return server_name.strip()

    @classmethod
    def _inner_tool_name(cls, payload: Mapping[str, Any]) -> str | None:
        """Return the trimmed inner ``args.tool_name`` for a dispatcher event.

        Returns ``None`` (so the public helper falls back to the dispatcher
        name) when ``args`` is missing, isn't a mapping, omits ``tool_name``,
        or carries a non-string / blank value there.
        """

        args = payload.get(cls._ARGS_WRAPPER_KEY)
        if not isinstance(args, Mapping):
            return None
        inner = args.get(McpKeys.Field.TOOL_NAME)
        if not isinstance(inner, str) or not inner.strip():
            return None
        return inner.strip()
