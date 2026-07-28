from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.model_invocation.release_controls import (
    ModelReliabilityReleaseControls,
)


def test_all_controls_off_preserve_primary_only_feature_off_parity() -> None:
    decision = ModelReliabilityReleaseControls().resolve()
    assert decision.primary_only
    assert not decision.same_deployment_retry_enabled
    assert not decision.alternate_route_enabled
    assert not decision.equivalent_route_enabled
    assert not decision.circuit_influence_enabled
    assert not decision.retry_shadow_observation
    assert not decision.circuit_shadow_observation


def test_controls_and_kill_switches_are_independent() -> None:
    decision = ModelReliabilityReleaseControls(
        retry_mode=FeatureMode.ENFORCE,
        alternate_route_mode=FeatureMode.ENFORCE,
        equivalent_route_mode=FeatureMode.SHADOW,
        circuit_mode=FeatureMode.ENFORCE,
        alternate_route_kill_switch=True,
        circuit_kill_switch=True,
    ).resolve()
    assert decision.same_deployment_retry_enabled
    assert not decision.alternate_route_enabled
    assert decision.equivalent_route_shadow_observation
    assert not decision.equivalent_route_enabled
    assert not decision.circuit_influence_enabled
