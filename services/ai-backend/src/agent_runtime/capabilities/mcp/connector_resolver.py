"""Native ``tool_name → server_slug`` resolver for per-tool MCP (migration P2-1).

Consumed at both ends: the worker's stream seams fall back to it (P2-6), and the
P2-8 registration flip publishes one per run — but only when
``MCP_PER_TOOL_ENABLED`` is on, so with the flag off no run has a resolver and
the seams resolve exactly as they did.

Under the legacy single-gateway design every MCP call is one ``call_mcp_tool``
dispatch, and the connector that hosts it is recovered by *unwrapping* the nested
``args.server_name`` — see
:meth:`agent_runtime.capabilities.mcp.dispatcher.McpDispatcherUnwrap.effective_server_name`.
Under per-tool registration (P2) each real MCP tool is its own ``BaseTool`` whose
name IS the user-meaningful tool name, so there is nothing to unwrap: the
connector is recovered by a plain lookup in a map built at registration time — a
*native* step.

:class:`ToolConnectorResolver` is that map, and the exact drop-in replacement for
``McpDispatcherUnwrap.effective_server_name`` at the semantic level: a **known**
tool name resolves to its owning ``server_slug``; an **unknown** (or blank) name
resolves to ``None`` — mirroring the dispatcher helper returning ``None`` for any
payload it does not recognise as an MCP call.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


class ToolConnectorResolver:
    """A built-at-registration ``tool_name → server_slug`` map, queried per event.

    Constructed once from the ``{tool_name: server_slug}`` pairs the per-tool
    source publishes (P2-3 / P2-8), then queried by :meth:`server_for`. The map is
    copied into a read-only :class:`~types.MappingProxyType` at construction, so a
    published resolver cannot be mutated by the caller's later edits to the source
    mapping nor by a downstream holder — the registration snapshot is the whole
    truth. Blank tool names or blank slugs are dropped on the way in: an entry
    that cannot name a tool or a connector could only ever mis-resolve.
    """

    __slots__ = ("_by_tool_name",)

    def __init__(self, tool_to_server: Mapping[str, str]) -> None:
        """Snapshot ``tool_to_server`` into an immutable, blank-pruned map."""

        self._by_tool_name: Mapping[str, str] = MappingProxyType(
            {
                name.strip(): server.strip()
                for name, server in tool_to_server.items()
                if name.strip() and server.strip()
            }
        )

    def server_for(self, tool_name: str) -> str | None:
        """Return the owning ``server_slug`` for ``tool_name``, else ``None``.

        The per-tool-world semantics of
        ``McpDispatcherUnwrap.effective_server_name``: a registered tool name maps
        to its connector; a blank or unregistered name is not an MCP-hosted call
        here and resolves to ``None`` (the caller then treats it as a native,
        connector-less step, exactly as the legacy helper's ``None`` did).
        """

        if not tool_name or not tool_name.strip():
            return None
        return self._by_tool_name.get(tool_name.strip())

    @property
    def tool_names(self) -> frozenset[str]:
        """The registered tool names — the resolver's known keys (introspection)."""

        return frozenset(self._by_tool_name)


__all__ = ["ToolConnectorResolver"]
