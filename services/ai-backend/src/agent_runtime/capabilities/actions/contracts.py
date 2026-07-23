"""Classification + effective-policy contracts (PRD-C1, all NEW).

These enums intentionally DUPLICATE the value set of the wire enums in
``surfaces_v2/ledger_models`` (``ActionClass`` / ``ClassificationBasis``).
They cannot import them: this is a ``capabilities`` package and ``surfaces_v2``
imports *from* capabilities (one-way). The values are byte-identical, so the
emitter passes ``classified.action_class.value`` / ``.basis.value`` straight
onto the ledger payload without translation.
"""

from __future__ import annotations

from enum import StrEnum

from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
)
from agent_runtime.execution.contracts import RuntimeContract


class ActionClass(StrEnum):
    """Read/write classification of an MCP tool call.

    ``UNKNOWN`` is a legal wire value (SDR §5) but the classifier NEVER emits
    it — fail-closed collapses an unclassifiable op to ``WRITE``. It exists so
    the type mirrors the wire vocabulary exactly.
    """

    READ = "read"
    WRITE = "write"
    UNKNOWN = "unknown"


class ClassificationBasis(StrEnum):
    """Which rung of the ladder produced the classification."""

    CATALOG = "catalog"
    ANNOTATION = "annotation"
    DEFAULT = "default"


class CatalogActionKind(StrEnum):
    """What a curated catalog file may declare per operation."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ConnectorWritePolicy(StrEnum):
    """Per-connector approval-posture override (FR-B4).

    Value-identical to the backend ``ConnectorWritePolicy`` (stored on the
    ``connectors`` row) and to ``packages/api-types``. Absence of an override
    is represented by the connector NOT appearing in the override map, never a
    third member.
    """

    ASK_FIRST = "ask_first"
    ALLOW_ALWAYS = "allow_always"


class ClassifiedAction(RuntimeContract):
    """The classifier's verdict for one ``(connector, op)`` call."""

    connector: str  # server_slug-normalized
    op: str  # tool_slug-normalized
    action_class: ActionClass  # READ or WRITE (never UNKNOWN — see above)
    basis: ClassificationBasis
    catalog_kind: CatalogActionKind | None = None  # set iff basis == CATALOG


class EffectiveActionPolicy(RuntimeContract):
    """The resolved hold/auto decision for a classified action.

    Composes the classification with the global Approval Policy snapshot and
    the per-connector override. ``hold`` is the single question the gate asks;
    ``bypass`` records that an ``allow_always`` override downgraded an
    otherwise-held write.
    """

    classified: ClassifiedAction
    policy_kind: ToolUsePolicyKind  # axis used: read | write | destructive
    mode: ToolUsePolicyMode  # auto | ask | require | block (post-override)
    hold: bool  # True unless mode == AUTO
    bypass: bool  # True iff allow_always downgraded ask -> auto


__all__ = [
    "ActionClass",
    "CatalogActionKind",
    "ClassificationBasis",
    "ClassifiedAction",
    "ConnectorWritePolicy",
    "EffectiveActionPolicy",
]
