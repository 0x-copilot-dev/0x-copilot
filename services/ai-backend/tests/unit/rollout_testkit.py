"""Explicit rollout fixtures for domains that now require the E2 boundary."""

from __future__ import annotations

from agent_runtime.rollout import E2RolloutResolution
from agent_runtime.rollout_admission import E2RolloutAdmission, E2RolloutKillSwitches
from agent_runtime.rollout_control import RolloutCohortPolicy
from agent_runtime.surfaces_v2.stage_rollout import StagedWriteRolloutGate


def legacy_staged_write_gate() -> StagedWriteRolloutGate:
    """Return an explicitly uncontrolled gate for legacy-domain test fixtures.

    Production composition never uses this helper: it injects the process's
    resolved E2 settings. Tests must opt into compatibility deliberately so an
    accidental omitted gate is a constructor error rather than a hidden bypass.
    """

    return StagedWriteRolloutGate(
        admission=E2RolloutAdmission(
            resolution=E2RolloutResolution(),
            cohorts=RolloutCohortPolicy(),
            kill_switches=E2RolloutKillSwitches(),
        )
    )


__all__ = ("legacy_staged_write_gate",)
