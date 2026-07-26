"""E2 admission for the legacy staged-MCP compatibility lane.

``WriteStager`` is the sole producer of the D1/D3 staged-write ledger state;
the worker's stage-commit adapter is its sole consumer.  This small domain
adapter gives both ends the same persisted E2 lane mark without teaching either
one about settings, queues, or connector dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.effects.rollout import effect_execution_capabilities
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import (
    E2GovernedLane,
    E2RolloutAdmission,
)
from agent_runtime.rollout_control import VerifiedRolloutCohortFactsProvider
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


_STAGED_MCP_CAPABILITIES = effect_execution_capabilities(EffectExecutorKind.MCP)


@dataclass(frozen=True)
class StagedWriteRolloutGate:
    """Shared request/worker gate for D1/D3 work that can dispatch through MCP.

    A new stage receives a mark only when this capability group is explicitly
    controlled.  The mark is immutable ledger data; continuation therefore
    cannot fall back to legacy behavior after a configuration reload.
    """

    admission: E2RolloutAdmission

    @property
    def capabilities(self) -> tuple[RolloutCapability, ...]:
        """Return the one closed MCP dependency group used at both boundaries."""

        return _STAGED_MCP_CAPABILITIES

    def admit_new(
        self,
        *,
        facts_provider: VerifiedRolloutCohortFactsProvider,
    ) -> E2GovernedLane | None:
        """Admit a fresh stage before it can append any ledger event."""

        return self.admission.begin_governed_lane(
            capabilities=self.capabilities,
            facts_provider=facts_provider,
        )

    def require_continuation(
        self,
        *,
        lane: E2GovernedLane | None,
        malformed_mark: bool,
        facts_provider: VerifiedRolloutCohortFactsProvider,
    ) -> None:
        """Require a marked stage to remain admitted before it mutates or sends.

        Unmarked historic stages are intentionally compatibility work.  A bad
        mark is *not* historic work: it is denied rather than discarded into a
        legacy path.
        """

        if malformed_mark:
            from agent_runtime.rollout_admission import (  # noqa: PLC0415
                E2RolloutAdmissionDenied,
            )

            raise E2RolloutAdmissionDenied("The staged write rollout mark is invalid.")
        if lane is None:
            return
        self.admission.require_governed_lane(
            lane=lane,
            facts_provider=facts_provider,
        )


__all__ = ("StagedWriteRolloutGate",)
