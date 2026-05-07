"""Shared authorization helpers for dynamic MCP loading."""

from __future__ import annotations

from agent_runtime.execution.contracts import AgentRuntimeContext
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

        PR 4.4.6.2 — paused server_ids are denied here so a connector the
        user toggled off in the per-chat popover is invisible to
        ``list_server_cards`` AND blocked from ``load_server`` for the
        duration of the run. The check is by ``server_id`` (the same key
        the conversation column writes); cards without a ``server_id``
        (deployment-level cards, not user-installed) skip the gate.
        """

        if card.server_id is not None and card.server_id in context.paused_connectors:
            return False
        if card.allowed_org_ids and context.org_id not in card.allowed_org_ids:
            return False
        if card.allowed_user_ids and context.user_id not in card.allowed_user_ids:
            return False
        return card.required_scopes.issubset(context.permission_scopes)
