"""EffectiveActionPolicyResolver + per-connector override parsing (PRD-C1).

Composes the global Approval Policy snapshot (``ToolUsePolicySnapshot``) with
the per-connector write-policy override into the single hold/auto decision the
gate needs. Pure and total — no I/O, never raises for any input.

**Scope note (C1):** this resolver is built + unit-tested but NOT wired into
runtime holding in C1 — the actual hold/interrupt stays with the existing
``ToolUsePolicyEnforcer``. PRD-C2 (gates) and PRD-D1 (staging) consume it.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    ClassificationBasis,
    CatalogActionKind,
    ClassifiedAction,
    ConnectorWritePolicy,
    EffectiveActionPolicy,
)
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)


class _Keys:
    """Wire keys read out of ``user_policies_json['tool_use']``."""

    TOOL_USE = "tool_use"
    CONNECTOR_WRITE_POLICY = "connector_write_policy"


class ConnectorWritePolicyOverrides:
    """Per-connector write-policy overrides parsed from the run's policy JSON.

    Read from ``user_policies_json['tool_use']['connector_write_policy']``
    (``{}`` on absence). Unknown values are dropped (forward-additive, same
    discipline as ``ToolUsePolicySnapshot``). Keys are normalized through
    ``server_slug`` on ingest AND :meth:`for_connector` normalizes its lookup
    arg the same way, so the backend map (keyed by the connector ``slug``)
    aligns with ``classified.connector`` (also ``server_slug``-normalized).
    """

    __slots__ = ("_by_connector",)

    def __init__(
        self, overrides: Mapping[str, ConnectorWritePolicy] | None = None
    ) -> None:
        self._by_connector = dict(overrides or {})

    @classmethod
    def from_user_policies(
        cls, user_policies_json: Mapping[str, object] | None
    ) -> "ConnectorWritePolicyOverrides":
        """Parse the overrides out of a run's ``user_policies_json`` (total)."""

        if not isinstance(user_policies_json, Mapping):
            return cls({})
        tool_use = user_policies_json.get(_Keys.TOOL_USE)
        if not isinstance(tool_use, Mapping):
            return cls({})
        raw = tool_use.get(_Keys.CONNECTOR_WRITE_POLICY)
        if not isinstance(raw, Mapping):
            return cls({})
        parsed: dict[str, ConnectorWritePolicy] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            try:
                policy = ConnectorWritePolicy(value)
            except ValueError:
                continue  # forward-additive: ignore a mode we don't model
            parsed[server_slug(key)] = policy
        return cls(parsed)

    def for_connector(self, connector: str) -> ConnectorWritePolicy | None:
        """Return the override for ``connector`` (normalized), or ``None``."""

        return self._by_connector.get(server_slug(connector))


class EffectiveActionPolicyResolver:
    """Resolve a :class:`ClassifiedAction` into an :class:`EffectiveActionPolicy`.

    Encodes the SDR §10 invariants 1 and 3 resolution table exactly, then
    applies the per-connector override (``allow_always`` downgrades ONLY a
    ``write``-axis ``ask`` to ``auto``; never touches destructive, never
    downgrades ``require``, never overrides ``block``).
    """

    __slots__ = ("_snapshot", "_overrides")

    def __init__(
        self,
        *,
        snapshot: ToolUsePolicySnapshot,
        overrides: ConnectorWritePolicyOverrides,
    ) -> None:
        self._snapshot = snapshot
        self._overrides = overrides

    def resolve(self, classified: ClassifiedAction) -> EffectiveActionPolicy:
        policy_kind = self._policy_kind(classified)
        mode = self._snapshot.mode_for_kind(policy_kind)
        bypass = False

        # Per-connector override: ONLY a write-axis ASK may be downgraded to
        # AUTO. Destructive axis, REQUIRE, and BLOCK are never touched.
        if (
            policy_kind is ToolUsePolicyKind.WRITE
            and mode is ToolUsePolicyMode.ASK
            and self._overrides.for_connector(classified.connector)
            is ConnectorWritePolicy.ALLOW_ALWAYS
        ):
            mode = ToolUsePolicyMode.AUTO
            bypass = True

        return EffectiveActionPolicy(
            classified=classified,
            policy_kind=policy_kind,
            mode=mode,
            hold=mode is not ToolUsePolicyMode.AUTO,
            bypass=bypass,
        )

    @staticmethod
    def _policy_kind(classified: ClassifiedAction) -> ToolUsePolicyKind:
        """Map a classification onto the enforcement axis (resolution table).

        The ONLY auto-run-eligible cell is READ+CATALOG (-> READ axis). An
        annotation-only read routes to the WRITE axis so it stays held — the
        label stays "read" for honest display, but annotations never grant
        auto-run.
        """

        if classified.action_class is ActionClass.READ:
            if classified.basis is ClassificationBasis.CATALOG:
                return ToolUsePolicyKind.READ
            # READ by annotation -> write axis (never auto-run).
            return ToolUsePolicyKind.WRITE

        # WRITE-classified.
        if classified.basis is ClassificationBasis.CATALOG:
            if classified.catalog_kind is CatalogActionKind.DESTRUCTIVE:
                return ToolUsePolicyKind.DESTRUCTIVE
            return ToolUsePolicyKind.WRITE
        if classified.basis is ClassificationBasis.ANNOTATION:
            # A destructive_hint can only tighten -> destructive axis.
            return ToolUsePolicyKind.DESTRUCTIVE
        # basis == DEFAULT (unknown op) -> fail-closed write axis.
        return ToolUsePolicyKind.WRITE


__all__ = [
    "ConnectorWritePolicyOverrides",
    "EffectiveActionPolicyResolver",
]
