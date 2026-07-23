"""Action classification capability (PRD-C1).

Layered, fail-closed read/write classification for MCP tool calls
(curated catalog -> protocol annotations as untrusted hints -> default =
write) plus the effective-policy resolver that composes the global Approval
Policy with the per-connector write-policy override.

This is a **capability** (it classifies MCP tool calls). It imports the
importable ``server_slug`` / ``tool_slug`` from ``capabilities/surfaces`` and
the ``ToolUsePolicy*`` types from ``capabilities/tools``; it is imported *by*
the ``surfaces_v2`` ledger emitter (the one-way ``surfaces_v2 -> capabilities``
direction — no cycle). The classifier home is HERE — do not relocate it
(PRD-C2 references ``capabilities/actions/classifier.py`` by path).
"""

from __future__ import annotations

from agent_runtime.capabilities.actions.catalog import ACTION_CATALOG, ActionCatalog
from agent_runtime.capabilities.actions.classifier import (
    ACTION_CLASSIFIER,
    ActionClassifier,
)
from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
    ClassificationBasis,
    ClassifiedAction,
    ConnectorWritePolicy,
    EffectiveActionPolicy,
)
from agent_runtime.capabilities.actions.policy import (
    ConnectorWritePolicyOverrides,
    EffectiveActionPolicyResolver,
)

__all__ = [
    "ACTION_CATALOG",
    "ACTION_CLASSIFIER",
    "ActionCatalog",
    "ActionClass",
    "ActionClassifier",
    "CatalogActionKind",
    "ClassificationBasis",
    "ClassifiedAction",
    "ConnectorWritePolicy",
    "ConnectorWritePolicyOverrides",
    "EffectiveActionPolicy",
    "EffectiveActionPolicyResolver",
]
