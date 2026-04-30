"""Shared authorization helpers for dynamic tool loading."""

from __future__ import annotations

from enterprise_search_ai.agent.contracts import AgentRuntimeContext
from enterprise_search_ai.tools.cards import ToolCard, ToolPermissionPolicy


def is_card_authorized(context: AgentRuntimeContext, card: ToolCard) -> bool:
    """Return whether a compact card may be shown to the model."""

    if not card.enabled:
        return False
    return _has_scopes_for_connector(
        context,
        connector=card.connector,
        required_scopes=card.required_scopes,
    )


def is_policy_authorized(
    context: AgentRuntimeContext,
    policy: ToolPermissionPolicy,
) -> bool:
    """Return whether the runtime may load the full tool spec now."""

    return _has_scopes_for_connector(
        context,
        connector=policy.connector,
        required_scopes=policy.required_scopes,
    )


def _has_scopes_for_connector(
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
