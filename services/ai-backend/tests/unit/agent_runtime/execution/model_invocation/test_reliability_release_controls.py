from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.control_plane.model_reliability import (
    ModelReliabilityControl,
    ModelReliabilityControlSnapshot,
    ModelReliabilityDecisionReason,
    ModelReliabilityLiveConstraints,
    ModelReliabilityReleaseDecision,
    ModelReliabilityReleaseResolver,
)

_AUTHORITY = {
    "run_id": "run-1",
    "snapshot_id": "snapshot-1",
    "snapshot_digest": "a" * 64,
}


def _qualified_snapshot(
    **updates: FeatureMode,
) -> ModelReliabilityControlSnapshot:
    return ModelReliabilityControlSnapshot(
        same_deployment_retry=updates.get(
            "same_deployment_retry",
            FeatureMode.OFF,
        ),
        alternate_route=updates.get("alternate_route", FeatureMode.OFF),
        equivalent_route=updates.get("equivalent_route", FeatureMode.OFF),
        circuit_influence=updates.get("circuit_influence", FeatureMode.OFF),
        qualification_authority_ref="qualification://f1/public-research",
        qualification_authority_revision="f1-qualification-r7",
    )


def test_safe_default_is_primary_only_and_body_free() -> None:
    decision = ModelReliabilityReleaseDecision.safe_off(**_AUTHORITY)

    assert decision.primary_only
    assert not decision.same_deployment_retry_enabled
    assert not decision.alternate_route_enabled
    assert not decision.equivalent_route_enabled
    assert not decision.circuit_influence_enabled
    assert not decision.retry_shadow_observation
    assert not decision.circuit_shadow_observation
    assert set(decision.model_dump(mode="json")) == {
        "run_id",
        "snapshot_id",
        "snapshot_digest",
        "effective_f10_mode",
        "same_deployment_retry",
        "alternate_route",
        "equivalent_route",
        "circuit_influence",
        "qualification_authority_ref",
        "qualification_authority_revision",
    }


@pytest.mark.parametrize(
    ("parent", "child"),
    (
        (FeatureMode.OFF, FeatureMode.SHADOW),
        (FeatureMode.OFF, FeatureMode.ENFORCE),
        (FeatureMode.SHADOW, FeatureMode.ENFORCE),
    ),
)
def test_subcontrol_cannot_exceed_snapshot_parent(
    parent: FeatureMode,
    child: FeatureMode,
) -> None:
    controls = ModelReliabilityControlSnapshot(
        same_deployment_retry=child,
    )

    with pytest.raises(ValueError, match="cannot exceed the parent F10"):
        controls.validate_parent(parent)


