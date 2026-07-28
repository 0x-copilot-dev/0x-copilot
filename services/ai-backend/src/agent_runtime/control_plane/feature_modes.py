"""Closed F1-F12 rollout modes and authority-narrowing kill switches.

This module owns the common mode vocabulary used by both the immutable run
snapshot and live emergency controls.  It deliberately does not read process
environment variables or mutable product state.  Trusted adapters resolve
those inputs, then call :class:`FeatureModeResolver`.

An ``off`` decision disables only the new feature-owned path.  It does not
remove pre-existing safety boundaries such as authorization, the Operation
Gateway, hard tool budgets, result bounding, or effect staging.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar

from agent_runtime.execution.contracts import RuntimeContract


class FeatureMode(StrEnum):
    """Closed rollout posture shared by runtime feature control planes."""

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"

    @property
    def rank(self) -> int:
        """Return the authority rank used for monotonic mode comparison."""

        return {
            FeatureMode.OFF: 0,
            FeatureMode.SHADOW: 1,
            FeatureMode.ENFORCE: 2,
        }[self]

    @classmethod
    def most_authoritative(cls, *modes: "FeatureMode") -> "FeatureMode":
        """Return the highest mode.

        This compatibility helper is used only while resolving an existing
        authoritative rollout floor.  Runtime kill switches must use
        :meth:`least_authoritative`.
        """

        if not modes:
            raise ValueError("at least one feature mode is required")
        return max(modes, key=lambda mode: mode.rank)

    @classmethod
    def least_authoritative(cls, *modes: "FeatureMode") -> "FeatureMode":
        """Return the narrowest supplied mode for live control composition."""

        if not modes:
            raise ValueError("at least one feature mode is required")
        return min(modes, key=lambda mode: mode.rank)


class AgentQualityFeature(StrEnum):
    """Stable persisted identifiers for the twelve quality-runtime features."""

    F1_HARNESS_QUALITY = "f1"
    F2_PROMPT_ASSEMBLY = "f2"
    F3_CAPABILITY_DISCOVERY = "f3"
    F4_TOOL_USE_CONTROLLER = "f4"
    F5_CONTEXT_BUDGETING = "f5"
    F6_CAPABILITY_CONCURRENCY = "f6"
    F7_GOVERNED_DATAFLOW = "f7"
    F8_MCP_CONTROL_PLANE = "f8"
    F9_PARALLEL_DELEGATION = "f9"
    F10_MODEL_INVOCATION = "f10"
    F11_WORKSPACE_EDITING = "f11"
    F12_ANSWER_VERIFICATION = "f12"


class FeatureFallback(StrEnum):
    """Safe behavior when a feature-owned path cannot be admitted.

    ``DENY_NEW_WORK`` is scoped to the new feature path.  It never revokes a
    pre-existing product authority or bypasses an existing fallback contract.
    """

    OFF = "off"
    SERIAL = "serial"
    DENY_NEW_WORK = "deny_new_work"


class FeatureModeDecisionReason(StrEnum):
    """Low-cardinality reason for a resolved mode."""

    CONFIGURED = "configured"
    DEFAULTED_OFF = "defaulted_off"
    UNKNOWN_DEFAULTED_SAFE = "unknown_defaulted_safe"
    LIVE_CONSTRAINT = "live_constraint"
    KILL_SWITCHED = "kill_switched"


class FeatureModePolicy(RuntimeContract):
    """Closed safe-default policy for one independently controlled feature."""

    feature: AgentQualityFeature
    safe_fallback: FeatureFallback = FeatureFallback.OFF


AGENT_QUALITY_FEATURE_POLICIES: Mapping[AgentQualityFeature, FeatureModePolicy] = (
    MappingProxyType(
        {
            feature: FeatureModePolicy(feature=feature, safe_fallback=fallback)
            for feature, fallback in {
                AgentQualityFeature.F1_HARNESS_QUALITY: FeatureFallback.OFF,
                AgentQualityFeature.F2_PROMPT_ASSEMBLY: FeatureFallback.OFF,
                AgentQualityFeature.F3_CAPABILITY_DISCOVERY: FeatureFallback.OFF,
                AgentQualityFeature.F4_TOOL_USE_CONTROLLER: FeatureFallback.OFF,
                AgentQualityFeature.F5_CONTEXT_BUDGETING: FeatureFallback.OFF,
                AgentQualityFeature.F6_CAPABILITY_CONCURRENCY: FeatureFallback.SERIAL,
                AgentQualityFeature.F7_GOVERNED_DATAFLOW: FeatureFallback.DENY_NEW_WORK,
                AgentQualityFeature.F8_MCP_CONTROL_PLANE: FeatureFallback.OFF,
                AgentQualityFeature.F9_PARALLEL_DELEGATION: (
                    FeatureFallback.DENY_NEW_WORK
                ),
                AgentQualityFeature.F10_MODEL_INVOCATION: FeatureFallback.OFF,
                AgentQualityFeature.F11_WORKSPACE_EDITING: FeatureFallback.DENY_NEW_WORK,
                AgentQualityFeature.F12_ANSWER_VERIFICATION: (
                    FeatureFallback.DENY_NEW_WORK
                ),
            }.items()
        }
    )
)


def feature_mode_policy(feature: AgentQualityFeature) -> FeatureModePolicy:
    """Return the complete safe-default policy for ``feature``."""

    return AGENT_QUALITY_FEATURE_POLICIES[feature]


class FeatureModeDecision(RuntimeContract):
    """Content-free result of resolving snapshot and live feature controls."""

    feature: AgentQualityFeature
    configured_mode: FeatureMode | None = None
    effective_mode: FeatureMode
    safe_fallback: FeatureFallback
    reason: FeatureModeDecisionReason
    kill_switch_asserted: bool = False

    @property
    def observes(self) -> bool:
        """Return whether shadow observations may run."""

        return self.effective_mode in {FeatureMode.SHADOW, FeatureMode.ENFORCE}

    @property
    def enforces(self) -> bool:
        """Return whether the feature may own its enforcement path."""

        return self.effective_mode is FeatureMode.ENFORCE

    @property
    def fallback_active(self) -> bool:
        """Return whether the caller must use the policy's safe fallback."""

        return self.effective_mode is FeatureMode.OFF


