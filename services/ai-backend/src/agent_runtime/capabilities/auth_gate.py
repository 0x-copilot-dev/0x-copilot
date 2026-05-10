"""Connector authentication pre-check for the Workspace-pane draft send flow.

The :class:`CapabilityAuthGate` answers one question: "is ``target_connector``
reachable for this runtime context — right now?" It's used by

- :class:`DraftService.send` as a pre-check, so non-authenticated targets
  return ``409 connector_auth_required`` *before* any draft row mutation;
- the approval-resolution path as a re-check at dispatch time, so a connector
  that was revoked between API send and approval resolution doesn't quietly
  succeed.

The gate is dependency-free beyond the registries it wraps; both registries
are in-memory caches refreshed on the existing TTL by the run handler. One
``check`` call is sub-µs in the hot path.
"""

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
    def list_available_tools(self, context: object) -> tuple[ToolCard, ...]: ...


class _McpServerLike(Protocol):
    """Structural view of one entry returned by ``mcp_registry.list_available_servers``.

    We only depend on the fields we actually read so unit tests can supply
    minimal stubs without re-validating the full :class:`McpServerCard`.
    """

    name: str
    server_id: str | None
    auth_state: object  # McpAuthState; compared by ``str(value)``
    enabled: bool


class _McpRegistryLike(Protocol):
    async def list_available_servers(
        self, context: object
    ) -> Iterable[_McpServerLike]: ...


class CapabilityAuthGate:
    """Resolve a ``target_connector`` to an authentication verdict."""

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
        """Return an outcome explaining whether the connector is reachable."""

        if not target_connector:
            return CapabilityAuthCheck(
                outcome=CapabilityAuthOutcome.UNKNOWN_CAPABILITY,
                safe_message="target_connector is required.",
            )
        # 1. Is the target a built-in tool the user can already see?
        for tool in self._tool_registry.list_available_tools(runtime_context):
            if tool.name == target_connector:
                return CapabilityAuthCheck(
                    outcome=CapabilityAuthOutcome.AUTHENTICATED,
                )
        # 2. Is it a known MCP server?
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
