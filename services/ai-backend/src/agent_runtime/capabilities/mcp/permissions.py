"""Shared authorization helpers for dynamic MCP loading."""

from __future__ import annotations

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ConnectorAccessMode,
)
from agent_runtime.capabilities.mcp.cards import McpServerCard, McpServerHealth


class McpPermissionPolicy:
    """Authorization helpers for MCP card visibility and loading."""

    VISIBLE_HEALTH_STATES = frozenset(
        {
            McpServerHealth.HEALTHY,
            McpServerHealth.DEGRADED,
        }
    )

    @classmethod
    def is_server_card_visible(
        cls,
        context: AgentRuntimeContext,
        card: McpServerCard,
    ) -> bool:
        """Return whether a compact MCP server card may be shown to the model."""

        if not card.enabled or card.health not in cls.VISIBLE_HEALTH_STATES:
            return False
        return cls.is_server_card_authorized(context, card)

    @classmethod
    def is_server_card_authorized(
        cls,
        context: AgentRuntimeContext,
        card: McpServerCard,
    ) -> bool:
        """Return whether the runtime context may load this MCP server.

        Paused ``server_id``s are denied so per-chat connector toggles
        block both card listing and server loading. Cards without a
        ``server_id`` (deployment-level cards) skip the paused-id gate.

        An ``off`` connector access mode (PRD-06 D3b) is denied on the same
        ``server_id`` key — the frozen-at-run-create mirror of the
        authoritative backend gate, so a stale tool reference from an earlier
        turn can't reach a connector the user switched off.
        """

        if card.server_id is not None and card.server_id in context.paused_connectors:
            return False
        if (
            card.server_id is not None
            and context.connector_access_modes.get(card.server_id)
            == ConnectorAccessMode.OFF
        ):
            return False
        if card.allowed_org_ids and context.org_id not in card.allowed_org_ids:
            return False
        if card.allowed_user_ids and context.user_id not in card.allowed_user_ids:
            return False
        return card.required_scopes.issubset(context.permission_scopes)
