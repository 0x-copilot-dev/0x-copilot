"""Bounded D1/D2/D6 cohort and promotion-control tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.rollout import (
    E2RolloutResolution,
    E2RolloutSettings,
    RolloutCapability,
    RolloutMode,
)
from agent_runtime.rollout_control import (
    CohortAdmissionOutcome,
    CohortConfigurationSource,
    CohortMatchScope,
    CohortSubjectSource,
    RolloutCohortConfigurationError,
    RolloutCohortPolicy,
    RolloutControlObserver,
    RolloutDiagnosticKind,
    RolloutSoakMetrics,
    RolloutSoakRequirements,
    RolloutTransitionOutcome,
    SoakEvaluationOutcome,
    VerifiedRolloutCohortSubject,
    evaluate_soak,
    validate_transition,
)


def _resolution(mode: RolloutMode) -> E2RolloutResolution:
    return E2RolloutResolution(
        modes=E2RolloutSettings(operation_gateway=mode),
    )


def _subject(*, user_id: str = "user-7") -> VerifiedRolloutCohortSubject:
    return VerifiedRolloutCohortSubject.from_verified_identity(
        {
            "source": CohortSubjectSource.VERIFIED_IDENTITY,
            "org_id": "org-1",
            "user_id": user_id,
            "device_id": "device-2",
            "connector_id": "connector-3",
        }
    )


def _policy() -> RolloutCohortPolicy:
    return RolloutCohortPolicy.from_trusted_deployment(
        {
            "source": CohortConfigurationSource.TRUSTED_DEPLOYMENT,
            "rules": [
                {
                    "capability": RolloutCapability.OPERATION_GATEWAY,
                    "org_id": "org-1",
                    "user_id": "user-7",
                }
            ],
        }
    )


def _requirements() -> RolloutSoakRequirements:
    return RolloutSoakRequirements(
        minimum_soak=timedelta(minutes=10),
        minimum_admissions=100,
        maximum_mismatch_rate=0.01,
        maximum_error_rate=0.02,
    )


def test_cohort_configuration_and_subject_require_trusted_sources() -> None:
    with pytest.raises(RolloutCohortConfigurationError, match="malformed"):
        RolloutCohortPolicy.from_trusted_deployment(
            {
                "source": CohortConfigurationSource.REQUEST,
                "rules": [
                    {
                        "capability": RolloutCapability.OPERATION_GATEWAY,
                        "org_id": "org-1",
                    }
                ],
            }
        )
    with pytest.raises(RolloutCohortConfigurationError, match="verified identity"):
        VerifiedRolloutCohortSubject.from_verified_identity(
            {"source": CohortSubjectSource.REQUEST, "org_id": "org-1"}
        )


@pytest.mark.parametrize(
    "document",
    (
        {"source": CohortConfigurationSource.TRUSTED_DEPLOYMENT, "rules": [{}]},
        {
            "source": CohortConfigurationSource.TRUSTED_DEPLOYMENT,
            "rules": [
                {
                    "capability": RolloutCapability.OPERATION_GATEWAY,
                    "org_id": "contains a space",
                }
            ],
        },
        {
            "source": CohortConfigurationSource.TRUSTED_DEPLOYMENT,
            "rules": [
                {
                    "capability": RolloutCapability.OPERATION_GATEWAY,
                    "org_id": "org-1",
                    "unexpected": "request data",
                }
            ],
        },
    ),
)
def test_malformed_criteria_fail_closed_without_echoing_input(document: object) -> None:
    with pytest.raises(RolloutCohortConfigurationError) as error:
        RolloutCohortPolicy.from_trusted_deployment(document)

    assert "contains a space" not in str(error.value)
    assert "request data" not in str(error.value)


def test_cohort_admission_matches_all_trusted_dimensions_and_global_mode() -> None:
    policy = _policy()
    admitted = policy.admit(
        resolution=_resolution(RolloutMode.SHADOW),
        subject=_subject(),
        capability=RolloutCapability.OPERATION_GATEWAY,
    )
    nonmatching = policy.admit(
        resolution=_resolution(RolloutMode.SHADOW),
        subject=_subject(user_id="user-8"),
        capability=RolloutCapability.OPERATION_GATEWAY,
    )
    globally_off = policy.admit(
        resolution=_resolution(RolloutMode.OFF),
        subject=_subject(),
        capability=RolloutCapability.OPERATION_GATEWAY,
    )

    assert admitted.outcome is CohortAdmissionOutcome.ADMITTED
    assert admitted.match_scope is CohortMatchScope.COMPOSITE
    assert nonmatching.outcome is CohortAdmissionOutcome.NO_MATCHING_COHORT
    assert globally_off.outcome is CohortAdmissionOutcome.GLOBAL_OFF


def test_soak_evaluator_requires_the_full_interval_and_uses_explicit_rates() -> None:
    started_at = datetime(2026, 7, 1, 12, tzinfo=UTC)
    metrics = RolloutSoakMetrics(
        cohort_admissions=100,
        shadow_comparisons=1000,
        shadow_mismatches=10,
        evaluation_errors=20,
    )
    before_boundary = evaluate_soak(
        capability=RolloutCapability.OPERATION_GATEWAY,
        started_at=started_at,
        observed_at=started_at + timedelta(minutes=10, microseconds=-1),
        metrics_snapshot=metrics,
        requirements=_requirements(),
    )
    at_boundary = evaluate_soak(
        capability=RolloutCapability.OPERATION_GATEWAY,
        started_at=started_at,
        observed_at=started_at + timedelta(minutes=10),
        metrics_snapshot=metrics,
        requirements=_requirements(),
    )

    assert before_boundary.outcome is SoakEvaluationOutcome.INSUFFICIENT_SOAK
    assert at_boundary.outcome is SoakEvaluationOutcome.PROMOTE
    assert at_boundary.mismatch_rate == 0.01
    assert at_boundary.error_rate == 0.02


def test_invalid_metric_combinations_and_time_windows_fail_closed() -> None:
    with pytest.raises(ValueError, match="mismatches"):
        RolloutSoakMetrics(
            cohort_admissions=1,
            shadow_comparisons=1,
            shadow_mismatches=2,
            evaluation_errors=0,
        )
    with pytest.raises(RolloutCohortConfigurationError, match="timezone-aware"):
        evaluate_soak(
            capability=RolloutCapability.OPERATION_GATEWAY,
            started_at=datetime(2026, 7, 1, 12),
            observed_at=datetime(2026, 7, 1, 13),
            metrics_snapshot=RolloutSoakMetrics(
                cohort_admissions=1,
                shadow_comparisons=1,
                shadow_mismatches=0,
                evaluation_errors=0,
            ),
            requirements=_requirements(),
        )


def test_transition_validation_requires_shadow_soak_and_allows_safe_rollback() -> None:
    started_at = datetime(2026, 7, 1, 12, tzinfo=UTC)
    promoted = evaluate_soak(
        capability=RolloutCapability.OPERATION_GATEWAY,
        started_at=started_at,
        observed_at=started_at + timedelta(minutes=10),
        metrics_snapshot=RolloutSoakMetrics(
            cohort_admissions=100,
            shadow_comparisons=100,
            shadow_mismatches=0,
            evaluation_errors=0,
        ),
        requirements=_requirements(),
    )

    direct_enforce = validate_transition(
        capability=RolloutCapability.OPERATION_GATEWAY,
        current_mode=RolloutMode.OFF,
        requested_mode=RolloutMode.ENFORCE,
        soak_decision=promoted,
    )
    admitted_promotion = validate_transition(
        capability=RolloutCapability.OPERATION_GATEWAY,
        current_mode=RolloutMode.SHADOW,
        requested_mode=RolloutMode.ENFORCE,
        soak_decision=promoted,
    )
    safe_rollback = validate_transition(
        capability=RolloutCapability.OPERATION_GATEWAY,
        current_mode=RolloutMode.ENFORCE,
        requested_mode=RolloutMode.OFF,
    )

    assert direct_enforce.outcome is RolloutTransitionOutcome.REJECTED
    assert admitted_promotion.outcome is RolloutTransitionOutcome.PROMOTION_ALLOWED
    assert safe_rollback.outcome is RolloutTransitionOutcome.ROLLBACK_ALLOWED
    assert not hasattr(safe_rollback, "dispatch")


class _MetricsSpy:
    def __init__(self) -> None:
        self.admissions: list[dict[str, object]] = []
        self.transitions: list[dict[str, object]] = []
        self.diagnostics: list[dict[str, object]] = []

    def cohort_admission(self, **kwargs: object) -> None:
        self.admissions.append(kwargs)

    def transition(self, **kwargs: object) -> None:
        self.transitions.append(kwargs)

    def diagnostic_sampled(self, **kwargs: object) -> None:
        self.diagnostics.append(kwargs)


class _DiagnosticSpy:
    def __init__(self) -> None:
        self.diagnostics: list[object] = []

    def record(self, diagnostic: object) -> None:
        self.diagnostics.append(diagnostic)


def test_admission_and_transition_telemetry_have_no_identifier_labels_or_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _MetricsSpy()
    diagnostics = _DiagnosticSpy()
    observer = RolloutControlObserver(metrics_port=metrics, diagnostic_sink=diagnostics)
    monkeypatch.setattr(
        "agent_runtime.rollout_control._protected_digest",
        lambda _value: "0" * 32,
    )
    admission = _policy().admit(
        resolution=_resolution(RolloutMode.SHADOW),
        subject=_subject(),
        capability=RolloutCapability.OPERATION_GATEWAY,
    )
    rollback = validate_transition(
        capability=RolloutCapability.OPERATION_GATEWAY,
        current_mode=RolloutMode.ENFORCE,
        requested_mode=RolloutMode.OFF,
    )

    observer.admission(admission, diagnostic_sample_key="org-1:user-7:device-2")
    observer.transition(rollback, diagnostic_sample_key="org-1:user-7:device-2")

    rendered = repr((metrics.admissions, metrics.transitions, diagnostics.diagnostics))
    assert "org-1" not in rendered
    assert "user-7" not in rendered
    assert "device-2" not in rendered
    assert metrics.admissions[0]["match_scope"] is CohortMatchScope.COMPOSITE
    assert (
        metrics.transitions[0]["outcome"] is RolloutTransitionOutcome.ROLLBACK_ALLOWED
    )
    assert metrics.diagnostics == [
        {"kind": RolloutDiagnosticKind.COHORT_ADMISSION},
        {"kind": RolloutDiagnosticKind.TRANSITION},
    ]
