"""Safe OpenTelemetry metrics for Generative Surfaces lifecycle operations.

This module is the D13 metric contract, not an operations runner.  It owns a
small, explicit registry of low-cardinality metric names and label values for
the lifecycle seams that exist today:

* D10/D11 retention planning and retention-sweeper failures;
* D12 repair/reconciliation planning backlog snapshots;
* opt-in physical artifact-cleanup execution;
* D7 receipt-export verification; and
* concrete runtime authorization-denial boundaries.

There are deliberately no tenant, user, run, artifact, connector, path,
reference, payload, exception-message, or raw error labels.  Inputs that do
not belong to a closed vocabulary are normalized to ``other``/``unknown``;
they never become a new time series.  Metric publication is best-effort and
cannot change a planner, verifier, request, or worker outcome.

The repair and retention planner metrics are emitted only when their existing
in-process planning APIs are invoked. The physical-cleanup counter is emitted
only by its explicit opt-in worker. This module does *not* imply that an
alerting backend has been deployed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from threading import Lock
from typing import Any, Final, Literal


logger = logging.getLogger(__name__)

_METER_NAME: Final = "agent_runtime.surfaces.lifecycle"
_OTHER: Final = "other"
_UNKNOWN: Final = "unknown"


class LifecycleMetricName:
    """Stable D13 metric names owned by this module."""

    PLAN_TOTAL = "surfaces_lifecycle_plan_total"
    PLAN_DECISIONS_TOTAL = "surfaces_lifecycle_plan_decisions_total"
    PLAN_DURATION_SECONDS = "surfaces_lifecycle_plan_duration_seconds"
    RETENTION_LAG_SECONDS = "surfaces_lifecycle_retention_lag_seconds"
    RECONCILE_BACKLOG_SNAPSHOT_ITEMS = (
        "surfaces_lifecycle_reconcile_backlog_snapshot_items"
    )
    RETENTION_EXECUTION_FAILURES_TOTAL = (
        "surfaces_lifecycle_retention_execution_failures_total"
    )
    REPAIR_EXECUTION_TOTAL = "surfaces_lifecycle_repair_execution_total"
    ARTIFACT_CLEANUP_EXECUTION_TOTAL = (
        "surfaces_lifecycle_artifact_cleanup_execution_total"
    )
    AUDIT_VERIFICATION_TOTAL = "surfaces_lifecycle_audit_verification_total"
    AUTHORIZATION_DENIALS_TOTAL = "surfaces_lifecycle_authorization_denials_total"


class LifecyclePlannerLabel:
    """Closed ``planner`` values for planning metrics."""

    RETENTION = "retention"
    REPAIR = "repair"


class LifecyclePlanOutcomeLabel:
    """Closed result labels for one planning invocation."""

    SUCCEEDED = "succeeded"
    REJECTED_INPUT = "rejected_input"
    FAILED = "failed"


class LifecycleDispositionLabel:
    """Closed union of the D10 and D12 decision states."""

    RETAIN = "retain"
    LOGICAL_TOMBSTONE_ONLY = "logical_tombstone_only"
    PHYSICALLY_ELIGIBLE = "physically_eligible"
    CANDIDATE = "candidate"
    WITHHELD = "withheld"


class RetentionLagStageLabel:
    """Closed lifecycle stages whose due-time lag can be observed safely."""

    TOMBSTONE_DUE = "tombstone_due"
    PHYSICAL_GC_DUE = "physical_gc_due"


class ReconcileBacklogStateLabel:
    """Closed D12 effect-reconciliation snapshot states."""

    CANDIDATE = "candidate"
    WITHHELD = "withheld"


class RepairExecutionActionLabel:
    """Closed executable D12 action families."""

    EFFECT_RECONCILE = "effect_reconcile"


class RepairExecutionOutcomeLabel:
    """Closed outcomes for the durable repair execution seam."""

    QUEUED = "queued"
    ALREADY_QUEUED = "already_queued"
    WITHHELD = "withheld"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ArtifactCleanupExecutionOutcomeLabel:
    """Closed outcomes for one physical artifact-cleanup tenant pass."""

    PURGED = "purged"
    QUARANTINED = "quarantined"
    REAPED = "reaped"
    RESTORED = "restored"
    WITHHELD = "withheld"
    ALREADY_CLEAN = "already_clean"
    FAILED = "failed"
    AUDIT_FAILED = "audit_failed"


class AuditVerificationFormatLabel:
    """Closed receipt verification formats supported by the v2 verifier."""

    RECEIPT_V1 = "receipt_v1"
    RECEIPT_V2 = "receipt_v2"


class VerificationOutcomeLabel:
    """Closed verifier outcomes."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuthorizationBoundaryLabel:
    """Concrete authorization boundaries instrumented by D13."""

    RBAC = "rbac"
    AUDIT_LIST_IDENTITY = "audit_list_identity"


