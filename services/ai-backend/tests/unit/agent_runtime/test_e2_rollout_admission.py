"""Production E2 cohort/rollback admission tests.

These tests exercise the immutable settings snapshot that API and worker
composition both receive.  They intentionally do not call the pure
``RolloutCohortPolicy`` in isolation: the assertion target is the runtime
admission boundary that controls capability exposure and effect dispatch.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    E2RuntimeAdmissionOutcome,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.settings import RuntimeSettings


_MCP_EFFECT_CAPABILITIES = (
    RolloutCapability.OPERATION_GATEWAY,
    RolloutCapability.MCP_GATEWAY,
    RolloutCapability.EFFECT_STAGER,
    RolloutCapability.EFFECT_COMMIT,
)


def _environment(
    *, kill_switches: tuple[RolloutCapability, ...] = ()
) -> dict[str, str]:
    """Return one complete, safe E2 MCP-effect rollout configuration."""

    rules = [
        {
            "capability": capability.value,
            "org_id": "org_rollout",
            "user_id": "user_canary",
        }
        for capability in _MCP_EFFECT_CAPABILITIES
    ]
    environment = {
        "SURFACES_V2": "true",
        "ARTIFACT_EFFECTS_V2": "true",
        "ARTIFACT_DRAFTS_V2": "true",
        "OPERATION_GATEWAY_MODE": "enforce",
        "EFFECT_STAGER_MODE": "enforce",
        "EFFECT_COMMIT_MODE": "enforce",
        "MCP_GATEWAY_MODE": "enforce",
        "E2_ROLLOUT_COHORTS_JSON": json.dumps(rules),
    }
    if kill_switches:
        environment["E2_ROLLOUT_KILL_SWITCHES_JSON"] = json.dumps(
            [capability.value for capability in kill_switches]
        )
    return environment


def _admission(settings: RuntimeSettings) -> E2RolloutAdmission:
    return E2RolloutAdmission(
        resolution=settings.execution.rollout,
        cohorts=settings.execution.rollout_cohorts,
        kill_switches=settings.execution.rollout_kill_switches,
    )


def _facts(*, user_id: str = "user_canary") -> PersistedRunCohortFactsProvider:
    return PersistedRunCohortFactsProvider(org_id="org_rollout", user_id=user_id)


def test_runtime_admission_requires_the_full_persisted_cohort_dependency_set() -> None:
    admission = _admission(RuntimeSettings.load(environ=_environment()))

    assert admission.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(),
    )
    assert not admission.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(user_id="user_not_in_rollout"),
    )

    denied = admission.decision(
        capability=RolloutCapability.EFFECT_COMMIT,
        facts_provider=_facts(user_id="user_not_in_rollout"),
    )
    assert denied.outcome is E2RuntimeAdmissionOutcome.NO_MATCHING_COHORT


def test_targeted_kill_switch_overrides_an_admitted_cohort_before_exposure() -> None:
    admission = _admission(
        RuntimeSettings.load(
            environ=_environment(
                kill_switches=(RolloutCapability.EFFECT_COMMIT,),
            )
        )
    )

    decision = admission.decision(
        capability=RolloutCapability.EFFECT_COMMIT,
        facts_provider=_facts(),
    )

    assert decision.outcome is E2RuntimeAdmissionOutcome.KILL_SWITCHED
    assert not admission.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(),
    )


def test_cohort_and_rollback_snapshots_are_deterministic_across_restart_reload() -> (
    None
):
    environment = _environment()
    first = RuntimeSettings.load(environ=environment)
    restarted = RuntimeSettings.load(environ=dict(environment))

    first_admission = _admission(first)
    restarted_admission = _admission(restarted)
    assert first.execution.rollout == restarted.execution.rollout
    assert first.execution.rollout_cohorts == restarted.execution.rollout_cohorts
    assert first_admission.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(),
    )
    assert restarted_admission.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(),
    )

    # Process snapshots remain immutable after startup; a configuration reload
    # is represented by a new settings object, not a mutable in-flight policy.
    environment["E2_ROLLOUT_KILL_SWITCHES_JSON"] = json.dumps(
        [RolloutCapability.EFFECT_COMMIT.value]
    )
    reloaded = _admission(RuntimeSettings.load(environ=environment))

    assert first_admission.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(),
    )
    assert not reloaded.permits_all(
        capabilities=_MCP_EFFECT_CAPABILITIES,
        facts_provider=_facts(),
    )


@pytest.mark.parametrize(
    "raw",
    ("not-json", json.dumps(["effect_commit", "effect_commit"])),
)
def test_malformed_kill_switch_configuration_fails_closed_without_echoing_values(
    raw: str,
) -> None:
    with pytest.raises(ValueError, match="E2_ROLLOUT_KILL_SWITCHES_JSON") as error:
        RuntimeSettings.load(environ={"E2_ROLLOUT_KILL_SWITCHES_JSON": raw})

    assert raw not in str(error.value)