class FeatureModeResolver:
    """Pure resolver for initial settings and live authority narrowing."""

    @staticmethod
    def _parse(raw_mode: object) -> FeatureMode | None:
        if isinstance(raw_mode, FeatureMode):
            return raw_mode
        if not isinstance(raw_mode, str):
            return None
        normalized = raw_mode.strip().lower()
        if not normalized:
            return None
        try:
            return FeatureMode(normalized)
        except ValueError:
            return None

    def resolve_configured(
        self,
        *,
        feature: AgentQualityFeature,
        raw_mode: object = None,
    ) -> FeatureModeDecision:
        """Resolve untrusted configuration without retaining or echoing it."""

        policy = feature_mode_policy(feature)
        parsed = self._parse(raw_mode)
        if parsed is not None:
            return FeatureModeDecision(
                feature=feature,
                configured_mode=parsed,
                effective_mode=parsed,
                safe_fallback=policy.safe_fallback,
                reason=FeatureModeDecisionReason.CONFIGURED,
            )
        return FeatureModeDecision(
            feature=feature,
            effective_mode=FeatureMode.OFF,
            safe_fallback=policy.safe_fallback,
            reason=(
                FeatureModeDecisionReason.DEFAULTED_OFF
                if raw_mode is None
                or (isinstance(raw_mode, str) and not raw_mode.strip())
                else FeatureModeDecisionReason.UNKNOWN_DEFAULTED_SAFE
            ),
        )

    def apply_live_constraint(
        self,
        *,
        feature: AgentQualityFeature,
        snapshot_mode: FeatureMode,
        raw_live_mode: object = None,
        kill_switch_asserted: bool = False,
    ) -> FeatureModeDecision:
        """Narrow an immutable snapshot without ever increasing authority.

        An absent live constraint preserves the snapshot.  A malformed live
        constraint is treated like an emergency shutdown and activates the
        feature's conservative fallback.  An asserted kill switch always wins.
        """

        policy = feature_mode_policy(feature)
        if kill_switch_asserted:
            return FeatureModeDecision(
                feature=feature,
                configured_mode=snapshot_mode,
                effective_mode=FeatureMode.OFF,
                safe_fallback=policy.safe_fallback,
                reason=FeatureModeDecisionReason.KILL_SWITCHED,
                kill_switch_asserted=True,
            )
        if raw_live_mode is None or (
            isinstance(raw_live_mode, str) and not raw_live_mode.strip()
        ):
            return FeatureModeDecision(
                feature=feature,
                configured_mode=snapshot_mode,
                effective_mode=snapshot_mode,
                safe_fallback=policy.safe_fallback,
                reason=FeatureModeDecisionReason.CONFIGURED,
            )
        live_mode = self._parse(raw_live_mode)
        if live_mode is None:
            return FeatureModeDecision(
                feature=feature,
                configured_mode=snapshot_mode,
                effective_mode=FeatureMode.OFF,
                safe_fallback=policy.safe_fallback,
                reason=FeatureModeDecisionReason.UNKNOWN_DEFAULTED_SAFE,
            )
        return FeatureModeDecision(
            feature=feature,
            configured_mode=snapshot_mode,
            effective_mode=FeatureMode.least_authoritative(
                snapshot_mode,
                live_mode,
            ),
            safe_fallback=policy.safe_fallback,
            reason=FeatureModeDecisionReason.LIVE_CONSTRAINT,
        )


