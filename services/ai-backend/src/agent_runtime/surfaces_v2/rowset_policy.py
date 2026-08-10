"""Adapter binding PRD-C1's policy resolver to the row-set stager (PRD-D3, FR-C8).

The pure :class:`~agent_runtime.surfaces_v2.staging.WriteStager` asks one
question at the end of ``stage_rowset``: does an allow-always connector policy
auto-apply this ``(connector, op)``? This adapter answers it by classifying the
op (fail-closed to ``write``) and composing PRD-C1's
:class:`EffectiveActionPolicyResolver` — returning its ``bypass`` flag. Pure and
total: any surprise degrades to ``False`` (ask-first), never raises into staging,
and agent pre-holds are NEVER consulted here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent_runtime.capabilities.actions.classifier import ACTION_CLASSIFIER
from agent_runtime.capabilities.actions.policy import EffectiveActionPolicyResolver
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RowsetPolicyResolver:
    """Concrete ``WritePolicyResolverPort`` over the C1 resolver + classifier."""

    resolver: EffectiveActionPolicyResolver

    def bypass_for(self, *, connector: str, op: str) -> bool:
        """Return ``True`` iff an allow-always override auto-applies this op."""

        try:
            annotations = McpToolAnnotationsRegistry.get(connector, op)
            classified = ACTION_CLASSIFIER.classify(
                server=connector, tool=op, annotations=annotations
            )
            return self.resolver.resolve(classified).bypass
        except Exception:  # noqa: BLE001 — never fail staging on a policy lookup.
            _LOGGER.warning("[surfaces_v2] rowset_policy.resolve_raised", exc_info=True)
            return False


__all__ = ["RowsetPolicyResolver"]
