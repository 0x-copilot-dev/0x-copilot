"""Independent rollout controls for F10 recovery mechanisms."""

from __future__ import annotations

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import RuntimeContract


class ModelReliabilityReleaseControls(RuntimeContract):
    """Snapshot modes plus emergency kill switches; all defaults preserve parity."""

    retry_mode: FeatureMode = FeatureMode.OFF
    alternate_route_mode: FeatureMode = FeatureMode.OFF
    equivalent_route_mode: FeatureMode = FeatureMode.OFF
    circuit_mode: FeatureMode = FeatureMode.OFF
    retry_kill_switch: bool = False
    alternate_route_kill_switch: bool = False
    equivalent_route_kill_switch: bool = False
    circuit_kill_switch: bool = False

    def resolve(self) -> "ModelReliabilityReleaseDecision":
        return ModelReliabilityReleaseDecision(
            same_deployment_retry_enabled=self._enforces(
                self.retry_mode, self.retry_kill_switch
            ),
            alternate_route_enabled=self._enforces(
                self.alternate_route_mode, self.alternate_route_kill_switch
            ),
            equivalent_route_enabled=self._enforces(
                self.equivalent_route_mode, self.equivalent_route_kill_switch
            ),
            circuit_influence_enabled=self._enforces(
                self.circuit_mode, self.circuit_kill_switch
            ),
            retry_shadow_observation=self._shadows(
                self.retry_mode, self.retry_kill_switch
            ),
            alternate_route_shadow_observation=self._shadows(
                self.alternate_route_mode, self.alternate_route_kill_switch
            ),
            equivalent_route_shadow_observation=self._shadows(
                self.equivalent_route_mode, self.equivalent_route_kill_switch
            ),
            circuit_shadow_observation=self._shadows(
                self.circuit_mode, self.circuit_kill_switch
            ),
        )

    @staticmethod
    def _enforces(mode: FeatureMode, killed: bool) -> bool:
        return not killed and mode is FeatureMode.ENFORCE

    @staticmethod
    def _shadows(mode: FeatureMode, killed: bool) -> bool:
        return not killed and mode is FeatureMode.SHADOW


class ModelReliabilityReleaseDecision(RuntimeContract):
    """Content-free decisions consumed later by routing/middleware composition."""

    same_deployment_retry_enabled: bool
    alternate_route_enabled: bool
    equivalent_route_enabled: bool
    circuit_influence_enabled: bool
    retry_shadow_observation: bool
    alternate_route_shadow_observation: bool
    equivalent_route_shadow_observation: bool
    circuit_shadow_observation: bool

    @property
    def primary_only(self) -> bool:
        return not (
            self.same_deployment_retry_enabled
            or self.alternate_route_enabled
            or self.equivalent_route_enabled
        )


__all__ = ("ModelReliabilityReleaseControls", "ModelReliabilityReleaseDecision")