class FeatureModeSet(RuntimeContract):
    """Closed immutable F1-F12 mode set suitable for a run snapshot."""

    f1: FeatureMode = FeatureMode.OFF
    f2: FeatureMode = FeatureMode.OFF
    f3: FeatureMode = FeatureMode.OFF
    f4: FeatureMode = FeatureMode.OFF
    f5: FeatureMode = FeatureMode.OFF
    f6: FeatureMode = FeatureMode.OFF
    f7: FeatureMode = FeatureMode.OFF
    f8: FeatureMode = FeatureMode.OFF
    f9: FeatureMode = FeatureMode.OFF
    f10: FeatureMode = FeatureMode.OFF
    f11: FeatureMode = FeatureMode.OFF
    f12: FeatureMode = FeatureMode.OFF

    _FIELD_BY_FEATURE: ClassVar[Mapping[AgentQualityFeature, str]] = {
        feature: feature.value for feature in AgentQualityFeature
    }

    @classmethod
    def from_untrusted_mapping(
        cls,
        configured_modes: Mapping[AgentQualityFeature, object],
        *,
        resolver: FeatureModeResolver | None = None,
    ) -> tuple["FeatureModeSet", tuple[FeatureModeDecision, ...]]:
        """Resolve all features in declaration order with safe closed defaults."""

        active_resolver = resolver or FeatureModeResolver()
        decisions = tuple(
            active_resolver.resolve_configured(
                feature=feature,
                raw_mode=configured_modes.get(feature),
            )
            for feature in AgentQualityFeature
        )
        return (
            cls.model_validate(
                {
                    decision.feature.value: decision.effective_mode
                    for decision in decisions
                }
            ),
            decisions,
        )

    def mode_for(self, feature: AgentQualityFeature) -> FeatureMode:
        """Return the immutable mode for one closed feature identifier."""

        return getattr(self, self._FIELD_BY_FEATURE[feature])

    def as_safe_mapping(self) -> dict[str, str]:
        """Return a stable low-cardinality mapping for records and diagnostics."""

        return {
            feature.value: self.mode_for(feature).value
            for feature in AgentQualityFeature
        }
