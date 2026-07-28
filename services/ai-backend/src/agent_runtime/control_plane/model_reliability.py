"""Run-bound release authority for F10 recovery and circuit mechanisms.

This module is pure domain policy. Trusted startup adapters resolve mutable
configuration into :class:`ModelReliabilityLiveConstraints`; no environment
variables, request bodies, credentials, or provider responses are read here.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, field_validator, model_validator

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import RuntimeContract

_MAX_AUTHORITY_REF = 256


class ModelReliabilityControl(StrEnum):
    """Independent F10 mechanisms controlled by the run snapshot."""

    SAME_DEPLOYMENT_RETRY = "same_deployment_retry"
    ALTERNATE_ROUTE = "alternate_route"
    EQUIVALENT_ROUTE = "equivalent_route"
    CIRCUIT_INFLUENCE = "circuit_influence"


class ModelReliabilityControlSnapshot(RuntimeContract):
    """Immutable per-run modes and qualification authority references."""

    same_deployment_retry: FeatureMode = FeatureMode.OFF
    alternate_route: FeatureMode = FeatureMode.OFF
    equivalent_route: FeatureMode = FeatureMode.OFF
    circuit_influence: FeatureMode = FeatureMode.OFF
    qualification_authority_ref: str | None = Field(
        default=None,
        max_length=_MAX_AUTHORITY_REF,
    )
    qualification_authority_revision: str | None = Field(
        default=None,
        max_length=_MAX_AUTHORITY_REF,
    )

    _FIELD_BY_CONTROL: ClassVar[Mapping[ModelReliabilityControl, str]] = {
        control: control.value for control in ModelReliabilityControl
    }

    @field_validator(
        "qualification_authority_ref",
        "qualification_authority_revision",
    )
    @classmethod
    def _normalize_optional_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("qualification authority references must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _qualification_authority_is_complete(
        self,
    ) -> "ModelReliabilityControlSnapshot":
        has_ref = self.qualification_authority_ref is not None
        has_revision = self.qualification_authority_revision is not None
        if has_ref != has_revision:
            raise ValueError(
                "qualification authority ref and revision must be supplied together"
            )
        if self.equivalent_route is FeatureMode.ENFORCE and not has_ref:
            raise ValueError(
                "equivalent-route enforcement requires qualification authority"
            )
        return self

    def mode_for(self, control: ModelReliabilityControl) -> FeatureMode:
        """Return the immutable mode for one F10 subcontrol."""

        return getattr(self, self._FIELD_BY_CONTROL[control])

    def validate_parent(self, f10_mode: FeatureMode) -> None:
        """Reject a subcontrol posture broader than its owning F10 mode."""

        for control in ModelReliabilityControl:
            if self.mode_for(control).rank > f10_mode.rank:
                raise ValueError(
                    f"{control.value} cannot exceed the parent F10 feature mode"
                )

    @property
    def is_all_off(self) -> bool:
        return all(
            self.mode_for(control) is FeatureMode.OFF
            for control in ModelReliabilityControl
        )


class ModelReliabilityLiveConstraints(RuntimeContract):
    """Trusted mutable controls applied only as monotonic narrowing inputs."""

    modes: Mapping[ModelReliabilityControl, object] = Field(default_factory=dict)
    kill_switches: frozenset[ModelReliabilityControl] = Field(default_factory=frozenset)


class ModelReliabilityDecisionReason(StrEnum):
    """Body-free reason for one effective F10 subcontrol decision."""

    SNAPSHOT = "snapshot"
    PARENT_F10_OFF = "parent_f10_off"
    PARENT_F10_SHADOW = "parent_f10_shadow"
    LIVE_CONSTRAINT = "live_constraint"
    LIVE_UNKNOWN_DEFAULTED_OFF = "live_unknown_defaulted_off"
    KILL_SWITCHED = "kill_switched"


class ModelReliabilitySubcontrolDecision(RuntimeContract):
    """One immutable, low-cardinality F10 subcontrol decision."""

    control: ModelReliabilityControl
    snapshot_mode: FeatureMode
    effective_mode: FeatureMode
    reason: ModelReliabilityDecisionReason
    kill_switch_asserted: bool = False

    @property
    def observes(self) -> bool:
        return self.effective_mode in {FeatureMode.SHADOW, FeatureMode.ENFORCE}

    @property
    def enforces(self) -> bool:
        return self.effective_mode is FeatureMode.ENFORCE


class ModelReliabilityReleaseDecision(RuntimeContract):
    """Complete body-free release decision consumed by worker composition."""

    run_id: str = Field(min_length=1, max_length=160)
    snapshot_id: str = Field(min_length=1, max_length=160)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_f10_mode: FeatureMode
    same_deployment_retry: ModelReliabilitySubcontrolDecision
    alternate_route: ModelReliabilitySubcontrolDecision
    equivalent_route: ModelReliabilitySubcontrolDecision
    circuit_influence: ModelReliabilitySubcontrolDecision
    qualification_authority_ref: str | None = Field(
        default=None,
        max_length=_MAX_AUTHORITY_REF,
    )
    qualification_authority_revision: str | None = Field(
        default=None,
        max_length=_MAX_AUTHORITY_REF,
    )

    _FIELD_BY_CONTROL: ClassVar[Mapping[ModelReliabilityControl, str]] = {
        control: control.value for control in ModelReliabilityControl
    }

    @model_validator(mode="after")
    def _decision_is_consistent(self) -> "ModelReliabilityReleaseDecision":
        for control in ModelReliabilityControl:
            decision = self.for_control(control)
            if decision.control is not control:
                raise ValueError("model reliability decision control mismatch")
            if decision.effective_mode.rank > self.effective_f10_mode.rank:
                raise ValueError("model reliability decision exceeds F10 authority")
        if self.equivalent_route.enforces and (
            self.qualification_authority_ref is None
            or self.qualification_authority_revision is None
        ):
            raise ValueError(
                "equivalent-route enforcement lacks qualification authority"
            )
        return self

    def for_control(
        self,
        control: ModelReliabilityControl,
    ) -> ModelReliabilitySubcontrolDecision:
        return getattr(self, self._FIELD_BY_CONTROL[control])

    @property
    def same_deployment_retry_enabled(self) -> bool:
        return self.same_deployment_retry.enforces

    @property
    def alternate_route_enabled(self) -> bool:
        return self.alternate_route.enforces

    @property
    def equivalent_route_enabled(self) -> bool:
        return self.equivalent_route.enforces

    @property
    def circuit_influence_enabled(self) -> bool:
        return self.circuit_influence.enforces

    @property
    def retry_shadow_observation(self) -> bool:
        return self.same_deployment_retry.effective_mode is FeatureMode.SHADOW

    @property
    def alternate_route_shadow_observation(self) -> bool:
        return self.alternate_route.effective_mode is FeatureMode.SHADOW

    @property
    def equivalent_route_shadow_observation(self) -> bool:
        return self.equivalent_route.effective_mode is FeatureMode.SHADOW

    @property
    def circuit_shadow_observation(self) -> bool:
        return self.circuit_influence.effective_mode is FeatureMode.SHADOW

    @property
    def primary_only(self) -> bool:
        return not (
            self.same_deployment_retry_enabled
            or self.alternate_route_enabled
            or self.equivalent_route_enabled
        )

    @classmethod
    def safe_off(
        cls,
        *,
        run_id: str,
        snapshot_id: str,
        snapshot_digest: str,
    ) -> "ModelReliabilityReleaseDecision":
        return ModelReliabilityReleaseResolver().resolve(
            run_id=run_id,
            snapshot_id=snapshot_id,
            snapshot_digest=snapshot_digest,
            snapshot=ModelReliabilityControlSnapshot(),
            snapshot_f10_mode=FeatureMode.OFF,
            effective_f10_mode=FeatureMode.OFF,
        )


class ModelReliabilityReleaseResolver:
    """Resolve signed snapshot authority plus live, narrowing-only controls."""

    def resolve(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        snapshot_digest: str,
        snapshot: ModelReliabilityControlSnapshot,
        snapshot_f10_mode: FeatureMode,
        effective_f10_mode: FeatureMode,
        live: ModelReliabilityLiveConstraints | None = None,
    ) -> ModelReliabilityReleaseDecision:
        snapshot.validate_parent(snapshot_f10_mode)
        if effective_f10_mode.rank > snapshot_f10_mode.rank:
            raise ValueError("effective F10 mode cannot broaden the run snapshot")
        active_live = live or ModelReliabilityLiveConstraints()
        decisions = {
            control: self._resolve_control(
                control=control,
                snapshot_mode=snapshot.mode_for(control),
                effective_f10_mode=effective_f10_mode,
                raw_live_mode=active_live.modes.get(control),
                live_mode_supplied=control in active_live.modes,
                kill_switch_asserted=control in active_live.kill_switches,
            )
            for control in ModelReliabilityControl
        }
        return ModelReliabilityReleaseDecision(
            run_id=run_id,
            snapshot_id=snapshot_id,
            snapshot_digest=snapshot_digest,
            effective_f10_mode=effective_f10_mode,
            same_deployment_retry=decisions[
                ModelReliabilityControl.SAME_DEPLOYMENT_RETRY
            ],
            alternate_route=decisions[ModelReliabilityControl.ALTERNATE_ROUTE],
            equivalent_route=decisions[ModelReliabilityControl.EQUIVALENT_ROUTE],
            circuit_influence=decisions[ModelReliabilityControl.CIRCUIT_INFLUENCE],
            qualification_authority_ref=snapshot.qualification_authority_ref,
            qualification_authority_revision=(
                snapshot.qualification_authority_revision
            ),
        )

    @classmethod
    def _resolve_control(
        cls,
        *,
        control: ModelReliabilityControl,
        snapshot_mode: FeatureMode,
        effective_f10_mode: FeatureMode,
        raw_live_mode: object,
        live_mode_supplied: bool,
        kill_switch_asserted: bool,
    ) -> ModelReliabilitySubcontrolDecision:
        ceiling = FeatureMode.least_authoritative(
            snapshot_mode,
            effective_f10_mode,
        )
        if kill_switch_asserted:
            return ModelReliabilitySubcontrolDecision(
                control=control,
                snapshot_mode=snapshot_mode,
                effective_mode=FeatureMode.OFF,
                reason=ModelReliabilityDecisionReason.KILL_SWITCHED,
                kill_switch_asserted=True,
            )
        if (
            not live_mode_supplied
            or raw_live_mode is None
            or (isinstance(raw_live_mode, str) and not raw_live_mode.strip())
        ):
            return ModelReliabilitySubcontrolDecision(
                control=control,
                snapshot_mode=snapshot_mode,
                effective_mode=ceiling,
                reason=cls._parent_reason(
                    snapshot_mode=snapshot_mode,
                    effective_f10_mode=effective_f10_mode,
                ),
            )
        parsed = cls._parse(raw_live_mode)
        if parsed is None:
            return ModelReliabilitySubcontrolDecision(
                control=control,
                snapshot_mode=snapshot_mode,
                effective_mode=FeatureMode.OFF,
                reason=ModelReliabilityDecisionReason.LIVE_UNKNOWN_DEFAULTED_OFF,
            )
        return ModelReliabilitySubcontrolDecision(
            control=control,
            snapshot_mode=snapshot_mode,
            effective_mode=FeatureMode.least_authoritative(ceiling, parsed),
            reason=ModelReliabilityDecisionReason.LIVE_CONSTRAINT,
        )

    @staticmethod
    def _parse(raw_mode: object) -> FeatureMode | None:
        if isinstance(raw_mode, FeatureMode):
            return raw_mode
        if not isinstance(raw_mode, str):
            return None
        try:
            return FeatureMode(raw_mode.strip().lower())
        except ValueError:
            return None

    @staticmethod
    def _parent_reason(
        *,
        snapshot_mode: FeatureMode,
        effective_f10_mode: FeatureMode,
    ) -> ModelReliabilityDecisionReason:
        if (
            effective_f10_mode is FeatureMode.OFF
            and snapshot_mode is not FeatureMode.OFF
        ):
            return ModelReliabilityDecisionReason.PARENT_F10_OFF
        if (
            effective_f10_mode is FeatureMode.SHADOW
            and snapshot_mode is FeatureMode.ENFORCE
        ):
            return ModelReliabilityDecisionReason.PARENT_F10_SHADOW
        return ModelReliabilityDecisionReason.SNAPSHOT


__all__ = (
    "ModelReliabilityControl",
    "ModelReliabilityControlSnapshot",
    "ModelReliabilityDecisionReason",
    "ModelReliabilityLiveConstraints",
    "ModelReliabilityReleaseDecision",
    "ModelReliabilityReleaseResolver",
    "ModelReliabilitySubcontrolDecision",
)
