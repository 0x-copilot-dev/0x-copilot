"""Step 0 contract tests for F1-F12 modes and emergency narrowing."""

from agent_runtime.control_plane.feature_modes import (
    AGENT_QUALITY_FEATURE_POLICIES,
    AgentQualityFeature,
    FeatureFallback,
    FeatureMode,
    FeatureModeDecisionReason,
    FeatureModeResolver,
    FeatureModeSet,
    feature_mode_policy,
)
from agent_runtime.rollout import RolloutMode


def test_feature_mode_is_the_existing_rollout_mode_authority() -> None:
    assert RolloutMode is FeatureMode
    assert (
        FeatureMode.most_authoritative(
            FeatureMode.OFF,
            FeatureMode.ENFORCE,
        )
        is FeatureMode.ENFORCE
    )
    assert (
        FeatureMode.least_authoritative(
            FeatureMode.SHADOW,
            FeatureMode.ENFORCE,
        )
        is FeatureMode.SHADOW
    )


def test_policy_map_is_closed_and_complete_for_all_twelve_features() -> None:
    assert len(AgentQualityFeature) == 12
    assert set(AGENT_QUALITY_FEATURE_POLICIES) == set(AgentQualityFeature)
    assert {
        policy.feature for policy in AGENT_QUALITY_FEATURE_POLICIES.values()
    } == set(AgentQualityFeature)


def test_safe_fallbacks_are_scoped_to_their_feature_owned_paths() -> None:
    # Capability concurrency has no fallback of its own any more: the runtime
    # does not decide which tool calls overlap, so there is nothing for it to
    # fall back *to* and ``OFF`` is the honest answer.
    assert (
        feature_mode_policy(AgentQualityFeature.F6_CAPABILITY_CONCURRENCY).safe_fallback
        is FeatureFallback.OFF
    )
    deny_features = {
        AgentQualityFeature.F7_GOVERNED_DATAFLOW,
        AgentQualityFeature.F9_PARALLEL_DELEGATION,
        AgentQualityFeature.F11_WORKSPACE_EDITING,
        AgentQualityFeature.F12_ANSWER_VERIFICATION,
    }
    assert {
        feature
        for feature, policy in AGENT_QUALITY_FEATURE_POLICIES.items()
        if policy.safe_fallback is FeatureFallback.DENY_NEW_WORK
    } == deny_features


def test_missing_and_unknown_modes_default_to_safe_off_without_echoing_input() -> None:
    resolver = FeatureModeResolver()
    missing = resolver.resolve_configured(
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY
    )
    unknown = resolver.resolve_configured(
        feature=AgentQualityFeature.F6_CAPABILITY_CONCURRENCY,
        raw_mode="secret-bearing-unknown-value",
    )

    assert missing.effective_mode is FeatureMode.OFF
    assert missing.reason is FeatureModeDecisionReason.DEFAULTED_OFF
    assert unknown.effective_mode is FeatureMode.OFF
    assert unknown.safe_fallback is FeatureFallback.OFF
    assert unknown.reason is FeatureModeDecisionReason.UNKNOWN_DEFAULTED_SAFE
    assert "secret-bearing" not in repr(unknown)
    assert "secret-bearing" not in unknown.model_dump_json()


def test_valid_config_is_normalized_but_never_open_ended() -> None:
    decision = FeatureModeResolver().resolve_configured(
        feature=AgentQualityFeature.F1_HARNESS_QUALITY,
        raw_mode="  ShAdOw ",
    )

    assert decision.configured_mode is FeatureMode.SHADOW
    assert decision.effective_mode is FeatureMode.SHADOW
    assert decision.observes
    assert not decision.enforces
    assert not decision.fallback_active


def test_live_constraint_can_narrow_but_never_broaden_snapshot() -> None:
    resolver = FeatureModeResolver()
    narrowed = resolver.apply_live_constraint(
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
        snapshot_mode=FeatureMode.ENFORCE,
        raw_live_mode=FeatureMode.SHADOW,
    )
    attempted_broadening = resolver.apply_live_constraint(
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
        snapshot_mode=FeatureMode.SHADOW,
        raw_live_mode=FeatureMode.ENFORCE,
    )
    off_stays_off = resolver.apply_live_constraint(
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
        snapshot_mode=FeatureMode.OFF,
        raw_live_mode=FeatureMode.ENFORCE,
    )

    assert narrowed.effective_mode is FeatureMode.SHADOW
    assert attempted_broadening.effective_mode is FeatureMode.SHADOW
    assert off_stays_off.effective_mode is FeatureMode.OFF


def test_kill_switch_wins_and_activates_feature_specific_safe_fallback() -> None:
    decision = FeatureModeResolver().apply_live_constraint(
        feature=AgentQualityFeature.F6_CAPABILITY_CONCURRENCY,
        snapshot_mode=FeatureMode.ENFORCE,
        raw_live_mode=FeatureMode.ENFORCE,
        kill_switch_asserted=True,
    )

    assert decision.effective_mode is FeatureMode.OFF
    assert decision.safe_fallback is FeatureFallback.OFF
    assert decision.reason is FeatureModeDecisionReason.KILL_SWITCHED
    assert decision.kill_switch_asserted
    assert decision.fallback_active


def test_malformed_live_constraint_fails_closed() -> None:
    decision = FeatureModeResolver().apply_live_constraint(
        feature=AgentQualityFeature.F11_WORKSPACE_EDITING,
        snapshot_mode=FeatureMode.ENFORCE,
        raw_live_mode={"unexpected": "shape"},
    )

    assert decision.effective_mode is FeatureMode.OFF
    assert decision.safe_fallback is FeatureFallback.DENY_NEW_WORK
    assert decision.reason is FeatureModeDecisionReason.UNKNOWN_DEFAULTED_SAFE


def test_feature_mode_set_is_closed_ordered_and_off_by_default() -> None:
    default_modes = FeatureModeSet()
    modes, decisions = FeatureModeSet.from_untrusted_mapping(
        {
            AgentQualityFeature.F2_PROMPT_ASSEMBLY: "shadow",
            AgentQualityFeature.F6_CAPABILITY_CONCURRENCY: "not-a-mode",
        }
    )

    assert default_modes.as_safe_mapping() == {
        feature.value: "off" for feature in AgentQualityFeature
    }
    assert modes.mode_for(AgentQualityFeature.F2_PROMPT_ASSEMBLY) is FeatureMode.SHADOW
    assert (
        modes.mode_for(AgentQualityFeature.F6_CAPABILITY_CONCURRENCY) is FeatureMode.OFF
    )
    assert len(decisions) == 12
    assert tuple(decision.feature for decision in decisions) == tuple(
        AgentQualityFeature
    )
