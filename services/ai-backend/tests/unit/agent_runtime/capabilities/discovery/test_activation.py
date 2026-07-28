from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.discovery import (
    CapabilityActivationDecision,
    CapabilityActivationError,
    CapabilityActivationMode,
    CapabilityActivationReason,
    CapabilityActivationResolver,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureFallback,
    FeatureMode,
    FeatureModeDecision,
    FeatureModeDecisionReason,
    FeatureModeResolver,
)


class ActivationResolverMixin:
    """Shared resolver, mode fixtures, and typed-error extraction."""

    @staticmethod
    def resolver() -> CapabilityActivationResolver:
        return CapabilityActivationResolver(feature_modes=FeatureModeResolver())

    @staticmethod
    def mode_decision(
        *,
        effective_mode: FeatureMode,
        feature: AgentQualityFeature = AgentQualityFeature.F3_CAPABILITY_DISCOVERY,
    ) -> FeatureModeDecision:
        return FeatureModeDecision(
            feature=feature,
            configured_mode=effective_mode,
            effective_mode=effective_mode,
            safe_fallback=FeatureFallback.OFF,
            reason=FeatureModeDecisionReason.CONFIGURED,
        )

    @staticmethod
    def raised_error(exc_info: pytest.ExceptionInfo[ValidationError]) -> object:
        return exc_info.value.errors()[0].get("ctx", {}).get("error")


class TestCapabilityActivationMode:
    def test_rank_is_monotonic_in_machinery_depth(self) -> None:
        ranks = [mode.rank for mode in CapabilityActivationMode]

        assert ranks == sorted(ranks)
        assert CapabilityActivationMode.DIRECT.rank == 0
        assert CapabilityActivationMode.DEFERRED.rank == 3

    def test_narrowest_returns_the_least_activating_mode(self) -> None:
        assert (
            CapabilityActivationMode.narrowest(
                CapabilityActivationMode.DEFERRED,
                CapabilityActivationMode.SERVER,
                CapabilityActivationMode.SHADOW,
            )
            is CapabilityActivationMode.SERVER
        )

    def test_narrowest_requires_at_least_one_mode(self) -> None:
        with pytest.raises(CapabilityActivationError, match="at least one"):
            CapabilityActivationMode.narrowest()

    def test_ceiling_maps_each_feature_mode(self) -> None:
        assert (
            CapabilityActivationMode.ceiling_for(FeatureMode.OFF)
            is CapabilityActivationMode.DIRECT
        )
        assert (
            CapabilityActivationMode.ceiling_for(FeatureMode.SHADOW)
            is CapabilityActivationMode.SHADOW
        )
        assert (
            CapabilityActivationMode.ceiling_for(FeatureMode.ENFORCE)
            is CapabilityActivationMode.DEFERRED
        )

    def test_parse_normalizes_and_rejects_untrusted_values(self) -> None:
        assert (
            CapabilityActivationMode.parse("  DEFERRED ")
            is CapabilityActivationMode.DEFERRED
        )
        assert (
            CapabilityActivationMode.parse(CapabilityActivationMode.SHADOW)
            is CapabilityActivationMode.SHADOW
        )
        assert CapabilityActivationMode.parse("enforce") is None
        assert CapabilityActivationMode.parse("") is None
        assert CapabilityActivationMode.parse(None) is None
        assert CapabilityActivationMode.parse(3) is None

    def test_posture_predicates_are_mutually_exclusive(self) -> None:
        assert CapabilityActivationMode.DEFERRED.registers_bridge
        assert CapabilityActivationMode.SHADOW.observes_only
        assert CapabilityActivationMode.DIRECT.uses_existing_disclosure
        assert CapabilityActivationMode.SERVER.uses_existing_disclosure
        assert not CapabilityActivationMode.SHADOW.registers_bridge
        assert not CapabilityActivationMode.DEFERRED.uses_existing_disclosure


