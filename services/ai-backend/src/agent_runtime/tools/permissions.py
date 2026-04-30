"""Shared authorization helpers for dynamic tool loading."""

from __future__ import annotations

from agent_runtime.agent.contracts import AgentRuntimeContext
from agent_runtime.tools.cards import ToolCard, ToolPermissionPolicy


class ToolPermissionChecker:
    """Shared permission checks for tool cards and loaded specs."""

    @classmethod
    def is_card_authorized(cls, context: AgentRuntimeContext, card: ToolCard) -> bool:
        """Return whether a compact card may be shown to the model."""

        if not card.enabled:
            return False
        return cls.has_scopes_for_connector(
            context,
            connector=card.connector,
            required_scopes=card.required_scopes,
        )

    @classmethod
    def is_policy_authorized(
        cls,
        context: AgentRuntimeContext,
        policy: ToolPermissionPolicy,
    ) -> bool:
        """Return whether the runtime may load the full tool spec now."""

        return cls.has_scopes_for_connector(
            context,
            connector=policy.connector,
            required_scopes=policy.required_scopes,
        )

    @classmethod
    def has_scopes_for_connector(
        cls,
        context: AgentRuntimeContext,
        *,
        connector: str,
        required_scopes: frozenset[str],
    ) -> bool:
        if not required_scopes.issubset(context.permission_scopes):
            return False

        connector_scopes = context.connector_scopes.get(connector)
        if connector_scopes is None:
            return False
        return required_scopes.issubset(connector_scopes)
