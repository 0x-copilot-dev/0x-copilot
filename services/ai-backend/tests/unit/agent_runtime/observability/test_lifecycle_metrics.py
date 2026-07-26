"""D13 metric-contract and emission-seam canaries.

The important invariant is not merely that meters are created: every emitted
attribute must stay inside the checked-in closed registry, even when a caller
tries to pass an identifier, path, tenant, or other unbounded value.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
import re

import pytest
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from agent_runtime.observability.lifecycle_metrics import (
    LIFECYCLE_METRIC_REGISTRY,
    LifecycleMetricName,
    LifecycleOperationalMetrics,
    LifecyclePlanDecisionMetric,
)
from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceScheme
from agent_runtime.surfaces_v2.receipt_export_v2 import ReceiptExportV2Verifier
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairCandidateKind,
    RepairEffectState,
    RepairEvidenceState,
    RepairGraphCoverage,
    RepairLegalHoldState,
    RepairOwnerState,
    RepairPlanner,
    RepairPlanningRequest,
    RepairSnapshotRecord,
)
from agent_runtime.surfaces_v2.retention import (
    RetentionCandidate,
    RetentionCandidateKind,
    RetentionCandidateState,
    RetentionEnumerationCoverage,
    RetentionPlanner,
    RetentionPlanningPolicy,
    RetentionPlanningRequest,
    RetentionReferenceEnumeration,
)
from copilot_audit_chain import AuditChainSigner


_SAFE_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    """Install a fresh provider so this canary never reads global test state."""

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    previous = metrics_internal._METER_PROVIDER
    metrics_internal._METER_PROVIDER = provider
    try:
        yield reader
    finally:
        metrics_internal._METER_PROVIDER = previous


def _points(
    reader: InMemoryMetricReader, name: str
) -> list[tuple[dict[str, object], object]]:
    points: list[tuple[dict[str, object], object]] = []
    metrics_data = reader.get_metrics_data()
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    value = getattr(point, "value", getattr(point, "sum", None))
                    points.append((dict(point.attributes), value))
    return points


class _RecordingMetrics:
    """Narrow test double proving planners call only aggregate metric seams."""

    def __init__(self) -> None:
        self.plan_successes: list[dict[str, object]] = []
        self.plan_failures: list[dict[str, object]] = []
        self.retention_lags: list[dict[str, object]] = []
        self.reconcile_backlogs: list[dict[str, int]] = []

    def record_plan_success(self, **kwargs: object) -> None:
        self.plan_successes.append(dict(kwargs))

    def record_plan_failure(self, **kwargs: object) -> None:
        self.plan_failures.append(dict(kwargs))

    def record_retention_lag(self, **kwargs: object) -> None:
        self.retention_lags.append(dict(kwargs))

    def record_reconcile_backlog_snapshot(self, **kwargs: int) -> None:
        self.reconcile_backlogs.append(dict(kwargs))


class _VerificationMetrics:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_audit_verification(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def test_metric_registry_is_low_cardinality_and_safe() -> None:
    """Automated D13 canary: the registry cannot grow unsafe label values."""

    names = [definition.name for definition in LIFECYCLE_METRIC_REGISTRY]
    assert len(names) == len(set(names))
    assert set(names) == {
        LifecycleMetricName.PLAN_TOTAL,
        LifecycleMetricName.PLAN_DECISIONS_TOTAL,
        LifecycleMetricName.PLAN_DURATION_SECONDS,
        LifecycleMetricName.RETENTION_LAG_SECONDS,
        LifecycleMetricName.RECONCILE_BACKLOG_SNAPSHOT_ITEMS,
        LifecycleMetricName.RETENTION_EXECUTION_FAILURES_TOTAL,
        LifecycleMetricName.REPAIR_EXECUTION_TOTAL,
        LifecycleMetricName.AUDIT_VERIFICATION_TOTAL,
        LifecycleMetricName.AUTHORIZATION_DENIALS_TOTAL,
    }
    forbidden = {"tenant", "org", "user", "run", "path", "reference", "payload"}
    for definition in LIFECYCLE_METRIC_REGISTRY:
        assert definition.instrument in {"counter", "histogram", "observable_gauge"}
        for label_name, values in definition.labels:
            assert label_name not in forbidden
            assert _SAFE_LABEL.fullmatch(label_name)
            assert values
            assert all(_SAFE_LABEL.fullmatch(value) for value in values)
            assert not any("/" in value or ":" in value for value in values)


def test_untrusted_metric_inputs_are_collapsed_to_closed_labels(
    metric_reader: InMemoryMetricReader,
) -> None:
    metrics = LifecycleOperationalMetrics()
    unsafe = "org-acme/user@example.com/private/secret.md"
    metrics.record_plan_success(
        planner=unsafe,
        decisions=(
            LifecyclePlanDecisionMetric(
                candidate_kind=unsafe,
                disposition=unsafe,
            ),
        ),
        elapsed_seconds=0.01,
    )
    metrics.record_retention_lag(
        candidate_kind=unsafe,
        stage=unsafe,
        elapsed_seconds=3.0,
    )
    metrics.record_reconcile_backlog_snapshot(candidate_count=4, withheld_count=2)
    metrics.record_retention_execution_failure(kind=unsafe)
    metrics.record_repair_execution(action=unsafe, outcome=unsafe)
    metrics.record_audit_verification(format=unsafe, succeeded=False)
    metrics.record_authorization_denial(
        boundary=unsafe,
        reason=unsafe,
        enforcement=unsafe,
    )

    emitted: list[dict[str, object]] = []
    for definition in LIFECYCLE_METRIC_REGISTRY:
        allowed = dict(definition.labels)
        metric_points = _points(metric_reader, definition.name)
        for attributes, _ in metric_points:
            assert set(attributes) == set(allowed)
            assert all(value in allowed[label] for label, value in attributes.items())
        emitted.extend(attributes for attributes, _ in metric_points)

    assert emitted
    rendered = repr(emitted)
    assert unsafe not in rendered
    for attributes in emitted:
        assert all(
            isinstance(value, str) and _SAFE_LABEL.fullmatch(value)
            for value in attributes.values()
        )
    assert {
        attributes.get("planner") for attributes in emitted if "planner" in attributes
    } == {"other"}
    assert any(
        attributes == {"boundary": "other", "reason": "other", "enforcement": "unknown"}
        for attributes in emitted
    )


def test_retention_planner_emits_due_lag_without_candidate_or_tenant() -> None:
    metrics = _RecordingMetrics()
    candidate = RetentionCandidate(
        candidate_id="candidate_1",
        tenant_id="tenant_1",
        kind=RetentionCandidateKind.ARTIFACT_BLOB,
        state=RetentionCandidateState.ACTIVE,
        retention_expires_at=_NOW - timedelta(hours=2),
        enumeration=RetentionReferenceEnumeration(
            coverage=RetentionEnumerationCoverage.COMPLETE_TENANT,
        ),
    )
    request = RetentionPlanningRequest(
        tenant_id="tenant_1",
        snapshot_id="snapshot_1",
        as_of=_NOW,
        policy=RetentionPlanningPolicy(physical_grace_period=timedelta(hours=1)),
        candidates=(candidate,),
    )

    plan = RetentionPlanner(metrics=metrics).plan(request)  # type: ignore[arg-type]

    assert plan.decisions[0].state.value == "logical_tombstone_only"
    assert metrics.plan_successes[0]["planner"] == "retention"
    decision = metrics.plan_successes[0]["decisions"]
    assert isinstance(decision, Sequence)
    assert decision[0].candidate_kind == "artifact_blob"
    assert metrics.retention_lags == [
        {
            "candidate_kind": "artifact_blob",
            "stage": "tombstone_due",
            "elapsed_seconds": 7200.0,
        }
    ]
    assert "candidate_1" not in repr(metrics.plan_successes)
    assert "tenant_1" not in repr(metrics.plan_successes)


def test_repair_planner_emits_effect_reconcile_backlog_only() -> None:
    metrics = _RecordingMetrics()
    candidate = RepairSnapshotRecord(
        candidate_id="candidate_2",
        tenant_id="tenant_2",
        kind=RepairCandidateKind.EFFECT_RECONCILIATION,
        reference_scheme=LifecycleReferenceScheme.ARTIFACT_BLOB.value,
        graph_coverage=RepairGraphCoverage.COMPLETE,
        legal_hold=RepairLegalHoldState.NONE,
        evidence_state=RepairEvidenceState.VERIFIED,
        evidence_id="evidence_2",
        owner_state=RepairOwnerState.TERMINAL,
        effect_state=RepairEffectState.CLAIMED,
        reconcile_supported=True,
        quiet_period_elapsed=True,
    )
    request = RepairPlanningRequest(
        tenant_id="tenant_2",
        snapshot_id="snapshot_2",
        as_of=_NOW,
        records=(candidate,),
    )

    plan = RepairPlanner(metrics=metrics).plan(request)  # type: ignore[arg-type]

    assert plan.decisions[0].state.value == "candidate"
    assert metrics.reconcile_backlogs == [{"candidate_count": 1, "withheld_count": 0}]
    assert "candidate_2" not in repr(metrics.plan_successes)
    assert "tenant_2" not in repr(metrics.plan_successes)


def test_receipt_verifier_records_failure_without_raw_reason() -> None:
    metrics = _VerificationMetrics()
    verifier = ReceiptExportV2Verifier(
        signer=AuditChainSigner(keys={1: b"0123456789abcdef"}, active_version=1),
        metrics=metrics,  # type: ignore[arg-type]
    )

    result = verifier.verify({"bundle_version": 2, "rows": []})

    assert result.ok is False
    assert metrics.calls == [{"format": "receipt_v2", "succeeded": False}]
