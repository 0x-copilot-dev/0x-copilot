"""Closed F3 activation policy resolved through the shared feature-mode seam.

Activation is a *narrowing dial inside* the existing
:class:`~agent_runtime.control_plane.feature_modes.FeatureMode` vocabulary, not
a second rollout vocabulary.  The resolved feature mode is a hard ceiling:
``off`` can never produce more than :attr:`CapabilityActivationMode.DIRECT`,
``shadow`` can never produce more than :attr:`CapabilityActivationMode.SHADOW`,
and only ``enforce`` may reach :attr:`CapabilityActivationMode.DEFERRED`.

The rank ordering measures *how much F3 machinery is active*, not how much
authority a caller holds.  ``direct`` is the pre-F3 disclosure path and is
therefore the conservative default: an absent, unknown, or unparseable control
value always resolves there, so a malformed input can only remove the new
discovery path.  A live constraint or kill switch may narrow an already-bound
activation; nothing here can widen one.

Activation never grants authority.  Every mode still projects a catalog that is
a subset of what the caller is already authorized to use, and every inner
operation still enters the Operation Gateway.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeDecision,
    FeatureModeResolver,
)
from agent_runtime.execution.contracts import RuntimeContract


class CapabilityActivationError(ValueError):
    """Typed, model-safe failure of an F3 activation resolution."""


class CapabilityActivationMode(StrEnum):
    """Closed F3 activation postures ordered by F3 machinery depth."""

    DIRECT = "direct"
    SERVER = "server"
    SHADOW = "shadow"
    DEFERRED = "deferred"

    class Messages:
        """Safe public messages for activation mode composition."""

        NO_MODES = "at least one activation mode is required"
        UNKNOWN_FEATURE_MODE = "unsupported feature mode for activation"

    @property
    def rank(self) -> int:
        """Return how much F3-owned machinery this posture activates."""

        return {
            CapabilityActivationMode.DIRECT: 0,
            CapabilityActivationMode.SERVER: 1,
            CapabilityActivationMode.SHADOW: 2,
            CapabilityActivationMode.DEFERRED: 3,
        }[self]

    @property
    def registers_bridge(self) -> bool:
        """Return whether the bounded discovery bridge tools may be registered."""

        return self is CapabilityActivationMode.DEFERRED

    @property
    def observes_only(self) -> bool:
        """Return whether the ranker may record candidates without model effect."""

        return self is CapabilityActivationMode.SHADOW

    @property
    def uses_existing_disclosure(self) -> bool:
        """Return whether the model keeps the pre-F3 direct/server disclosure."""

        return self in {
            CapabilityActivationMode.DIRECT,
            CapabilityActivationMode.SERVER,
        }

    @classmethod
    def narrowest(
        cls, *modes: "CapabilityActivationMode"
    ) -> "CapabilityActivationMode":
        """Return the least-activating supplied posture."""

        if not modes:
            raise CapabilityActivationError(cls.Messages.NO_MODES)
        return min(modes, key=lambda mode: mode.rank)

    @classmethod
    def ceiling_for(cls, mode: FeatureMode) -> "CapabilityActivationMode":
        """Return the widest activation the resolved feature mode permits."""

        try:
            return {
                FeatureMode.OFF: cls.DIRECT,
                FeatureMode.SHADOW: cls.SHADOW,
                FeatureMode.ENFORCE: cls.DEFERRED,
            }[mode]
        except KeyError as exc:  # pragma: no cover - FeatureMode is closed.
            raise CapabilityActivationError(cls.Messages.UNKNOWN_FEATURE_MODE) from exc

    @classmethod
    def parse(cls, raw_activation: object) -> "CapabilityActivationMode | None":
        """Parse an untrusted value without retaining or echoing it."""

        if isinstance(raw_activation, CapabilityActivationMode):
            return raw_activation
        if not isinstance(raw_activation, str):
            return None
        normalized = raw_activation.strip().lower()
        if not normalized:
            return None
        try:
            return cls(normalized)
        except ValueError:
            return None


class CapabilityActivationReason(StrEnum):
    """Low-cardinality reason for a resolved activation posture."""

    CONFIGURED = "configured"
    DEFAULTED_DIRECT = "defaulted_direct"
    UNKNOWN_DEFAULTED_SAFE = "unknown_defaulted_safe"
    FEATURE_MODE_CEILING = "feature_mode_ceiling"
    LIVE_CONSTRAINT = "live_constraint"
    KILL_SWITCHED = "kill_switched"


class CapabilityActivationDecision(RuntimeContract):
    """Content-free result of composing F3 activation with its feature mode.

    The invariants are enforced structurally, so a widening decision cannot be
    represented at all: the effective posture never exceeds the feature mode's
    ceiling, and it never exceeds what was requested.
    """

    mode: FeatureModeDecision
    requested_activation: CapabilityActivationMode | None = None
    effective_activation: CapabilityActivationMode
    reason: CapabilityActivationReason

    class Messages:
        """Safe public messages for activation decision invariants."""

        WRONG_FEATURE = "activation decisions belong to the F3 discovery feature"
        ABOVE_CEILING = "activation cannot exceed the resolved feature mode"
        WIDENS_REQUEST = "activation cannot exceed the requested posture"

    @model_validator(mode="after")
    def _activation_only_narrows(self) -> Self:
        if self.mode.feature is not AgentQualityFeature.F3_CAPABILITY_DISCOVERY:
            raise CapabilityActivationError(self.Messages.WRONG_FEATURE)
        ceiling = CapabilityActivationMode.ceiling_for(self.mode.effective_mode)
        if self.effective_activation.rank > ceiling.rank:
            raise CapabilityActivationError(self.Messages.ABOVE_CEILING)
        if (
            self.requested_activation is not None
            and self.effective_activation.rank > self.requested_activation.rank
        ):
            raise CapabilityActivationError(self.Messages.WIDENS_REQUEST)
        return self

    @property
    def registers_bridge(self) -> bool:
        """Return whether the bounded discovery bridge tools may be registered."""

        return self.effective_activation.registers_bridge

    @property
    def observes_only(self) -> bool:
        """Return whether the ranker may record candidates without model effect."""

        return self.effective_activation.observes_only

    @property
    def uses_existing_disclosure(self) -> bool:
        """Return whether the model keeps the pre-F3 direct/server disclosure."""

        return self.effective_activation.uses_existing_disclosure

    def narrowed_to(
        self,
        activation: CapabilityActivationMode,
        *,
        reason: CapabilityActivationReason,
    ) -> "CapabilityActivationDecision":
        """Return an equal-or-narrower decision; a wider posture is ignored."""

        narrowed = CapabilityActivationMode.narrowest(
            self.effective_activation,
            activation,
        )
        if narrowed is self.effective_activation:
            return self
        return CapabilityActivationDecision(
            mode=self.mode,
            requested_activation=self.requested_activation,
            effective_activation=narrowed,
            reason=reason,
        )


class CapabilityActivationResolver:
    """Compose F3 activation with the shared, authoritative feature-mode seam."""

    _FEATURE = AgentQualityFeature.F3_CAPABILITY_DISCOVERY

    def __init__(self, *, feature_modes: FeatureModeResolver | None = None) -> None:
        self._feature_modes = feature_modes or FeatureModeResolver()

    def resolve_configured(
        self,
        *,
        raw_mode: object = None,
        raw_activation: object = None,
    ) -> CapabilityActivationDecision:
        """Resolve untrusted rollout configuration into one safe posture."""

        mode = self._feature_modes.resolve_configured(
            feature=self._FEATURE,
            raw_mode=raw_mode,
        )
        return self._compose(mode=mode, raw_activation=raw_activation)

    def apply_live_constraint(
        self,
        *,
        snapshot_mode: FeatureMode,
        snapshot_activation: CapabilityActivationMode,
        raw_live_mode: object = None,
        raw_live_activation: object = None,
        kill_switch_asserted: bool = False,
    ) -> CapabilityActivationDecision:
        """Narrow an already-bound activation without ever increasing it.

        An absent live activation preserves the bound posture, subject to the
        live feature-mode ceiling.  A malformed live activation is treated like
        an emergency shutdown and falls back to the pre-F3 disclosure path.  An
        asserted kill switch always wins.
        """

        mode = self._feature_modes.apply_live_constraint(
            feature=self._FEATURE,
            snapshot_mode=snapshot_mode,
            raw_live_mode=raw_live_mode,
            kill_switch_asserted=kill_switch_asserted,
        )
        ceiling = CapabilityActivationMode.ceiling_for(mode.effective_mode)
        if mode.kill_switch_asserted:
            return CapabilityActivationDecision(
                mode=mode,
                requested_activation=snapshot_activation,
                effective_activation=CapabilityActivationMode.narrowest(
                    snapshot_activation,
                    ceiling,
                ),
                reason=CapabilityActivationReason.KILL_SWITCHED,
            )
        if self._is_absent(raw_live_activation):
            return CapabilityActivationDecision(
                mode=mode,
                requested_activation=snapshot_activation,
                effective_activation=CapabilityActivationMode.narrowest(
                    snapshot_activation,
                    ceiling,
                ),
                reason=self._reason_for(
                    requested=snapshot_activation,
                    ceiling=ceiling,
                    live_supplied=False,
                ),
            )
        live_activation = CapabilityActivationMode.parse(raw_live_activation)
        if live_activation is None:
            return CapabilityActivationDecision(
                mode=mode,
                requested_activation=snapshot_activation,
                effective_activation=CapabilityActivationMode.DIRECT,
                reason=CapabilityActivationReason.UNKNOWN_DEFAULTED_SAFE,
            )
        return CapabilityActivationDecision(
            mode=mode,
            requested_activation=snapshot_activation,
            effective_activation=CapabilityActivationMode.narrowest(
                snapshot_activation,
                live_activation,
                ceiling,
            ),
            reason=self._reason_for(
                requested=snapshot_activation,
                ceiling=ceiling,
                live_supplied=True,
                live_activation=live_activation,
            ),
        )

    def _compose(
        self,
        *,
        mode: FeatureModeDecision,
        raw_activation: object,
    ) -> CapabilityActivationDecision:
        ceiling = CapabilityActivationMode.ceiling_for(mode.effective_mode)
        if self._is_absent(raw_activation):
            return CapabilityActivationDecision(
                mode=mode,
                effective_activation=CapabilityActivationMode.DIRECT,
                reason=CapabilityActivationReason.DEFAULTED_DIRECT,
            )
        requested = CapabilityActivationMode.parse(raw_activation)
        if requested is None:
            return CapabilityActivationDecision(
                mode=mode,
                effective_activation=CapabilityActivationMode.DIRECT,
                reason=CapabilityActivationReason.UNKNOWN_DEFAULTED_SAFE,
            )
        return CapabilityActivationDecision(
            mode=mode,
            requested_activation=requested,
            effective_activation=CapabilityActivationMode.narrowest(requested, ceiling),
            reason=self._reason_for(
                requested=requested,
                ceiling=ceiling,
                live_supplied=False,
            ),
        )

    @staticmethod
    def _is_absent(raw_value: object) -> bool:
        return raw_value is None or (
            isinstance(raw_value, str) and not raw_value.strip()
        )

    @staticmethod
    def _reason_for(
        *,
        requested: CapabilityActivationMode,
        ceiling: CapabilityActivationMode,
        live_supplied: bool,
        live_activation: CapabilityActivationMode | None = None,
    ) -> CapabilityActivationReason:
        if live_supplied and live_activation is not None:
            if live_activation.rank < min(requested.rank, ceiling.rank):
                return CapabilityActivationReason.LIVE_CONSTRAINT
        if ceiling.rank < requested.rank:
            return CapabilityActivationReason.FEATURE_MODE_CEILING
        if live_supplied:
            return CapabilityActivationReason.LIVE_CONSTRAINT
        return CapabilityActivationReason.CONFIGURED


__all__ = (
    "CapabilityActivationDecision",
    "CapabilityActivationError",
    "CapabilityActivationMode",
    "CapabilityActivationReason",
    "CapabilityActivationResolver",
)