class TestConfiguredActivation(ActivationResolverMixin):
    def test_absent_activation_defaults_to_direct(self) -> None:
        decision = self.resolver().resolve_configured(raw_mode="enforce")

        assert decision.effective_activation is CapabilityActivationMode.DIRECT
        assert decision.reason is CapabilityActivationReason.DEFAULTED_DIRECT
        assert decision.requested_activation is None
        assert decision.uses_existing_disclosure

    def test_unknown_activation_defaults_to_direct(self) -> None:
        for raw_activation in ("turbo", "ENFORCE", 7, object(), True):
            decision = self.resolver().resolve_configured(
                raw_mode="enforce",
                raw_activation=raw_activation,
            )

            assert decision.effective_activation is CapabilityActivationMode.DIRECT
            assert decision.reason is CapabilityActivationReason.UNKNOWN_DEFAULTED_SAFE
            assert decision.requested_activation is None

    def test_enforce_mode_admits_the_configured_deferred_bridge(self) -> None:
        decision = self.resolver().resolve_configured(
            raw_mode="enforce",
            raw_activation="deferred",
        )

        assert decision.effective_activation is CapabilityActivationMode.DEFERRED
        assert decision.reason is CapabilityActivationReason.CONFIGURED
        assert decision.registers_bridge

    def test_shadow_mode_clamps_deferred_to_shadow(self) -> None:
        decision = self.resolver().resolve_configured(
            raw_mode="shadow",
            raw_activation="deferred",
        )

        assert decision.effective_activation is CapabilityActivationMode.SHADOW
        assert decision.reason is CapabilityActivationReason.FEATURE_MODE_CEILING
        assert decision.observes_only
        assert not decision.registers_bridge

    def test_off_mode_clamps_every_activation_to_direct(self) -> None:
        for raw_activation in ("deferred", "shadow", "server"):
            decision = self.resolver().resolve_configured(
                raw_mode="off",
                raw_activation=raw_activation,
            )

            assert decision.effective_activation is CapabilityActivationMode.DIRECT
            assert decision.reason is CapabilityActivationReason.FEATURE_MODE_CEILING

    def test_absent_mode_defaults_off_and_therefore_direct(self) -> None:
        decision = self.resolver().resolve_configured(raw_activation="deferred")

        assert decision.mode.effective_mode is FeatureMode.OFF
        assert decision.mode.reason is FeatureModeDecisionReason.DEFAULTED_OFF
        assert decision.effective_activation is CapabilityActivationMode.DIRECT

    def test_unknown_mode_value_fails_closed_through_the_shared_seam(self) -> None:
        decision = self.resolver().resolve_configured(
            raw_mode="turbo",
            raw_activation="deferred",
        )

        assert decision.mode.effective_mode is FeatureMode.OFF
        assert decision.mode.reason is FeatureModeDecisionReason.UNKNOWN_DEFAULTED_SAFE
        assert decision.effective_activation is CapabilityActivationMode.DIRECT

    def test_decision_carries_the_shared_feature_mode_provenance(self) -> None:
        decision = self.resolver().resolve_configured(
            raw_mode="enforce",
            raw_activation="deferred",
        )

        assert decision.mode.feature is AgentQualityFeature.F3_CAPABILITY_DISCOVERY
        assert decision.mode.safe_fallback is FeatureFallback.OFF
        assert decision.mode.enforces

    def test_server_activation_never_widens_to_the_mode_ceiling(self) -> None:
        decision = self.resolver().resolve_configured(
            raw_mode="enforce",
            raw_activation="server",
        )

        assert decision.effective_activation is CapabilityActivationMode.SERVER
        assert decision.reason is CapabilityActivationReason.CONFIGURED


