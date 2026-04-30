"""Shared authorization helpers for dynamic MCP loading."""

from __future__ import annotations

from enterprise_search_ai.agent.contracts import AgentRuntimeContext
from enterprise_search_ai.mcp.cards import McpServerCard, McpServerHealth


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
        """Return whether the runtime context may load this MCP server."""

        if card.allowed_org_ids and context.org_id not in card.allowed_org_ids:
            return False
        if card.allowed_user_ids and context.user_id not in card.allowed_user_ids:
            return False
        return card.required_scopes.issubset(context.permission_scopes)
