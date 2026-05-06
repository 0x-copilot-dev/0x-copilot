"""PR B1 / 8.0.3d — tool-use policy snapshot resolver."""

from __future__ import annotations

from agent_runtime.capabilities.tools.cards import (
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)
from agent_runtime.capabilities.tools.permissions import (
    ToolPermissionChecker,
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
    kind_for_side_effects,
)


class TestSnapshotComposition:
    def test_default_snapshot_returns_deployment_defaults(self) -> None:
        """``from_policy(None)`` must return the same deployment
        defaults the backend hydrates so a runtime that hasn't loaded
        a policy yet doesn't refuse on missing state alone."""

        snapshot = ToolPermissionChecker.from_policy(None)
        assert snapshot.mode_for_kind(ToolUsePolicyKind.READ) is ToolUsePolicyMode.AUTO
        assert snapshot.mode_for_kind(ToolUsePolicyKind.WRITE) is ToolUsePolicyMode.ASK
        assert (
            snapshot.mode_for_kind(ToolUsePolicyKind.DESTRUCTIVE)
            is ToolUsePolicyMode.REQUIRE
        )

    def test_user_override_wins_over_workspace_default(self) -> None:
        snapshot = ToolUsePolicySnapshot.from_response(
            workspace={"read": "auto", "write": "ask", "destructive": "require"},
            user={"destructive": "block"},
        )
        assert (
            snapshot.mode_for_kind(ToolUsePolicyKind.DESTRUCTIVE)
            is ToolUsePolicyMode.BLOCK
        )
        # Non-overridden axes inherit from workspace.
        assert snapshot.mode_for_kind(ToolUsePolicyKind.WRITE) is ToolUsePolicyMode.ASK

    def test_unknown_modes_drop_silently(self) -> None:
        """Forward-additive: a deployment that adds a new mode value
        won't crash an older runtime — the unknown entry is dropped
        and the axis falls through to the deployment default."""

        snapshot = ToolUsePolicySnapshot.from_response(
            workspace={"write": "make_up_mode"},
        )
        assert snapshot.mode_for_kind(ToolUsePolicyKind.WRITE) is ToolUsePolicyMode.ASK


class TestKindMapping:
    def test_high_risk_policy_maps_to_destructive(self) -> None:
        policy = ToolPermissionPolicy(
            connector="acme",
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )
        snapshot = ToolPermissionChecker.from_policy(None)
        assert snapshot.mode_for_tool(policy) is ToolUsePolicyMode.REQUIRE

    def test_medium_risk_maps_to_write(self) -> None:
        policy = ToolPermissionPolicy(
            connector="acme",
            risk_level=ToolRiskLevel.MEDIUM,
        )
        snapshot = ToolPermissionChecker.from_policy(None)
        assert snapshot.mode_for_tool(policy) is ToolUsePolicyMode.ASK

    def test_low_risk_maps_to_read(self) -> None:
        policy = ToolPermissionPolicy(
            connector="acme",
            risk_level=ToolRiskLevel.LOW,
        )
        snapshot = ToolPermissionChecker.from_policy(None)
        assert snapshot.mode_for_tool(policy) is ToolUsePolicyMode.AUTO

    def test_side_effects_map_destructive_first(self) -> None:
        # Even if WRITE is also present, DELETE wins.
        side_effects = frozenset({ToolSideEffect.WRITE, ToolSideEffect.DELETE})
        assert kind_for_side_effects(side_effects) is ToolUsePolicyKind.DESTRUCTIVE

    def test_side_effects_external_call_is_write(self) -> None:
        side_effects = frozenset({ToolSideEffect.EXTERNAL_CALL})
        assert kind_for_side_effects(side_effects) is ToolUsePolicyKind.WRITE

    def test_side_effects_read_only(self) -> None:
        side_effects = frozenset({ToolSideEffect.READ})
        assert kind_for_side_effects(side_effects) is ToolUsePolicyKind.READ