class AuthorizationDenyReasonLabel:
    """Closed denial reasons; no caller-provided reason may become a label."""

    RBAC_DENIED = "rbac_denied"
    MFA_PENDING = "mfa_pending"
    IDENTITY_MISMATCH = "identity_mismatch"


class AuthorizationEnforcementLabel:
    """Closed enforcement posture labels."""

    AUDIT = "audit"
    ENFORCE = "enforce"


@dataclass(frozen=True)
class LifecycleMetricDefinition:
    """One metric's stable name, instrument type, and closed label registry."""

    name: str
    instrument: Literal["counter", "histogram", "observable_gauge"]
    labels: tuple[tuple[str, tuple[str, ...]], ...]


_PLANNERS: Final[frozenset[str]] = frozenset(
    {LifecyclePlannerLabel.RETENTION, LifecyclePlannerLabel.REPAIR}
)
_PLAN_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        LifecyclePlanOutcomeLabel.SUCCEEDED,
        LifecyclePlanOutcomeLabel.REJECTED_INPUT,
        LifecyclePlanOutcomeLabel.FAILED,
    }
)
_DISPOSITIONS: Final[frozenset[str]] = frozenset(
    {
        LifecycleDispositionLabel.RETAIN,
        LifecycleDispositionLabel.LOGICAL_TOMBSTONE_ONLY,
        LifecycleDispositionLabel.PHYSICALLY_ELIGIBLE,
        LifecycleDispositionLabel.CANDIDATE,
        LifecycleDispositionLabel.WITHHELD,
    }
)
_LAG_STAGES: Final[frozenset[str]] = frozenset(
    {RetentionLagStageLabel.TOMBSTONE_DUE, RetentionLagStageLabel.PHYSICAL_GC_DUE}
)
_BACKLOG_STATES: Final[frozenset[str]] = frozenset(
    {
        ReconcileBacklogStateLabel.CANDIDATE,
        ReconcileBacklogStateLabel.WITHHELD,
    }
)
_RETENTION_CANDIDATE_KINDS: Final[tuple[str, ...]] = (
    "artifact_blob",
    "artifact_metadata",
    "preimage",
    "prepared_temp",
    "audit_metadata",
    "usage_metadata",
    "other",
)
_REPAIR_CANDIDATE_KINDS: Final[tuple[str, ...]] = (
    "metadata_outbox",
    "orphan_artifact_or_temp",
    "stale_prepared_resource",
    "receipt_source_projection",
    "usage_edge",
    "audit_verification",
    "effect_reconciliation",
)
_CANDIDATE_KINDS: Final[frozenset[str]] = frozenset(
    (*_RETENTION_CANDIDATE_KINDS, *_REPAIR_CANDIDATE_KINDS)
)
_RETENTION_EXECUTION_KINDS: Final[frozenset[str]] = frozenset(
    {
        "context_payloads",
        "checkpoints",
        "messages",
        "events",
        "memory_items",
        "messages_tombstoned",
        "events_tombstoned",
        "memory_items_tombstoned",
        "artifacts_tombstoned",
        "sweep_cycle",
    }
)
_REPAIR_EXECUTION_ACTIONS: Final[frozenset[str]] = frozenset(
    {RepairExecutionActionLabel.EFFECT_RECONCILE}
)
_REPAIR_EXECUTION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        RepairExecutionOutcomeLabel.QUEUED,
        RepairExecutionOutcomeLabel.ALREADY_QUEUED,
        RepairExecutionOutcomeLabel.WITHHELD,
        RepairExecutionOutcomeLabel.UNSUPPORTED,
        RepairExecutionOutcomeLabel.FAILED,
    }
)
_ARTIFACT_CLEANUP_EXECUTION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        ArtifactCleanupExecutionOutcomeLabel.PURGED,
        ArtifactCleanupExecutionOutcomeLabel.QUARANTINED,
        ArtifactCleanupExecutionOutcomeLabel.REAPED,
        ArtifactCleanupExecutionOutcomeLabel.RESTORED,
        ArtifactCleanupExecutionOutcomeLabel.WITHHELD,
        ArtifactCleanupExecutionOutcomeLabel.ALREADY_CLEAN,
        ArtifactCleanupExecutionOutcomeLabel.FAILED,
        ArtifactCleanupExecutionOutcomeLabel.AUDIT_FAILED,
    }
)
_AUDIT_FORMATS: Final[frozenset[str]] = frozenset(
    {
        AuditVerificationFormatLabel.RECEIPT_V1,
        AuditVerificationFormatLabel.RECEIPT_V2,
    }
)
_VERIFICATION_OUTCOMES: Final[frozenset[str]] = frozenset(
    {VerificationOutcomeLabel.SUCCEEDED, VerificationOutcomeLabel.FAILED}
)
_AUTHORIZATION_BOUNDARIES: Final[frozenset[str]] = frozenset(
    {
        AuthorizationBoundaryLabel.RBAC,
        AuthorizationBoundaryLabel.AUDIT_LIST_IDENTITY,
    }
)
_AUTHORIZATION_REASONS: Final[frozenset[str]] = frozenset(
    {
        AuthorizationDenyReasonLabel.RBAC_DENIED,
        AuthorizationDenyReasonLabel.MFA_PENDING,
        AuthorizationDenyReasonLabel.IDENTITY_MISMATCH,
    }
)
_AUTHORIZATION_ENFORCEMENTS: Final[frozenset[str]] = frozenset(
    {AuthorizationEnforcementLabel.AUDIT, AuthorizationEnforcementLabel.ENFORCE}
)