class TestLiveActivationConstraint(ActivationResolverMixin):
    def test_kill_switch_narrows_deferred_to_direct(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.DEFERRED,
            kill_switch_asserted=True,
        )

        assert decision.effective_activation is CapabilityActivationMode.DIRECT
        assert decision.reason is CapabilityActivationReason.KILL_SWITCHED
        assert decision.mode.kill_switch_asserted
        assert not decision.registers_bridge

    def test_absent_live_activation_preserves_the_bound_posture(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.DEFERRED,
        )

        assert decision.effective_activation is CapabilityActivationMode.DEFERRED
        assert decision.reason is CapabilityActivationReason.CONFIGURED

    def test_live_activation_narrows_the_bound_posture(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.DEFERRED,
            raw_live_activation="server",
        )

        assert decision.effective_activation is CapabilityActivationMode.SERVER
        assert decision.reason is CapabilityActivationReason.LIVE_CONSTRAINT

    def test_live_activation_can_never_widen_the_bound_posture(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.SERVER,
            raw_live_activation="deferred",
        )

        assert decision.effective_activation is CapabilityActivationMode.SERVER
        assert not decision.registers_bridge

    def test_malformed_live_activation_falls_back_to_direct(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.DEFERRED,
            raw_live_activation="turbo",
        )

        assert decision.effective_activation is CapabilityActivationMode.DIRECT
        assert decision.reason is CapabilityActivationReason.UNKNOWN_DEFAULTED_SAFE

    def test_live_mode_narrowing_clamps_the_bound_activation(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.DEFERRED,
            raw_live_mode="shadow",
        )

        assert decision.mode.effective_mode is FeatureMode.SHADOW
        assert decision.effective_activation is CapabilityActivationMode.SHADOW
        assert decision.reason is CapabilityActivationReason.FEATURE_MODE_CEILING

    def test_live_mode_can_never_widen_the_snapshot_mode(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.SHADOW,
            snapshot_activation=CapabilityActivationMode.SHADOW,
            raw_live_mode="enforce",
        )

        assert decision.mode.effective_mode is FeatureMode.SHADOW
        assert decision.effective_activation is CapabilityActivationMode.SHADOW

    def test_malformed_live_mode_fails_closed_to_direct(self) -> None:
        decision = self.resolver().apply_live_constraint(
            snapshot_mode=FeatureMode.ENFORCE,
            snapshot_activation=CapabilityActivationMode.DEFERRED,
            raw_live_mode="turbo",
        )

        assert decision.mode.effective_mode is FeatureMode.OFF
        assert decision.effective_activation is CapabilityActivationMode.DIRECT


class TestActivationDecisionInvariants(ActivationResolverMixin):
    def test_activation_above_the_mode_ceiling_is_unrepresentable(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityActivationDecision(
                mode=self.mode_decision(effective_mode=FeatureMode.OFF),
                effective_activation=CapabilityActivationMode.DEFERRED,
                reason=CapabilityActivationReason.CONFIGURED,
            )

        error = self.raised_error(exc_info)
        assert isinstance(error, CapabilityActivationError)
        assert str(error) == "activation cannot exceed the resolved feature mode"

    def test_activation_above_the_request_is_unrepresentable(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityActivationDecision(
                mode=self.mode_decision(effective_mode=FeatureMode.ENFORCE),
                requested_activation=CapabilityActivationMode.SERVER,
                effective_activation=CapabilityActivationMode.DEFERRED,
                reason=CapabilityActivationReason.CONFIGURED,
            )

        error = self.raised_error(exc_info)
        assert isinstance(error, CapabilityActivationError)
        assert str(error) == "activation cannot exceed the requested posture"

    def test_decision_rejects_a_foreign_feature_mode(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityActivationDecision(
                mode=self.mode_decision(
                    effective_mode=FeatureMode.ENFORCE,
                    feature=AgentQualityFeature.F4_TOOL_USE_CONTROLLER,
                ),
                effective_activation=CapabilityActivationMode.DIRECT,
                reason=CapabilityActivationReason.CONFIGURED,
            )

        error = self.raised_error(exc_info)
        assert isinstance(error, CapabilityActivationError)
        assert str(error) == "activation decisions belong to the F3 discovery feature"

    def test_narrowed_to_only_ever_narrows(self) -> None:
        decision = self.resolver().resolve_configured(
            raw_mode="enforce",
            raw_activation="deferred",
        )

        narrowed = decision.narrowed_to(
            CapabilityActivationMode.SERVER,
            reason=CapabilityActivationReason.LIVE_CONSTRAINT,
        )
        widened = narrowed.narrowed_to(
            CapabilityActivationMode.DEFERRED,
            reason=CapabilityActivationReason.CONFIGURED,
        )

        assert narrowed.effective_activation is CapabilityActivationMode.SERVER
        assert narrowed.reason is CapabilityActivationReason.LIVE_CONSTRAINT
        assert widened is narrowed

    def test_decision_is_immutable(self) -> None:
        decision = self.resolver().resolve_configured(raw_mode="enforce")

        with pytest.raises(ValidationError):
            decision.effective_activation = CapabilityActivationMode.DEFERRED
