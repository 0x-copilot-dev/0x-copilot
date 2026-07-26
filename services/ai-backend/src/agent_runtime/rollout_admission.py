"""Runtime enforcement for the closed E2 rollout contract.

``rollout`` owns the immutable ten-lane mode resolution and
``rollout_control`` owns the pure cohort selector.  This module is the one
runtime boundary that turns those two control-plane values into an admission
decision.  It deliberately has no executor, queue, route, or feature builder:
adapters must ask it *before* exposing a v2.1 capability or dispatching an
effect.

The scope is intentionally precise.  Existing v2 paths that predate E2 retain
their established compatibility behaviour until their owning migration moves
them into an explicit E2 lane.  A newly explicit E2 lane, however, can never
fall through to that compatibility path: it requires an ``enforce`` mode, a
matching server-verified cohort rule, and no targeted emergency kill switch.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.rollout import E2RolloutResolution, RolloutCapability, RolloutMode
from agent_runtime.rollout_control import (
    CohortAdmissionDecision,
    CohortAdmissionOutcome,
    RolloutCohortPolicy,
    RolloutControlObserver,
    VerifiedRolloutCohortFacts,
    VerifiedRolloutCohortFactsProvider,
    VerifiedRolloutCohortSubject,
)


_KILL_SWITCHES_ENVIRONMENT_NAME = "E2_ROLLOUT_KILL_SWITCHES_JSON"


class RolloutKillSwitchConfigurationError(ValueError):
    """Safe startup error for malformed targeted E2 emergency controls."""


class _StartupKillSwitchDocument(RuntimeContract):
    """Private, deployment-only document for targeted capability shutdowns."""

    disabled_capabilities: tuple[RolloutCapability, ...] = Field(
        default_factory=tuple,
        max_length=len(RolloutCapability),
    )

    @model_validator(mode="after")
    def _unique_capabilities(self) -> "_StartupKillSwitchDocument":
        if len(self.disabled_capabilities) != len(set(self.disabled_capabilities)):
            raise ValueError("rollout kill-switch capabilities must be unique")
        return self


class E2RolloutKillSwitches(RuntimeContract):
    """Immutable targeted kill switches resolved only at process config load.

    This is deliberately a closed capability list instead of an unscoped
    ``E2_ENABLED=false`` master switch.  An operator can immediately remove
    one consequential lane after a configuration reload without accidentally
    widening or restoring another lane.
    """

    disabled_capabilities: tuple[RolloutCapability, ...] = Field(default_factory=tuple)

    @classmethod
    def from_startup_environment(
        cls, environment: Mapping[str, str]
    ) -> "E2RolloutKillSwitches":
        """Load the targeted rollback list from trusted deployment config.

        ``E2_ROLLOUT_KILL_SWITCHES_JSON`` is a JSON array of closed capability
        names.  Missing/blank config means no emergency switch is asserted;
        rollout modes still default to ``off``.  Malformed or duplicate values
        fail configuration load with a value-free message.
        """

        try:
            raw = environment.get(_KILL_SWITCHES_ENVIRONMENT_NAME)
            if raw is None or not raw.strip():
                return cls()
            if not isinstance(raw, str):
                raise TypeError
            document = json.loads(raw)
            if not isinstance(document, list):
                raise TypeError
            parsed = _StartupKillSwitchDocument.model_validate(
                {"disabled_capabilities": document}
            )
        except Exception as exc:
            raise RolloutKillSwitchConfigurationError(
                f"{_KILL_SWITCHES_ENVIRONMENT_NAME} is malformed"
            ) from exc
        return cls(disabled_capabilities=parsed.disabled_capabilities)

    def disables(self, capability: RolloutCapability) -> bool:
        """Return whether the explicit rollback control blocks ``capability``."""

        return capability in self.disabled_capabilities


@dataclass(frozen=True)
class PersistedRunCohortFactsProvider:
    """Trusted cohort facts copied from an already-verified persisted run.

    This adapter intentionally accepts only server-owned values.  API payloads,
    model arguments, and plain request mappings cannot satisfy the
    ``VerifiedRolloutCohortFactsProvider`` protocol.
    """

    org_id: str
    user_id: str
    device_id: str | None = None
    connector_id: str | None = None

    def verified_rollout_cohort_facts(self) -> VerifiedRolloutCohortFacts:
        return VerifiedRolloutCohortFacts(
            org_id=self.org_id,
            user_id=self.user_id,
            device_id=self.device_id,
            connector_id=self.connector_id,
        )


class E2RuntimeAdmissionOutcome(StrEnum):
    """Closed outcome vocabulary for an actual runtime feature admission."""

    LEGACY_PASSTHROUGH = "legacy_passthrough"
    ADMITTED = "admitted"
    GLOBAL_OFF = "global_off"
    NO_MATCHING_COHORT = "no_matching_cohort"
    MODE_NOT_ENFORCED = "mode_not_enforced"
    KILL_SWITCHED = "kill_switched"


class E2RuntimeAdmissionDecision(RuntimeContract):
    """One auditable runtime decision with no execution or routing capability."""

    capability: RolloutCapability
    mode: RolloutMode
    outcome: E2RuntimeAdmissionOutcome
    cohort: CohortAdmissionDecision | None = None

    @property
    def permitted(self) -> bool:
        """Return whether this capability may be exposed or execute now."""

        return self.outcome in {
            E2RuntimeAdmissionOutcome.LEGACY_PASSTHROUGH,
            E2RuntimeAdmissionOutcome.ADMITTED,
        }


class E2RolloutAdmissionDenied(RuntimeError):
    """Safe domain signal used by request/worker adapters to halt a capability."""


class E2RolloutAdmission:
    """Shared v2.1 admission boundary for request and worker paths.

    An E2 lane is *controlled* when it was explicitly requested in ``enforce``
    or an emergency switch targets it.  A set of capabilities is controlled as
    one unit: if any member is E2-controlled, every member must be in enforce,
    match the same persisted-run cohort, and remain outside the kill-switch
    list.  This prevents a partially configured dependency graph from falling
    through to an older direct path.
    """

    def __init__(
        self,
        *,
        resolution: E2RolloutResolution,
        cohorts: RolloutCohortPolicy,
        kill_switches: E2RolloutKillSwitches,
        observer: RolloutControlObserver | None = None,
    ) -> None:
        self._resolution = resolution
        self._cohorts = cohorts
        self._kill_switches = kill_switches
        self._observer = observer or RolloutControlObserver()

    def decision(
        self,
        *,
        capability: RolloutCapability,
        facts_provider: VerifiedRolloutCohortFactsProvider,
    ) -> E2RuntimeAdmissionDecision:
        """Resolve exactly one capability against immutable runtime controls."""

        mode = self._resolution.modes.mode_for(capability)
        if self._kill_switches.disables(capability):
            return E2RuntimeAdmissionDecision(
                capability=capability,
                mode=mode,
                outcome=E2RuntimeAdmissionOutcome.KILL_SWITCHED,
            )
        if not self._is_explicitly_controlled(capability):
            return E2RuntimeAdmissionDecision(
                capability=capability,
                mode=mode,
                outcome=E2RuntimeAdmissionOutcome.LEGACY_PASSTHROUGH,
            )

        subject = VerifiedRolloutCohortSubject.from_verified_facts_provider(
            facts_provider
        )
        cohort = self._cohorts.admit(
            resolution=self._resolution,
            subject=subject,
            capability=capability,
        )
        # Telemetry is fail-soft by construction and receives no raw identity.
        self._observer.admission(cohort)
        if cohort.outcome is CohortAdmissionOutcome.GLOBAL_OFF:
            outcome = E2RuntimeAdmissionOutcome.GLOBAL_OFF
        elif cohort.outcome is CohortAdmissionOutcome.NO_MATCHING_COHORT:
            outcome = E2RuntimeAdmissionOutcome.NO_MATCHING_COHORT
        elif cohort.mode is not RolloutMode.ENFORCE:
            outcome = E2RuntimeAdmissionOutcome.MODE_NOT_ENFORCED
        else:
            outcome = E2RuntimeAdmissionOutcome.ADMITTED
        return E2RuntimeAdmissionDecision(
            capability=capability,
            mode=mode,
            outcome=outcome,
            cohort=cohort,
        )

    def permits_all(
        self,
        *,
        capabilities: Iterable[RolloutCapability],
        facts_provider: VerifiedRolloutCohortFactsProvider,
    ) -> bool:
        """Require every dependency if any dependency enters the E2 rollout.

        Legacy-only groups retain their current behavior.  Once any member is
        explicitly E2-controlled (or killed), the full dependency group is
        resolved through cohort admission; no mixed E2/legacy execution path is
        available.
        """

        unique = tuple(dict.fromkeys(capabilities))
        if not unique:
            raise ValueError("an E2 capability admission requires a capability")
        controlled = any(
            self._is_explicitly_controlled(capability)
            or self._kill_switches.disables(capability)
            for capability in unique
        )
        if not controlled:
            return True
        return all(
            self.decision(capability=capability, facts_provider=facts_provider).outcome
            is E2RuntimeAdmissionOutcome.ADMITTED
            for capability in unique
        )

    def require_all(
        self,
        *,
        capabilities: Iterable[RolloutCapability],
        facts_provider: VerifiedRolloutCohortFactsProvider,
    ) -> None:
        """Raise a value-free domain signal unless the full group is admitted."""

        if not self.permits_all(
            capabilities=capabilities, facts_provider=facts_provider
        ):
            raise E2RolloutAdmissionDenied(
                "The requested capability is unavailable for this rollout cohort."
            )

    def _is_explicitly_controlled(self, capability: RolloutCapability) -> bool:
        return self._resolution.provenance.is_explicitly_enforced(capability)


__all__ = (
    "E2RolloutAdmission",
    "E2RolloutAdmissionDenied",
    "E2RolloutKillSwitches",
    "E2RuntimeAdmissionDecision",
    "E2RuntimeAdmissionOutcome",
    "PersistedRunCohortFactsProvider",
    "RolloutKillSwitchConfigurationError",
)
