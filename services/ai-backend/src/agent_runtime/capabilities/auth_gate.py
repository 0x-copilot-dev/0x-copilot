"""Auth-gate for connectors: answers "is this connector reachable right now?" before a draft send or approval dispatch."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agent_runtime.capabilities.tools.cards import ToolCard


class CapabilityAuthOutcome(StrEnum):
    """Single discriminator for the gate's verdict."""

    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN_CAPABILITY = "unknown_capability"
    WORKSPACE_DISABLED = "workspace_disabled"


@dataclass(frozen=True)
class CapabilityAuthCheck:
    """Result of a :meth:`CapabilityAuthGate.check` call."""

    outcome: CapabilityAuthOutcome
    mcp_server_id: str | None = None
    safe_message: str | None = None


class _ToolRegistryLike(Protocol):
    """Structural view of the built-in tool registry consumed by the gate."""

    def list_available_tools(self, context: object) -> tuple[ToolCard, ...]: ...


class _McpServerLike(Protocol):
    """Minimal fields the gate reads from an MCP server entry.

    Using a narrow structural protocol keeps unit-test stubs small; the gate
    never validates the full ``McpServerCard`` shape.
    """

    name: str
    server_id: str | None
    # Compared as ``str(auth_state) == "authenticated"`` to avoid importing
    # ``McpAuthState`` here and creating a hard circular dependency.
    auth_state: object
    enabled: bool


class _McpRegistryLike(Protocol):
    """Structural view of the MCP registry consumed by the gate."""

    async def list_available_servers(
        self, context: object
    ) -> Iterable[_McpServerLike]: ...


class CapabilityAuthGate:
    """Resolve a ``target_connector`` name to an authentication verdict.

    Checks built-in tools first (always authenticated when visible), then
    MCP servers. A single instance is safe to reuse across requests; both
    registries are in-memory caches with their own TTL refresh.
    """

    # String representation of ``McpAuthState.AUTHENTICATED`` — kept as a
    # literal to avoid a hard import cycle between this module and mcp.cards.
    AUTHENTICATED_AUTH_STATE = "authenticated"

    def __init__(
        self,
        *,
        tool_registry: _ToolRegistryLike,
        mcp_registry: _McpRegistryLike,
    ) -> None:
        self._tool_registry = tool_registry
        self._mcp_registry = mcp_registry

    async def check(
        self,
        *,
        target_connector: str,
        runtime_context: object,
    ) -> CapabilityAuthCheck:
        """Return whether ``target_connector`` is reachable for ``runtime_context``."""

        if not target_connector:
            return CapabilityAuthCheck(
                outcome=CapabilityAuthOutcome.UNKNOWN_CAPABILITY,
                safe_message="target_connector is required.",
            )
        # Built-in tools are always reachable once visible — no auth step.
        for tool in self._tool_registry.list_available_tools(runtime_context):
            if tool.name == target_connector:
                return CapabilityAuthCheck(
                    outcome=CapabilityAuthOutcome.AUTHENTICATED,
                )
        # Walk MCP servers for a name match; order within a workspace is
        # non-deterministic, but name uniqueness is enforced by the registry.
        for server in await self._mcp_registry.list_available_servers(runtime_context):
            if server.name != target_connector:
                continue
            if not server.enabled:
                return CapabilityAuthCheck(
                    outcome=CapabilityAuthOutcome.WORKSPACE_DISABLED,
                    mcp_server_id=server.server_id,
                    safe_message="Connector is disabled for this workspace.",
                )
            if str(server.auth_state) == self.AUTHENTICATED_AUTH_STATE:
                return CapabilityAuthCheck(
                    outcome=CapabilityAuthOutcome.AUTHENTICATED,
                    mcp_server_id=server.server_id,
                )
            return CapabilityAuthCheck(
                outcome=CapabilityAuthOutcome.NOT_AUTHENTICATED,
                mcp_server_id=server.server_id,
                safe_message="Connector requires authentication for this user.",
            )
        return CapabilityAuthCheck(
            outcome=CapabilityAuthOutcome.UNKNOWN_CAPABILITY,
            safe_message="Unknown connector for this workspace.",
        )