def test_equivalent_enforcement_requires_explicit_qualification_authority() -> None:
    with pytest.raises(
        ValidationError,
        match="requires qualification authority",
    ):
        ModelReliabilityControlSnapshot(
            equivalent_route=FeatureMode.ENFORCE,
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelReliabilityControlSnapshot.model_validate(
            {
                "equivalent_route": "enforce",
                "qualified_task_families": ["public_research"],
            }
        )

    controls = _qualified_snapshot(equivalent_route=FeatureMode.ENFORCE)
    assert controls.qualification_authority_revision == "f1-qualification-r7"


def test_parent_off_and_shadow_are_effective_ceilings() -> None:
    resolver = ModelReliabilityReleaseResolver()
    snapshot = _qualified_snapshot(
        same_deployment_retry=FeatureMode.ENFORCE,
        alternate_route=FeatureMode.ENFORCE,
        equivalent_route=FeatureMode.ENFORCE,
        circuit_influence=FeatureMode.ENFORCE,
    )

    shadow = resolver.resolve(
        **_AUTHORITY,
        snapshot=snapshot,
        snapshot_f10_mode=FeatureMode.ENFORCE,
        effective_f10_mode=FeatureMode.SHADOW,
    )
    assert all(
        shadow.for_control(control).effective_mode is FeatureMode.SHADOW
        for control in ModelReliabilityControl
    )
    assert all(
        shadow.for_control(control).reason
        is ModelReliabilityDecisionReason.PARENT_F10_SHADOW
        for control in ModelReliabilityControl
    )

    off = resolver.resolve(
        **_AUTHORITY,
        snapshot=snapshot,
        snapshot_f10_mode=FeatureMode.ENFORCE,
        effective_f10_mode=FeatureMode.OFF,
    )
    assert all(
        off.for_control(control).effective_mode is FeatureMode.OFF
        for control in ModelReliabilityControl
    )
    assert all(
        off.for_control(control).reason is ModelReliabilityDecisionReason.PARENT_F10_OFF
        for control in ModelReliabilityControl
    )


def test_live_constraints_can_only_narrow_each_control() -> None:
    snapshot = _qualified_snapshot(
        same_deployment_retry=FeatureMode.ENFORCE,
        alternate_route=FeatureMode.SHADOW,
        equivalent_route=FeatureMode.ENFORCE,
        circuit_influence=FeatureMode.ENFORCE,
    )
    decision = ModelReliabilityReleaseResolver().resolve(
        **_AUTHORITY,
        snapshot=snapshot,
        snapshot_f10_mode=FeatureMode.ENFORCE,
        effective_f10_mode=FeatureMode.ENFORCE,
        live=ModelReliabilityLiveConstraints(
            modes={
                ModelReliabilityControl.SAME_DEPLOYMENT_RETRY: FeatureMode.SHADOW,
                # An attempted live broadening remains at snapshot SHADOW.
                ModelReliabilityControl.ALTERNATE_ROUTE: FeatureMode.ENFORCE,
                # Malformed trusted-adapter output fails closed.
                ModelReliabilityControl.EQUIVALENT_ROUTE: "unknown",
            },
        ),
    )

    assert decision.same_deployment_retry.effective_mode is FeatureMode.SHADOW
    assert decision.alternate_route.effective_mode is FeatureMode.SHADOW
    assert decision.equivalent_route.effective_mode is FeatureMode.OFF
    assert (
        decision.equivalent_route.reason
        is ModelReliabilityDecisionReason.LIVE_UNKNOWN_DEFAULTED_OFF
    )
    assert decision.circuit_influence.effective_mode is FeatureMode.ENFORCE


def test_independent_kill_switches_do_not_disable_unrelated_controls() -> None:
    snapshot = _qualified_snapshot(
        same_deployment_retry=FeatureMode.ENFORCE,
        alternate_route=FeatureMode.ENFORCE,
        equivalent_route=FeatureMode.SHADOW,
        circuit_influence=FeatureMode.ENFORCE,
    )
    decision = ModelReliabilityReleaseResolver().resolve(
        **_AUTHORITY,
        snapshot=snapshot,
        snapshot_f10_mode=FeatureMode.ENFORCE,
        effective_f10_mode=FeatureMode.ENFORCE,
        live=ModelReliabilityLiveConstraints(
            kill_switches=frozenset(
                {
                    ModelReliabilityControl.ALTERNATE_ROUTE,
                    ModelReliabilityControl.CIRCUIT_INFLUENCE,
                }
            )
        ),
    )

    assert decision.same_deployment_retry_enabled
    assert not decision.alternate_route_enabled
    assert decision.alternate_route.kill_switch_asserted
    assert decision.equivalent_route_shadow_observation
    assert not decision.circuit_influence_enabled
    assert decision.circuit_influence.kill_switch_asserted


def test_resolver_rejects_broader_effective_parent_mode() -> None:
    with pytest.raises(ValueError, match="cannot broaden"):
        ModelReliabilityReleaseResolver().resolve(
            **_AUTHORITY,
            snapshot=ModelReliabilityControlSnapshot(),
            snapshot_f10_mode=FeatureMode.OFF,
            effective_f10_mode=FeatureMode.ENFORCE,
        )
