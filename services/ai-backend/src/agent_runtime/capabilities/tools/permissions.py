"""Shared authorization helpers for dynamic tool loading."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.tools.cards import (
    ToolCard,
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)


class ToolUsePolicyKind(StrEnum):
    """Mirror of the backend's three policy axes (PR B1 / 8.0.3d)."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolUsePolicyMode(StrEnum):
    """Mirror of the backend's four allowed modes."""

    AUTO = "auto"
    ASK = "ask"
    REQUIRE = "require"
    BLOCK = "block"


class ToolUsePolicySnapshot:
    """Resolved (kind → mode) map cached on ``AgentRuntimeContext``.

    Composes the workspace default and (when present) the per-user
    override into a single map. A user override wins for that user;
    every absent cell falls through to the deployment default the
    backend hydrates on the GET path.

    The workspace + user shapes both arrive from the backend's
    ``/internal/v1/policies/tool-use`` endpoint as
    ``{kind: mode}`` dicts.
    """

    __slots__ = ("_modes",)

    def __init__(self, modes: Mapping[ToolUsePolicyKind, ToolUsePolicyMode]) -> None:
        self._modes = dict(modes)

    @classmethod
    def from_response(
        cls,
        *,
        workspace: Mapping[str, str] | None = None,
        user: Mapping[str, str] | None = None,
    ) -> "ToolUsePolicySnapshot":
        """Compose workspace + user-override dicts into a snapshot.

        Each input maps the wire-format strings (``"read"`` /
        ``"write"`` / ``"destructive"`` → ``"auto"`` / ``"ask"`` /
        ``"require"`` / ``"block"``). Unknown values are dropped
        (forward-additive: a future deployment that adds a new mode
        won't blow up an older runtime).
        """

        modes: dict[ToolUsePolicyKind, ToolUsePolicyMode] = {}
        for source in (workspace or {}, user or {}):
            for raw_kind, raw_mode in source.items():
                try:
                    kind = ToolUsePolicyKind(raw_kind)
                    mode = ToolUsePolicyMode(raw_mode)
                except ValueError:
                    continue
                modes[kind] = mode
        return cls(modes)

    def mode_for_kind(self, kind: ToolUsePolicyKind) -> ToolUsePolicyMode:
        """Return the mode for an axis. Defaults match the backend's
        deployment default (read=auto, write=ask, destructive=require)
        when no row is present so the runtime never refuses on missing
        policy state alone."""

        return self._modes.get(kind, _DEFAULT_MODES[kind])

    def mode_for_tool(self, policy: ToolPermissionPolicy) -> ToolUsePolicyMode:
        """Return the mode for a loaded tool spec.

        Mapping (intentionally narrow — the runtime errs on the side
        of stricter mode when ambiguous):

        * ``side_effects`` ∋ ``DELETE`` ⇒ destructive
        * ``side_effects`` ∋ ``WRITE`` or ``EXTERNAL_CALL`` ⇒ write
        * otherwise ⇒ read

        ``risk_level`` HIGH / CRITICAL bumps to destructive even if
        the side-effects say otherwise — the existing card validator
        already requires confirmation for those, so the policy gate
        agreeing keeps the two surfaces consistent.
        """

        return self.mode_for_kind(_kind_for_tool_policy(policy))


_DEFAULT_MODES: dict[ToolUsePolicyKind, ToolUsePolicyMode] = {
    ToolUsePolicyKind.READ: ToolUsePolicyMode.AUTO,
    ToolUsePolicyKind.WRITE: ToolUsePolicyMode.ASK,
    ToolUsePolicyKind.DESTRUCTIVE: ToolUsePolicyMode.REQUIRE,
}


def _kind_for_tool_policy(policy: ToolPermissionPolicy) -> ToolUsePolicyKind:
    if policy.risk_level in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL}:
        return ToolUsePolicyKind.DESTRUCTIVE
    # `getattr` not strictly necessary but a future field may move
    # `side_effects` onto ``ToolPermissionPolicy``; today it lives on
    # the loaded spec instead. Until then, risk_level is the only
    # signal on the policy itself, and we treat MEDIUM as "write".
    if policy.risk_level == ToolRiskLevel.MEDIUM:
        return ToolUsePolicyKind.WRITE
    return ToolUsePolicyKind.READ


def kind_for_side_effects(
    side_effects: frozenset[ToolSideEffect],
) -> ToolUsePolicyKind:
    """Map a loaded spec's side-effect set onto a policy axis.

    Used by callers that have a ``LoadedToolSpec`` rather than just
    its ``permission_policy`` — they get a more precise mode because
    DELETE / WRITE / EXTERNAL_CALL discriminate further than
    risk_level alone.
    """

    if ToolSideEffect.DELETE in side_effects:
        return ToolUsePolicyKind.DESTRUCTIVE
    if (
        ToolSideEffect.WRITE in side_effects
        or ToolSideEffect.EXTERNAL_CALL in side_effects
    ):
        return ToolUsePolicyKind.WRITE
    return ToolUsePolicyKind.READ


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
    def from_policy(
        cls,
        snapshot: ToolUsePolicySnapshot | None,
    ) -> ToolUsePolicySnapshot:
        """PR B1 / 8.0.3d — return the snapshot the runtime caches on
        ``AgentRuntimeContext`` at run start.

        Accepts an existing snapshot (passed through verbatim) or
        ``None`` (returns a deployment-default snapshot). Centralised
        here so callers don't need to import ``ToolUsePolicySnapshot``
        directly to get a sensible default.
        """

        if snapshot is None:
            return ToolUsePolicySnapshot(_DEFAULT_MODES)
        return snapshot

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
