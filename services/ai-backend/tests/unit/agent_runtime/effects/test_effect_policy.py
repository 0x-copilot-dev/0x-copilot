from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.effects.policy import EffectStagePolicyResolver
from agent_runtime.surfaces_v2.ledger_models import EffectClass, EffectPolicy

from .fakes import policy_snapshot, proposal


@pytest.mark.parametrize(
    ("effect_class", "snapshot_kwargs", "agent_hold", "expected"),
    [
        (
            EffectClass.EXTERNAL_REVERSIBLE,
            {"allow_always": True},
            False,
            EffectPolicy.AUTO,
        ),
        (EffectClass.UNKNOWN, {"allow_always": True}, False, EffectPolicy.ASK),
        (
            EffectClass.EXTERNAL_DESTRUCTIVE,
            {"allow_always": True},
            False,
            EffectPolicy.REQUIRE,
        ),
        (
            EffectClass.EXTERNAL_REVERSIBLE,
            {"sensitive_target": True},
            False,
            EffectPolicy.REQUIRE,
        ),
        (
            EffectClass.EXTERNAL_REVERSIBLE,
            {"allow_always": True},
            True,
            EffectPolicy.REQUIRE,
        ),
        (
            EffectClass.EXTERNAL_REVERSIBLE,
            {"descriptor_known": False, "allow_always": True},
            False,
            EffectPolicy.ASK,
        ),
        (
            EffectClass.EXTERNAL_REVERSIBLE,
            {"deployment_policy": EffectPolicy.BLOCK},
            False,
            EffectPolicy.BLOCK,
        ),
    ],
)
def test_policy_matrix_is_monotonic_and_fail_closed(
    effect_class: EffectClass,
    snapshot_kwargs: dict[str, object],
    agent_hold: bool,
    expected: EffectPolicy,
) -> None:
    result = EffectStagePolicyResolver().resolve(
        proposed_effect=proposal(effect_class=effect_class, agent_hold=agent_hold),
        snapshot=policy_snapshot(**snapshot_kwargs),
    )

    assert result.policy is expected
    assert result.auto_approval_allowed is (expected is EffectPolicy.AUTO)


def test_explicit_allow_always_never_lowers_a_destructive_floor() -> None:
    result = EffectStagePolicyResolver().resolve(
        proposed_effect=proposal(effect_class=EffectClass.EXTERNAL_DESTRUCTIVE),
        snapshot=policy_snapshot(user_policy=EffectPolicy.AUTO, allow_always=True),
    )

    assert result.policy is EffectPolicy.REQUIRE


def test_unqualified_auto_policy_degrades_to_ask_first() -> None:
    result = EffectStagePolicyResolver().resolve(
        proposed_effect=proposal(),
        snapshot=policy_snapshot(user_policy=EffectPolicy.AUTO),
    )

    assert result.policy is EffectPolicy.ASK


def test_policy_snapshot_is_immutable_after_stage_time() -> None:
    snapshot = policy_snapshot(allow_always=True)

    with pytest.raises(ValidationError):
        snapshot.allow_always = False
