"""PR 8.0.5 §2.2 — tool-use runtime gate (auto/ask/require/block)."""

from __future__ import annotations

from agent_runtime.capabilities.tools.cards import (
    LoadedToolSpec,
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)
from agent_runtime.capabilities.tools.runtime_gate import (
    ToolGateAction,
    ToolUsePolicyGate,
)


def _spec(*, side_effects: frozenset[ToolSideEffect]) -> LoadedToolSpec:
    return LoadedToolSpec(
        name="acme_tool",
        description="acme",
        args_schema={"type": "object", "properties": {}},
        return_schema={"type": "object", "properties": {}},
        side_effects=side_effects,
        timeout_ms=5000,
        permission_policy=ToolPermissionPolicy(
            connector="acme", risk_level=ToolRiskLevel.LOW
        ),
    )


def _snapshot(
    modes: dict[ToolUsePolicyKind, ToolUsePolicyMode],
) -> ToolUsePolicySnapshot:
    return ToolUsePolicySnapshot(modes)


class TestGateBranches:
    def test_auto_allows_silently(self) -> None:
        decision = ToolUsePolicyGate.decide(
            snapshot=_snapshot({ToolUsePolicyKind.READ: ToolUsePolicyMode.AUTO}),
            spec=_spec(side_effects=frozenset({ToolSideEffect.READ})),
        )
        assert decision.action is ToolGateAction.ALLOW
        assert decision.policy_fired is None

    def test_ask_emits_one_time_approval(self) -> None:
        decision = ToolUsePolicyGate.decide(
            snapshot=_snapshot({ToolUsePolicyKind.WRITE: ToolUsePolicyMode.ASK}),
            spec=_spec(side_effects=frozenset({ToolSideEffect.WRITE})),
        )
        assert decision.action is ToolGateAction.REQUIRE_APPROVAL
        assert decision.policy_fired is ToolUsePolicyKind.WRITE
        assert decision.one_time is True

    def test_require_re_prompts_every_dispatch(self) -> None:
        decision = ToolUsePolicyGate.decide(
            snapshot=_snapshot({ToolUsePolicyKind.WRITE: ToolUsePolicyMode.REQUIRE}),
            spec=_spec(side_effects=frozenset({ToolSideEffect.WRITE})),
        )
        assert decision.action is ToolGateAction.REQUIRE_APPROVAL
        assert decision.one_time is False

    def test_block_rejects_with_stable_message(self) -> None:
        decision = ToolUsePolicyGate.decide(
            snapshot=_snapshot(
                {ToolUsePolicyKind.DESTRUCTIVE: ToolUsePolicyMode.BLOCK}
            ),
            spec=_spec(side_effects=frozenset({ToolSideEffect.DELETE})),
        )
        assert decision.action is ToolGateAction.REJECT
        assert decision.policy_fired is ToolUsePolicyKind.DESTRUCTIVE
        assert decision.mode is ToolUsePolicyMode.BLOCK
        assert decision.safe_message is not None
        # Message MUST be safe to surface as a public API error — no
        # internal detail.
        assert "Destructive tools" in decision.safe_message


class TestKindMapping:
    def test_external_call_treated_as_write(self) -> None:
        decision = ToolUsePolicyGate.decide(
            snapshot=_snapshot({ToolUsePolicyKind.WRITE: ToolUsePolicyMode.BLOCK}),
            spec=_spec(side_effects=frozenset({ToolSideEffect.EXTERNAL_CALL})),
        )
        assert decision.action is ToolGateAction.REJECT
        assert decision.policy_fired is ToolUsePolicyKind.WRITE

    def test_delete_wins_over_write_in_side_effects_mix(self) -> None:
        # Specs that touch both stay on the destructive axis — the
        # gate errs strict.
        decision = ToolUsePolicyGate.decide(
            snapshot=_snapshot(
                {
                    ToolUsePolicyKind.WRITE: ToolUsePolicyMode.AUTO,
                    ToolUsePolicyKind.DESTRUCTIVE: ToolUsePolicyMode.REQUIRE,
                }
            ),
            spec=_spec(
                side_effects=frozenset({ToolSideEffect.WRITE, ToolSideEffect.DELETE})
            ),
        )
        assert decision.action is ToolGateAction.REQUIRE_APPROVAL
        assert decision.policy_fired is ToolUsePolicyKind.DESTRUCTIVE