LIFECYCLE_METRIC_REGISTRY: Final[tuple[LifecycleMetricDefinition, ...]] = (
    LifecycleMetricDefinition(
        name=LifecycleMetricName.PLAN_TOTAL,
        instrument="counter",
        labels=(
            ("planner", tuple(sorted((*_PLANNERS, _OTHER)))),
            ("outcome", tuple(sorted((*_PLAN_OUTCOMES, _OTHER)))),
        ),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.PLAN_DECISIONS_TOTAL,
        instrument="counter",
        labels=(
            ("planner", tuple(sorted((*_PLANNERS, _OTHER)))),
            ("candidate_kind", tuple(sorted((*_CANDIDATE_KINDS, _OTHER)))),
            ("disposition", tuple(sorted((*_DISPOSITIONS, _OTHER)))),
        ),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.PLAN_DURATION_SECONDS,
        instrument="histogram",
        labels=(("planner", tuple(sorted((*_PLANNERS, _OTHER)))),),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.RETENTION_LAG_SECONDS,
        instrument="histogram",
        labels=(
            ("candidate_kind", tuple(sorted((*_CANDIDATE_KINDS, _OTHER)))),
            ("stage", tuple(sorted((*_LAG_STAGES, _OTHER)))),
        ),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.RECONCILE_BACKLOG_SNAPSHOT_ITEMS,
        instrument="observable_gauge",
        labels=(("state", tuple(sorted((*_BACKLOG_STATES, _OTHER)))),),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.RETENTION_EXECUTION_FAILURES_TOTAL,
        instrument="counter",
        labels=(("kind", tuple(sorted((*_RETENTION_EXECUTION_KINDS, _OTHER)))),),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.REPAIR_EXECUTION_TOTAL,
        instrument="counter",
        labels=(
            ("action", tuple(sorted((*_REPAIR_EXECUTION_ACTIONS, _OTHER)))),
            ("outcome", tuple(sorted((*_REPAIR_EXECUTION_OUTCOMES, _OTHER)))),
        ),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.ARTIFACT_CLEANUP_EXECUTION_TOTAL,
        instrument="counter",
        labels=(
            (
                "outcome",
                tuple(sorted((*_ARTIFACT_CLEANUP_EXECUTION_OUTCOMES, _OTHER))),
            ),
        ),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.AUDIT_VERIFICATION_TOTAL,
        instrument="counter",
        labels=(
            ("format", tuple(sorted((*_AUDIT_FORMATS, _UNKNOWN)))),
            ("outcome", tuple(sorted((*_VERIFICATION_OUTCOMES, _OTHER)))),
        ),
    ),
    LifecycleMetricDefinition(
        name=LifecycleMetricName.AUTHORIZATION_DENIALS_TOTAL,
        instrument="counter",
        labels=(
            ("boundary", tuple(sorted((*_AUTHORIZATION_BOUNDARIES, _OTHER)))),
            ("reason", tuple(sorted((*_AUTHORIZATION_REASONS, _OTHER)))),
            (
                "enforcement",
                tuple(sorted((*_AUTHORIZATION_ENFORCEMENTS, _UNKNOWN))),
            ),
        ),
    ),
)


@dataclass(frozen=True)
class LifecyclePlanDecisionMetric:
    """A redacted, closed-vocabulary fact about one planner decision."""

    candidate_kind: str
    disposition: str


class LifecycleOperationalMetrics:
    """Best-effort OpenTelemetry façade for the D13 lifecycle contract.

    The class deliberately accepts string values at its public boundary so
    domain packages can remain independent of observability types.  Every
    string is immediately normalized through one of this module's closed
    vocabularies before it is exported.
    """

    _PLAN_DURATION_BUCKETS: Final[tuple[float, ...]] = (
        0.001,
        0.005,
        0.01,
        0.05,
        0.1,
        0.5,
        1.0,
        5.0,
    )
    _RETENTION_LAG_BUCKETS: Final[tuple[float, ...]] = (
        60.0,
        300.0,
        900.0,
        3600.0,
        21600.0,
        86400.0,
        604800.0,
    )

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._plan_total: Any | None = None
        self._plan_decisions_total: Any | None = None
        self._plan_duration_seconds: Any | None = None
        self._retention_lag_seconds: Any | None = None
        self._retention_execution_failures_total: Any | None = None
        self._repair_execution_total: Any | None = None
        self._artifact_cleanup_execution_total: Any | None = None
        self._audit_verification_total: Any | None = None
        self._authorization_denials_total: Any | None = None
        self._reconcile_backlog = {
            ReconcileBacklogStateLabel.CANDIDATE: 0,
            ReconcileBacklogStateLabel.WITHHELD: 0,
        }
        self._reconcile_backlog_gauge = self._observable_gauge(
            LifecycleMetricName.RECONCILE_BACKLOG_SNAPSHOT_ITEMS,
            self._observe_reconcile_backlog,
        )

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics as otel_metrics
        except ImportError:  # pragma: no cover - optional in minimal dev environments
            return None
        try:
            return otel_metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - telemetry must never affect work
            return None

    def _counter(self, name: str) -> Any:
        if self._meter is None:
            return None
        try:
            return self._meter.create_counter(name)
        except Exception:  # pragma: no cover - defensive OTel boundary
            return None

    def _histogram(self, name: str, *, buckets: tuple[float, ...]) -> Any:
        if self._meter is None:
            return None
        try:
            return self._meter.create_histogram(
                name,
                explicit_bucket_boundaries_advisory=list(buckets),
            )
        except TypeError:
            try:
                return self._meter.create_histogram(name)
            except Exception:  # pragma: no cover - defensive OTel boundary
                return None
        except Exception:  # pragma: no cover - defensive OTel boundary
            return None

    def _observable_gauge(self, name: str, callback: Any) -> Any:
        if self._meter is None:
            return None
        try:
            return self._meter.create_observable_gauge(name, callbacks=[callback])
        except Exception:  # pragma: no cover - older/minimal OTel implementations
            return None

    @staticmethod
    def _closed(value: object, allowed: frozenset[str], fallback: str) -> str:
        return value if isinstance(value, str) and value in allowed else fallback

    @staticmethod
    def _non_negative(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        return max(0.0, float(value))

    def record_plan_success(
        self,
        *,
        planner: str,
        decisions: Sequence[LifecyclePlanDecisionMetric],
        elapsed_seconds: float,
    ) -> None:
        """Publish a completed D10 or D12 plan without exporting identifiers."""

        safe_planner = self._closed(planner, _PLANNERS, _OTHER)
        self._add_plan_total(
            planner=safe_planner, outcome=LifecyclePlanOutcomeLabel.SUCCEEDED
        )
        self._record_plan_duration(
            planner=safe_planner, elapsed_seconds=elapsed_seconds
        )
        if self._plan_decisions_total is None:
            self._plan_decisions_total = self._counter(
                LifecycleMetricName.PLAN_DECISIONS_TOTAL
            )
        if self._plan_decisions_total is None:
            return
        try:
            for decision in decisions:
                self._plan_decisions_total.add(
                    1,
                    {
                        "planner": safe_planner,
                        "candidate_kind": self._closed(
                            decision.candidate_kind, _CANDIDATE_KINDS, _OTHER
                        ),
                        "disposition": self._closed(
                            decision.disposition, _DISPOSITIONS, _OTHER
                        ),
                    },
                )
        except Exception:
            logger.debug(
                "lifecycle_metrics.plan_decisions.record_failed", exc_info=True
            )

    def record_plan_failure(
        self,
        *,
        planner: str,
        outcome: str,
        elapsed_seconds: float,
    ) -> None:
        """Publish a failed/rejected plan with no exception or snapshot details."""

        safe_planner = self._closed(planner, _PLANNERS, _OTHER)
        safe_outcome = self._closed(outcome, _PLAN_OUTCOMES, _OTHER)
        self._add_plan_total(planner=safe_planner, outcome=safe_outcome)
        self._record_plan_duration(
            planner=safe_planner, elapsed_seconds=elapsed_seconds
        )

    def _add_plan_total(self, *, planner: str, outcome: str) -> None:
        if self._plan_total is None:
            self._plan_total = self._counter(LifecycleMetricName.PLAN_TOTAL)
        if self._plan_total is None:
            return
        try:
            self._plan_total.add(1, {"planner": planner, "outcome": outcome})
        except Exception:
            logger.debug("lifecycle_metrics.plan_total.record_failed", exc_info=True)

    def _record_plan_duration(self, *, planner: str, elapsed_seconds: float) -> None:
        if self._plan_duration_seconds is None:
            self._plan_duration_seconds = self._histogram(
                LifecycleMetricName.PLAN_DURATION_SECONDS,
                buckets=self._PLAN_DURATION_BUCKETS,
            )
        if self._plan_duration_seconds is None:
            return
        try:
            self._plan_duration_seconds.record(
                self._non_negative(elapsed_seconds), {"planner": planner}
            )
        except Exception:
            logger.debug("lifecycle_metrics.plan_duration.record_failed", exc_info=True)

    def record_retention_lag(
        self,
        *,
        candidate_kind: str,
        stage: str,
        elapsed_seconds: float,
    ) -> None:
        """Observe safe due-time lag from a D10 planning snapshot."""

        if self._retention_lag_seconds is None:
            self._retention_lag_seconds = self._histogram(
                LifecycleMetricName.RETENTION_LAG_SECONDS,
                buckets=self._RETENTION_LAG_BUCKETS,
            )
        if self._retention_lag_seconds is None:
            return
        try:
            self._retention_lag_seconds.record(
                self._non_negative(elapsed_seconds),
                {
                    "candidate_kind": self._closed(
                        candidate_kind, _CANDIDATE_KINDS, _OTHER
                    ),
                    "stage": self._closed(stage, _LAG_STAGES, _OTHER),
                },
            )
        except Exception:
            logger.debug("lifecycle_metrics.retention_lag.record_failed", exc_info=True)

    def record_reconcile_backlog_snapshot(
        self,
        *,
        candidate_count: int,
        withheld_count: int,
    ) -> None:
        """Set the latest in-process D12 effect-reconciliation snapshot.

        This is an asynchronous gauge rather than a cumulative counter: a
        second planner invocation replaces the previous snapshot.  A process
        restart resets it to zero until a planner invocation occurs, which is
        why the runbook explicitly treats it as a runner-dependent signal.
        """

        self._reconcile_backlog[ReconcileBacklogStateLabel.CANDIDATE] = max(
            0, int(candidate_count)
        )
        self._reconcile_backlog[ReconcileBacklogStateLabel.WITHHELD] = max(
            0, int(withheld_count)
        )

    def _observe_reconcile_backlog(self, _options: object) -> Sequence[Any]:
        try:
            from opentelemetry.metrics import Observation
        except ImportError:  # pragma: no cover - optional OTel dependency
            return ()
        return tuple(
            Observation(value, {"state": state})
            for state, value in self._reconcile_backlog.items()
        )

    def record_retention_execution_failure(self, *, kind: str) -> None:
        """Count a real existing retention-sweeper failure without org details."""

        if self._retention_execution_failures_total is None:
            self._retention_execution_failures_total = self._counter(
                LifecycleMetricName.RETENTION_EXECUTION_FAILURES_TOTAL
            )
        if self._retention_execution_failures_total is None:
            return
        try:
            self._retention_execution_failures_total.add(
                1,
                {"kind": self._closed(kind, _RETENTION_EXECUTION_KINDS, _OTHER)},
            )
        except Exception:
            logger.debug(
                "lifecycle_metrics.retention_failure.record_failed", exc_info=True
            )

    def record_repair_execution(self, *, action: str, outcome: str) -> None:
        """Count a D12 dispatch outcome without resource-identifying labels."""

        if self._repair_execution_total is None:
            self._repair_execution_total = self._counter(
                LifecycleMetricName.REPAIR_EXECUTION_TOTAL
            )
        if self._repair_execution_total is None:
            return
        try:
            self._repair_execution_total.add(
                1,
                {
                    "action": self._closed(action, _REPAIR_EXECUTION_ACTIONS, _OTHER),
                    "outcome": self._closed(
                        outcome, _REPAIR_EXECUTION_OUTCOMES, _OTHER
                    ),
                },
            )
        except Exception:
            logger.debug(
                "lifecycle_metrics.repair_execution.record_failed", exc_info=True
            )

    def record_artifact_cleanup_execution(self, *, outcome: str) -> None:
        """Count a redacted physical-cleanup result without tenant labels."""

        if self._artifact_cleanup_execution_total is None:
            self._artifact_cleanup_execution_total = self._counter(
                LifecycleMetricName.ARTIFACT_CLEANUP_EXECUTION_TOTAL
            )
        if self._artifact_cleanup_execution_total is None:
            return
        try:
            self._artifact_cleanup_execution_total.add(
                1,
                {
                    "outcome": self._closed(
                        outcome,
                        _ARTIFACT_CLEANUP_EXECUTION_OUTCOMES,
                        _OTHER,
                    )
                },
            )
        except Exception:
            logger.debug(
                "lifecycle_metrics.artifact_cleanup.record_failed",
                exc_info=True,
            )

    def record_audit_verification(self, *, format: str, succeeded: bool) -> None:
        """Count receipt verification attempts and failures with a closed format label."""

        if self._audit_verification_total is None:
            self._audit_verification_total = self._counter(
                LifecycleMetricName.AUDIT_VERIFICATION_TOTAL
            )
        if self._audit_verification_total is None:
            return
        try:
            self._audit_verification_total.add(
                1,
                {
                    "format": self._closed(format, _AUDIT_FORMATS, _UNKNOWN),
                    "outcome": (
                        VerificationOutcomeLabel.SUCCEEDED
                        if succeeded
                        else VerificationOutcomeLabel.FAILED
                    ),
                },
            )
        except Exception:
            logger.debug(
                "lifecycle_metrics.audit_verification.record_failed", exc_info=True
            )

    def record_authorization_denial(
        self,
        *,
        boundary: str,
        reason: str,
        enforcement: str,
    ) -> None:
        """Count a concrete authorization denial with no request identity/path label."""

        if self._authorization_denials_total is None:
            self._authorization_denials_total = self._counter(
                LifecycleMetricName.AUTHORIZATION_DENIALS_TOTAL
            )
        if self._authorization_denials_total is None:
            return
        try:
            self._authorization_denials_total.add(
                1,
                {
                    "boundary": self._closed(
                        boundary, _AUTHORIZATION_BOUNDARIES, _OTHER
                    ),
                    "reason": self._closed(reason, _AUTHORIZATION_REASONS, _OTHER),
                    "enforcement": self._closed(
                        enforcement, _AUTHORIZATION_ENFORCEMENTS, _UNKNOWN
                    ),
                },
            )
        except Exception:
            logger.debug(
                "lifecycle_metrics.authorization_denial.record_failed", exc_info=True
            )


_PROCESS_METRICS: LifecycleOperationalMetrics | None = None
_PROCESS_METRICS_LOCK = Lock()


def get_lifecycle_operational_metrics() -> LifecycleOperationalMetrics:
    """Return the process-scoped façade used by existing runtime seams."""

    global _PROCESS_METRICS
    if _PROCESS_METRICS is None:
        with _PROCESS_METRICS_LOCK:
            if _PROCESS_METRICS is None:
                _PROCESS_METRICS = LifecycleOperationalMetrics()
    return _PROCESS_METRICS


def reset_lifecycle_operational_metrics_for_tests() -> None:
    """Clear the lazy process façade for an isolated OTel-provider test."""

    global _PROCESS_METRICS
    with _PROCESS_METRICS_LOCK:
        _PROCESS_METRICS = None


__all__ = (
    "AuditVerificationFormatLabel",
    "ArtifactCleanupExecutionOutcomeLabel",
    "AuthorizationBoundaryLabel",
    "AuthorizationDenyReasonLabel",
    "AuthorizationEnforcementLabel",
    "LIFECYCLE_METRIC_REGISTRY",
    "LifecycleDispositionLabel",
    "LifecycleMetricDefinition",
    "LifecycleMetricName",
    "LifecycleOperationalMetrics",
    "LifecyclePlanDecisionMetric",
    "LifecyclePlanOutcomeLabel",
    "LifecyclePlannerLabel",
    "ReconcileBacklogStateLabel",
    "RepairExecutionActionLabel",
    "RepairExecutionOutcomeLabel",
    "RetentionLagStageLabel",
    "VerificationOutcomeLabel",
    "get_lifecycle_operational_metrics",
    "reset_lifecycle_operational_metrics_for_tests",
)
