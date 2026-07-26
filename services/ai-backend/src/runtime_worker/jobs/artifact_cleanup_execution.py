"""Opt-in, tenant-scoped executor for physical artifact reclamation.

This runner intentionally owns scheduling only.  It has no artifact body,
filesystem path, blob key, reference edge, or legal-hold mutation capability.
Every destructive decision stays inside ``RuntimeApiStore.execute_artifact_cleanup``
and the existing lifecycle/GC adapters, where the live legal-hold and reference
checks are serialized with the physical move/unlink.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import os
from typing import Callable, Literal, Protocol, runtime_checkable
from uuid import uuid4

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupDeferredTenant,
    ArtifactCleanupLease,
    ArtifactCleanupScheduleStore,
    ArtifactCleanupTenantExecutionLease,
    ArtifactCleanupTrackedExecution,
)

from agent_runtime.observability.lifecycle_metrics import (
    LifecycleOperationalMetrics,
    get_lifecycle_operational_metrics,
)
from runtime_adapters.artifact_lifecycle import (
    ArtifactCleanupExecutionFence,
    ArtifactCleanupExecutionFenceLostError,
    ArtifactPhysicalCleanupOutcome,
)


_LOGGER = logging.getLogger(__name__)


class ArtifactCleanupExecutionEnv:
    """Explicit, disabled-by-default execution configuration."""

    ENABLED = "ARTIFACT_CLEANUP_EXECUTION_ENABLED"
    INTERVAL_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_INTERVAL_SECONDS"
    MAX_ORGS = "ARTIFACT_CLEANUP_EXECUTION_MAX_ORGS"
    LIMIT_PER_ORG = "ARTIFACT_CLEANUP_EXECUTION_LIMIT_PER_ORG"
    LEASE_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_LEASE_SECONDS"
    RETRY_BASE_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_RETRY_BASE_SECONDS"
    RETRY_MAX_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_RETRY_MAX_SECONDS"
    TENANT_TIMEOUT_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_TENANT_TIMEOUT_SECONDS"
    CANCEL_GRACE_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_CANCEL_GRACE_SECONDS"
    STOP_GRACE_SECONDS = "ARTIFACT_CLEANUP_EXECUTION_STOP_GRACE_SECONDS"
    MAX_QUARANTINED_EXECUTIONS = "ARTIFACT_CLEANUP_EXECUTION_MAX_QUARANTINED_EXECUTIONS"

    DEFAULT_INTERVAL_SECONDS = 900.0
    DEFAULT_MAX_ORGS = 100
    DEFAULT_LIMIT_PER_ORG = 100
    DEFAULT_LEASE_SECONDS = 1_800.0
    DEFAULT_RETRY_BASE_SECONDS = 60.0
    DEFAULT_RETRY_MAX_SECONDS = 3_600.0
    DEFAULT_TENANT_TIMEOUT_SECONDS = 300.0
    DEFAULT_CANCEL_GRACE_SECONDS = 10.0
    DEFAULT_STOP_GRACE_SECONDS = 15.0
    DEFAULT_MAX_QUARANTINED_EXECUTIONS = 4

    @classmethod
    def enabled(cls) -> bool:
        return cls.env_bool(cls.ENABLED, default=False)

    @staticmethod
    def env_bool(name: str, *, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def env_float(name: str, *, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            parsed = float(raw)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def env_int(name: str, *, default: int, maximum: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            parsed = int(raw)
        except ValueError:
            return default
        return parsed if 1 <= parsed <= maximum else default


@runtime_checkable
class ArtifactCleanupExecutionPort(Protocol):
    """Trusted persistence boundary for one tenant's physical lifecycle pass."""

    async def list_retention_orgs(self) -> Sequence[str]: ...

    async def execute_artifact_cleanup(
        self,
        *,
        org_id: str,
        now: datetime,
        limit: int,
        execution_fence: ArtifactCleanupExecutionFence | None = None,
    ) -> ArtifactPhysicalCleanupOutcome: ...

    async def write_audit_log(
        self,
        *,
        event_type: str,
        record: dict[str, object],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ArtifactCleanupExecutionResult:
    """Aggregate-only outcome for one bounded execution cycle."""

    tenants_scanned: int = 0
    purged_artifacts: int = 0
    quarantined_blobs: int = 0
    reaped_blobs: int = 0
    restored_blobs: int = 0
    withheld_blobs: int = 0
    already_clean_tenants: int = 0
    failures: int = 0
    deferred_tenants: int = 0
    hung_tenants: int = 0
    quarantine_capacity_reached: bool = False
    audit_failures: int = 0


@dataclass(slots=True)
class _LifecycleTask:
    """One explicitly tracked destructive tenant pass.

    The execution lease is intentionally held outside this task.  A task that
    does not honour cancellation remains in this registry and keeps its
    adapter-backed tenant fence until the task has actually finished.
    """

    execution: ArtifactCleanupTenantExecutionLease
    task: asyncio.Task[ArtifactPhysicalCleanupOutcome]
    current_time: Callable[[], datetime]
    quarantined: bool = False
    release_pending: bool = False


@dataclass(frozen=True, slots=True)
class _LifecycleAttempt:
    """A settled or quarantined lifecycle task observation."""

    state: Literal[
        "completed",
        "failed",
        "fence_lost",
        "cancelled",
        "timed_out",
        "quarantined",
    ]
    outcome: ArtifactPhysicalCleanupOutcome | None = None
    release_execution: bool = True


class ArtifactCleanupExecutionRunner:
    """Run bounded tenant cleanup with safe audit and metric evidence.

    Redelivery is safe because the lifecycle state machine is its own durable
    idempotency key: candidates/quarantine are atomically advanced, references
    restore rather than delete, and an interrupted reaping lane is recovered on
    the next pass.  This runner never advances a cursor before that state has
    been durably reconciled.
    """

    _AUDIT_EVENT_TYPE = "artifact_cleanup.executed"
    _CAPACITY_AUDIT_EVENT_TYPE = "artifact_cleanup.quarantine_capacity_reached"
    _RELEASE_PENDING_AUDIT_EVENT_TYPE = "artifact_cleanup.execution_release_pending"
    _RELEASE_RESOLVED_AUDIT_EVENT_TYPE = "artifact_cleanup.execution_release_resolved"

    def __init__(
        self,
        *,
        persistence: ArtifactCleanupExecutionPort,
        schedule: ArtifactCleanupScheduleStore,
        max_orgs: int = ArtifactCleanupExecutionEnv.DEFAULT_MAX_ORGS,
        limit_per_org: int = ArtifactCleanupExecutionEnv.DEFAULT_LIMIT_PER_ORG,
        lease_seconds: float = ArtifactCleanupExecutionEnv.DEFAULT_LEASE_SECONDS,
        retry_base_seconds: float = ArtifactCleanupExecutionEnv.DEFAULT_RETRY_BASE_SECONDS,
        retry_max_seconds: float = ArtifactCleanupExecutionEnv.DEFAULT_RETRY_MAX_SECONDS,
        tenant_timeout_seconds: float = ArtifactCleanupExecutionEnv.DEFAULT_TENANT_TIMEOUT_SECONDS,
        cancel_grace_seconds: float = ArtifactCleanupExecutionEnv.DEFAULT_CANCEL_GRACE_SECONDS,
        stop_grace_seconds: float = ArtifactCleanupExecutionEnv.DEFAULT_STOP_GRACE_SECONDS,
        max_quarantined_executions: int = (
            ArtifactCleanupExecutionEnv.DEFAULT_MAX_QUARANTINED_EXECUTIONS
        ),
        metrics: LifecycleOperationalMetrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not 1 <= max_orgs <= 500
            or not 1 <= limit_per_org <= 500
            or lease_seconds <= 0
            or retry_base_seconds <= 0
            or retry_max_seconds < retry_base_seconds
            or tenant_timeout_seconds <= 0
            or cancel_grace_seconds <= 0
            or stop_grace_seconds < cancel_grace_seconds
            or not 1 <= max_quarantined_executions <= 64
        ):
            raise ValueError("artifact cleanup execution bounds are invalid")
        self._persistence = persistence
        self._schedule = schedule
        self._max_orgs = max_orgs
        self._limit_per_org = limit_per_org
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._tenant_timeout_seconds = tenant_timeout_seconds
        self._cancel_grace_seconds = cancel_grace_seconds
        self._stop_grace_seconds = stop_grace_seconds
        self._max_quarantined_executions = max_quarantined_executions
        self._owner_id = f"artifact-cleanup-{uuid4().hex}"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._metrics = (
            metrics if metrics is not None else get_lifecycle_operational_metrics()
        )
        self._stop_requested = asyncio.Event()
        self._lifecycle_tasks: dict[str, _LifecycleTask] = {}
        self._quarantine_capacity_reported = False

    @property
    def stop_grace_seconds(self) -> float:
        """Upper bound used by the periodic loop's graceful stop."""

        return self._stop_grace_seconds

    def request_stop(self) -> None:
        """Ask a current tenant pass to take the bounded cancellation path."""

        self._stop_requested.set()

    def resume_scheduling(self) -> None:
        """Prepare a stopped loop for another cycle without dropping fences.

        Quarantined tasks deliberately remain in the registry. A restarted
        loop still respects their adapter-backed tenant fences and capacity
        limit instead of treating restart as permission to overlap cleanup.
        """

        self._stop_requested.clear()

    async def _tracked_executions(
        self,
    ) -> tuple[ArtifactCleanupTrackedExecution, ...] | None:
        """Read the globally durable admission state; unavailable means stop."""

        try:
            return await self._schedule.list_tracked_tenant_executions()
        except Exception:
            self._record_metric(outcome="failed")
            _LOGGER.warning("artifact_cleanup_execution_admission_unavailable")
            return None

    async def _global_capacity_reached(self) -> tuple[bool, int, str | None]:
        tracked = await self._tracked_executions()
        if tracked is None:
            # Admission state must fail closed; the caller reports a regular
            # failure rather than trusting a stale in-memory count.
            return True, self._max_quarantined_executions, None
        count = len(tracked)
        if count < self._max_quarantined_executions:
            self._quarantine_capacity_reported = False
        return (
            count >= self._max_quarantined_executions,
            count,
            tracked[0].execution.org_id if tracked else None,
        )

    async def _fail_closed_for_quarantine_capacity(
        self, *, count: int, representative_org_id: str | None
    ) -> ArtifactCleanupExecutionResult:
        """Emit bounded evidence and admit no new destructive lifecycle pass."""

        audit_failed = not await self._emit_quarantine_capacity_evidence(
            count=count, representative_org_id=representative_org_id
        )
        if audit_failed:
            self._record_metric(outcome="audit_failed")
        return ArtifactCleanupExecutionResult(
            failures=1,
            hung_tenants=count,
            quarantine_capacity_reached=True,
            audit_failures=int(audit_failed),
        )

    async def _emit_quarantine_capacity_evidence(
        self, *, count: int, representative_org_id: str | None
    ) -> bool:
        """Persist a tenant-scoped health record once per saturated interval."""

        if count < self._max_quarantined_executions:
            self._quarantine_capacity_reported = False
            return True
        if self._quarantine_capacity_reported:
            return True
        self._record_metric(outcome="quarantine_capacity_reached")
        try:
            await self._persistence.write_audit_log(
                event_type=self._CAPACITY_AUDIT_EVENT_TYPE,
                record={
                    "org_id": representative_org_id or "artifact_cleanup_worker",
                    "actor_type": "system",
                    "action": self._CAPACITY_AUDIT_EVENT_TYPE,
                    "resource_type": "artifact_cleanup_worker",
                    "resource_id": "artifact_cleanup_worker",
                    "outcome": "failure",
                    "metadata": {
                        "health": "quarantine_capacity_reached",
                        "quarantined_execution_count": count,
                        "max_quarantined_executions": self._max_quarantined_executions,
                    },
                },
            )
        except Exception:
            _LOGGER.warning("artifact_cleanup_execution_capacity_audit_unavailable")
            return False
        self._quarantine_capacity_reported = True
        _LOGGER.warning("artifact_cleanup_execution_quarantine_capacity_reached")
        return True

    async def _retry_due_release_pending(self, *, now: datetime) -> None:
        """Retry only locally-held, durably due release-pending fences.

        An adapter handle never crosses a process boundary.  Orphaned records
        are first reconciled by the store; this worker then retries only exact
        handles it still owns.  The bounded slice prevents a release outage
        from monopolising a scheduler cycle.
        """

        for tracked in tuple(self._lifecycle_tasks.values())[
            : self._max_quarantined_executions
        ]:
            if not tracked.release_pending:
                continue
            due = await self._schedule.load_tenant_execution_release_pending(
                execution=tracked.execution, now=now
            )
            if due is None:
                continue
            await self._release_execution_or_mark_pending(
                tracked=tracked, now=now, from_retry=True
            )

    async def _release_execution_or_mark_pending(
        self,
        *,
        tracked: _LifecycleTask,
        now: datetime,
        from_retry: bool = False,
    ) -> bool:
        """Release a stopped exact fence or retain it in durable retry state."""

        released = False
        try:
            released = await self._schedule.release_tenant_execution(
                execution=tracked.execution
            )
        except Exception:
            _LOGGER.warning("artifact_cleanup_execution_fence_release_failed")
        if released:
            was_pending = tracked.release_pending
            self._lifecycle_tasks.pop(tracked.execution.execution_token, None)
            if was_pending:
                await self._write_execution_health(
                    event_type=self._RELEASE_RESOLVED_AUDIT_EVENT_TYPE,
                    execution=tracked.execution,
                    outcome="resolved",
                    release_failure_count=0,
                )
            return True

        tracked.release_pending = True
        try:
            pending = await self._schedule.mark_tenant_execution_release_pending(
                execution=tracked.execution,
                now=now,
                retry_base_seconds=self._retry_base_seconds,
                retry_max_seconds=self._retry_max_seconds,
            )
        except Exception:
            pending = None
        if pending is None:
            # The original admission record still exists (or the store is
            # unavailable). Keep the in-process handle and consume capacity;
            # attempting a new lifecycle pass would be unsafe.
            self._record_metric(outcome="failed")
            _LOGGER.warning("artifact_cleanup_execution_release_pending_unavailable")
            return False
        self._record_metric(outcome="release_pending")
        await self._write_execution_health(
            event_type=self._RELEASE_PENDING_AUDIT_EVENT_TYPE,
            execution=tracked.execution,
            outcome="pending" if not from_retry else "retry_failed",
            release_failure_count=pending.release_failure_count,
            retry_not_before=pending.retry_not_before,
        )
        return False

    async def _write_execution_health(
        self,
        *,
        event_type: str,
        execution: ArtifactCleanupTenantExecutionLease,
        outcome: str,
        release_failure_count: int,
        retry_not_before: datetime | None = None,
    ) -> None:
        """Best-effort, body-free health evidence for fence release state."""

        try:
            await self._persistence.write_audit_log(
                event_type=event_type,
                record={
                    "org_id": execution.org_id,
                    "actor_type": "system",
                    "action": event_type,
                    "resource_type": "artifact_cleanup_execution_fence",
                    "resource_id": execution.execution_token,
                    "outcome": outcome,
                    "metadata": {
                        "release_failure_count": release_failure_count,
                        **(
                            {"retry_not_before": retry_not_before.isoformat()}
                            if retry_not_before is not None
                            else {}
                        ),
                    },
                },
            )
        except Exception:
            self._record_metric(outcome="audit_failed")
            _LOGGER.warning("artifact_cleanup_execution_release_audit_unavailable")

    async def run_once(
        self, *, now: datetime | None = None
    ) -> ArtifactCleanupExecutionResult:
        """Run one page without logging tenant, artifact, or content details."""

        fixed_now = _utc(now) if now is not None else None

        def current_time() -> datetime:
            return fixed_now if fixed_now is not None else _utc(self._clock())

        try:
            await self._schedule.reconcile_orphaned_tenant_executions()
            await self._retry_due_release_pending(now=current_time())
        except Exception:
            self._record_metric(outcome="failed")
            _LOGGER.warning("artifact_cleanup_execution_admission_reconcile_failed")
            return ArtifactCleanupExecutionResult(failures=1)
        (
            at_capacity,
            count,
            representative_org_id,
        ) = await self._global_capacity_reached()
        if at_capacity:
            return await self._fail_closed_for_quarantine_capacity(
                count=count, representative_org_id=representative_org_id
            )

        reference_now = current_time()
        try:
            lease = await self._schedule.acquire_lease(
                owner_id=self._owner_id,
                now=reference_now,
                duration_seconds=self._lease_seconds,
            )
        except Exception:
            self._record_metric(outcome="failed")
            _LOGGER.warning("artifact_cleanup_execution_schedule_unavailable")
            return ArtifactCleanupExecutionResult(failures=1)
        if lease is None:
            return ArtifactCleanupExecutionResult()

        keeper = _ArtifactCleanupLeaseKeeper(
            schedule=self._schedule,
            lease=lease,
            lease_seconds=self._lease_seconds,
            current_time=current_time,
            heartbeat_enabled=fixed_now is None,
        )
        await keeper.start()
        try:
            return await self._run_leased(
                reference_now=reference_now,
                current_time=current_time,
                keeper=keeper,
            )
        finally:
            await keeper.stop()
            try:
                await self._schedule.release_lease(
                    owner_id=self._owner_id,
                    fence_token=lease.fence_token,
                    now=current_time(),
                )
            except Exception:
                _LOGGER.warning("artifact_cleanup_execution_schedule_release_failed")

    async def _run_leased(
        self,
        *,
        reference_now: datetime,
        current_time: Callable[[], datetime],
        keeper: "_ArtifactCleanupLeaseKeeper",
    ) -> ArtifactCleanupExecutionResult:
        if self._stop_requested.is_set():
            return ArtifactCleanupExecutionResult()
        try:
            org_ids = tuple(sorted(set(await self._persistence.list_retention_orgs())))
            cursor = await self._schedule.load_cursor()
        except Exception:
            self._record_metric(outcome="failed")
            _LOGGER.warning("artifact_cleanup_execution_source_unavailable")
            return ArtifactCleanupExecutionResult(failures=1)

        result = ArtifactCleanupExecutionResult()
        for org_id in _fair_org_page(
            org_ids=org_ids,
            cursor_after_org_id=cursor,
            limit=len(org_ids),
        ):
            if self._stop_requested.is_set():
                return result
            if result.tenants_scanned >= self._max_orgs:
                break
            if not await keeper.renew_now():
                self._record_metric(outcome="failed")
                _LOGGER.warning("artifact_cleanup_execution_lease_lost")
                return _replace(result, failures=result.failures + 1)
            attempt_now = current_time()
            try:
                deferred = await self._schedule.load_deferred_tenant(
                    owner_id=self._owner_id,
                    fence_token=keeper.fence_token,
                    org_id=org_id,
                    now=attempt_now,
                )
            except Exception:
                self._record_metric(outcome="failed")
                _LOGGER.warning("artifact_cleanup_execution_lease_lost")
                return _replace(result, failures=result.failures + 1)
            if deferred is not None:
                continue
            try:
                execution = await self._schedule.acquire_tenant_execution(
                    owner_id=self._owner_id,
                    fence_token=keeper.fence_token,
                    org_id=org_id,
                    now=current_time(),
                    maximum_active_executions=self._max_quarantined_executions,
                )
            except Exception:
                self._record_metric(outcome="failed")
                _LOGGER.warning("artifact_cleanup_execution_fence_unavailable")
                return _replace(result, failures=result.failures + 1)
            if execution is None:
                (
                    at_capacity,
                    count,
                    representative_org_id,
                ) = await self._global_capacity_reached()
                if at_capacity:
                    capacity = await self._fail_closed_for_quarantine_capacity(
                        count=count, representative_org_id=representative_org_id
                    )
                    return _replace(
                        result,
                        failures=result.failures + capacity.failures,
                        hung_tenants=max(result.hung_tenants, capacity.hung_tenants),
                        quarantine_capacity_reached=True,
                        audit_failures=result.audit_failures + capacity.audit_failures,
                    )
                # A paused predecessor still owns this tenant's durable
                # execution fence. Leave its cursor position untouched and
                # continue the fair page; starting a second destructive pass
                # would be less safe than a later retry.
                _LOGGER.warning("artifact_cleanup_execution_tenant_busy")
                continue
            attempt: _LifecycleAttempt | None = None
            try:
                attempt = await self._run_lifecycle_task(
                    org_id=org_id,
                    reference_now=reference_now,
                    execution=execution,
                    current_time=current_time,
                )
                if attempt.state == "fence_lost":
                    self._record_metric(outcome="failed")
                    _LOGGER.warning("artifact_cleanup_execution_fence_lost")
                    return _replace(result, failures=result.failures + 1)
                if attempt.state == "completed":
                    outcome = attempt.outcome
                    if outcome is None:  # pragma: no cover - defensive invariant
                        raise RuntimeError("completed artifact cleanup lacks outcome")
                    # A long-running lifecycle call may span a heartbeat failure
                    # or takeover. The lifecycle guard already fenced every
                    # destructive phase; recheck the global generation before
                    # recording outcome evidence or moving the fair cursor.
                    if not await keeper.renew_now():
                        self._record_metric(outcome="failed")
                        _LOGGER.warning("artifact_cleanup_execution_lease_lost")
                        return _replace(result, failures=result.failures + 1)
                    self._record_outcome_metrics(outcome)
                    audit_failed = not await self._write_audit(outcome=outcome)
                    if audit_failed:
                        self._record_metric(outcome="audit_failed")
                    result = _replace(
                        result,
                        tenants_scanned=result.tenants_scanned + 1,
                        purged_artifacts=result.purged_artifacts
                        + outcome.purged_artifacts,
                        quarantined_blobs=result.quarantined_blobs
                        + outcome.quarantined_blobs,
                        reaped_blobs=result.reaped_blobs + outcome.reaped_blobs,
                        restored_blobs=result.restored_blobs + outcome.restored_blobs,
                        withheld_blobs=result.withheld_blobs + outcome.withheld_blobs,
                        already_clean_tenants=result.already_clean_tenants
                        + int(_is_already_clean(outcome)),
                        audit_failures=result.audit_failures + int(audit_failed),
                    )
                    try:
                        advanced = await self._schedule.complete_tenant(
                            owner_id=self._owner_id,
                            fence_token=keeper.fence_token,
                            expected_cursor=cursor,
                            org_id=org_id,
                            now=current_time(),
                        )
                    except Exception:
                        self._record_metric(outcome="failed")
                        _LOGGER.warning("artifact_cleanup_execution_cursor_unavailable")
                        return _replace(result, failures=result.failures + 1)
                    if not advanced:
                        # A lost lease or concurrent successor means the physical
                        # lifecycle may be retried, which is safe; do not
                        # speculate about later cursor positions in this cycle.
                        _LOGGER.warning(
                            "artifact_cleanup_execution_cursor_not_advanced"
                        )
                        return _replace(result, failures=result.failures + 1)
                    cursor = org_id
                    continue

                # A lifecycle task that misses its deadline has either stopped
                # during cancellation grace or remains quarantined with its
                # tenant execution fence.  Both cases are intentionally retried
                # later rather than recording a potentially stale success.
                self._record_metric(outcome="failed")
                # Exception text can include a backend path or body-derived
                # value.  Keep the worker log deliberately body-free.
                _LOGGER.warning(
                    "artifact_cleanup_execution_tenant_%s",
                    "quarantined" if attempt.state == "quarantined" else "failed",
                )
                try:
                    deferred = await self._schedule.defer_failed_tenant(
                        owner_id=self._owner_id,
                        fence_token=keeper.fence_token,
                        expected_cursor=cursor,
                        org_id=org_id,
                        now=current_time(),
                        retry_base_seconds=self._retry_base_seconds,
                        retry_max_seconds=self._retry_max_seconds,
                    )
                except Exception:
                    deferred = None
                if deferred is None:
                    self._record_metric(outcome="failed")
                    _LOGGER.warning("artifact_cleanup_execution_failure_not_deferred")
                    return _replace(result, failures=result.failures + 1)
                audit_failed = not await self._write_audit(
                    outcome=ArtifactPhysicalCleanupOutcome(org_id=org_id),
                    deferred=deferred,
                    execution_state=_execution_state_for_attempt(attempt),
                )
                if audit_failed:
                    self._record_metric(outcome="audit_failed")
                result = _replace(
                    result,
                    tenants_scanned=result.tenants_scanned + 1,
                    failures=result.failures + 1,
                    deferred_tenants=result.deferred_tenants + 1,
                    hung_tenants=result.hung_tenants
                    + int(attempt.state == "quarantined"),
                    audit_failures=result.audit_failures + int(audit_failed),
                )
                # The failed tenant is durably visible with a bounded retry;
                # its cursor transition is atomic with that defer state, so
                # later tenants can continue without silently losing it.
                cursor = org_id
            finally:
                if attempt is None or attempt.release_execution:
                    tracked = self._lifecycle_tasks.get(execution.execution_token)
                    if tracked is None:
                        # A failure before task construction has no running
                        # lifecycle to retain, but still needs exact release.
                        tracked = _LifecycleTask(
                            execution=execution,
                            task=asyncio.create_task(
                                _completed_cleanup_task(),
                                name="artifact-cleanup-empty-release",
                            ),
                            current_time=current_time,
                        )
                        self._lifecycle_tasks[execution.execution_token] = tracked
                    await self._release_execution_or_mark_pending(
                        tracked=tracked, now=current_time()
                    )
            if attempt is not None and attempt.state == "quarantined":
                (
                    at_capacity,
                    count,
                    representative_org_id,
                ) = await self._global_capacity_reached()
                if at_capacity:
                    capacity_audit_failed = (
                        not await self._emit_quarantine_capacity_evidence(
                            count=count, representative_org_id=representative_org_id
                        )
                    )
                    if capacity_audit_failed:
                        self._record_metric(outcome="audit_failed")
                    return _replace(
                        result,
                        quarantine_capacity_reached=True,
                        audit_failures=result.audit_failures
                        + int(capacity_audit_failed),
                    )
            if self._stop_requested.is_set():
                return result
        return result

    async def _run_lifecycle_task(
        self,
        *,
        org_id: str,
        reference_now: datetime,
        execution: ArtifactCleanupTenantExecutionLease,
        current_time: Callable[[], datetime],
    ) -> _LifecycleAttempt:
        """Run one destructive pass under a bounded, cancellation-safe watch.

        A timeout is deliberately not implemented with ``wait_for`` around the
        lifecycle coroutine.  ``wait_for`` returns after it sends cancellation,
        which would let a non-cooperative operation outlive the caller while a
        naive ``finally`` releases its tenant fence.  We instead retain the
        explicit task and only release that fence once the task is done.
        """

        task = asyncio.create_task(
            self._persistence.execute_artifact_cleanup(
                org_id=org_id,
                now=reference_now,
                limit=self._limit_per_org,
                execution_fence=_ArtifactCleanupLifecycleFence(
                    schedule=self._schedule,
                    execution=execution,
                    current_time=current_time,
                ),
            ),
            name=f"artifact-cleanup-lifecycle-{execution.execution_token}",
        )
        tracked = _LifecycleTask(
            execution=execution, task=task, current_time=current_time
        )
        self._lifecycle_tasks[execution.execution_token] = tracked
        stop_waiter = asyncio.create_task(
            self._stop_requested.wait(), name="artifact-cleanup-stop-waiter"
        )
        try:
            done, _pending = await asyncio.wait(
                (task, stop_waiter),
                timeout=self._tenant_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return self._consume_lifecycle_task(tracked)
            reason = "shutdown" if stop_waiter in done else "deadline"
            return await self._cancel_or_quarantine_lifecycle_task(
                tracked=tracked, reason=reason, current_time=current_time
            )
        except asyncio.CancelledError:
            # A caller may be cancelled during process teardown.  Convert that
            # into the same bounded cancellation path before returning control;
            # it is never safe for the surrounding finally to drop this fence
            # while the lifecycle task may still be executing.
            return await self._cancel_or_quarantine_lifecycle_task(
                tracked=tracked, reason="shutdown", current_time=current_time
            )
        finally:
            if not stop_waiter.done():
                stop_waiter.cancel()
            try:
                await stop_waiter
            except asyncio.CancelledError:
                pass

    def _consume_lifecycle_task(self, tracked: _LifecycleTask) -> _LifecycleAttempt:
        """Consume a conclusively stopped task; its fence may now be released."""

        try:
            return _LifecycleAttempt(state="completed", outcome=tracked.task.result())
        except ArtifactCleanupExecutionFenceLostError:
            return _LifecycleAttempt(state="fence_lost")
        except asyncio.CancelledError:
            return _LifecycleAttempt(state="cancelled")
        except Exception:
            return _LifecycleAttempt(state="failed")

    async def _cancel_or_quarantine_lifecycle_task(
        self,
        *,
        tracked: _LifecycleTask,
        reason: Literal["deadline", "shutdown"],
        current_time: Callable[[], datetime],
    ) -> _LifecycleAttempt:
        """Cancel a pass, then quarantine it if it survives the grace bound."""

        task = tracked.task
        if not task.done():
            task.cancel()
        done, _pending = await asyncio.wait((task,), timeout=self._cancel_grace_seconds)
        if task in done:
            # A deadline/cancellation is an auditable failed attempt even when
            # the task happened to return normally during its cancellation
            # grace. Its effects are lifecycle-idempotent and it will be
            # reconciled on the later durable retry.
            self._discard_lifecycle_task_result(task)
            return _LifecycleAttempt(
                state="timed_out" if reason == "deadline" else "cancelled"
            )
        tracked.quarantined = True
        try:
            marked = await self._schedule.mark_tenant_execution_quarantined(
                execution=tracked.execution, now=current_time()
            )
        except Exception:
            marked = False
        if not marked:
            # The admission row was created before lifecycle work began; if a
            # state transition cannot be persisted it remains "active" and
            # still consumes global capacity.  Retain the fence locally and
            # never admit new work on the strength of an in-memory count.
            self._record_metric(outcome="failed")
            _LOGGER.warning("artifact_cleanup_execution_quarantine_unavailable")
        task.add_done_callback(self._on_quarantined_lifecycle_task_done)
        return _LifecycleAttempt(state="quarantined", release_execution=False)

    @staticmethod
    def _discard_lifecycle_task_result(
        task: asyncio.Task[ArtifactPhysicalCleanupOutcome],
    ) -> None:
        """Consume a settled cancellation-race result without exposing details."""

        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            return

    def _on_quarantined_lifecycle_task_done(
        self, task: asyncio.Task[ArtifactPhysicalCleanupOutcome]
    ) -> None:
        """Release only the exact fence after a quarantined task has stopped."""

        for tracked in tuple(self._lifecycle_tasks.values()):
            if tracked.task is task and tracked.quarantined:
                asyncio.create_task(
                    self._release_quarantined_lifecycle_task(tracked),
                    name=(
                        "artifact-cleanup-quarantine-release-"
                        f"{tracked.execution.execution_token}"
                    ),
                )
                return

    async def _release_quarantined_lifecycle_task(
        self, tracked: _LifecycleTask
    ) -> None:
        """Reap a stopped quarantined task without touching scheduler progress."""

        try:
            tracked.task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            # The earlier timeout is already durably deferred/audited. Never
            # log arbitrary adapter exception text from a late task.
            _LOGGER.warning("artifact_cleanup_execution_quarantined_task_failed")
        finally:
            await self._release_execution_or_mark_pending(
                tracked=tracked, now=tracked.current_time()
            )

    async def _write_audit(
        self,
        *,
        outcome: ArtifactPhysicalCleanupOutcome,
        deferred: ArtifactCleanupDeferredTenant | None = None,
        execution_state: str | None = None,
    ) -> bool:
        """Write aggregate audit evidence only; audit trouble never retries bytes."""

        audit_outcome = (
            "deferred"
            if deferred is not None
            else (
                "withheld"
                if outcome.withheld_blobs
                else (
                    "completed" if not _is_already_clean(outcome) else "already_clean"
                )
            )
        )
        try:
            await self._persistence.write_audit_log(
                event_type=self._AUDIT_EVENT_TYPE,
                record={
                    "org_id": outcome.org_id,
                    "actor_type": "system",
                    "action": self._AUDIT_EVENT_TYPE,
                    "resource_type": "artifact_cleanup",
                    "resource_id": "artifact_cleanup",
                    "outcome": audit_outcome,
                    "metadata": {
                        "purged_artifacts": outcome.purged_artifacts,
                        "quarantined_blobs": outcome.quarantined_blobs,
                        "reaped_blobs": outcome.reaped_blobs,
                        "restored_blobs": outcome.restored_blobs,
                        "withheld_blobs": outcome.withheld_blobs,
                        **(
                            {
                                "retry_count": deferred.failure_count,
                                "retry_not_before": deferred.retry_not_before.isoformat(),
                                **(
                                    {"execution_state": execution_state}
                                    if execution_state is not None
                                    else {}
                                ),
                            }
                            if deferred is not None
                            else {}
                        ),
                    },
                },
            )
            return True
        except Exception:
            _LOGGER.warning("artifact_cleanup_execution_audit_unavailable")
            return False

    def _record_outcome_metrics(self, outcome: ArtifactPhysicalCleanupOutcome) -> None:
        for label, count in (
            ("purged", outcome.purged_artifacts),
            ("quarantined", outcome.quarantined_blobs),
            ("reaped", outcome.reaped_blobs),
            ("restored", outcome.restored_blobs),
            ("withheld", outcome.withheld_blobs),
        ):
            for _ in range(count):
                self._record_metric(outcome=label)
        if _is_already_clean(outcome):
            self._record_metric(outcome="already_clean")

    def _record_metric(self, *, outcome: str) -> None:
        try:
            self._metrics.record_artifact_cleanup_execution(outcome=outcome)
        except Exception:
            return


class _ArtifactCleanupLifecycleFence:
    """Bridge the scheduler's durable tenant lock into lifecycle phases."""

    def __init__(
        self,
        *,
        schedule: ArtifactCleanupScheduleStore,
        execution: ArtifactCleanupTenantExecutionLease,
        current_time: Callable[[], datetime],
    ) -> None:
        self._schedule = schedule
        self._execution = execution
        self._current_time = current_time

    async def assert_active(self) -> None:
        try:
            active = await self._schedule.validate_tenant_execution(
                execution=self._execution,
                now=self._current_time(),
            )
        except Exception as exc:
            raise ArtifactCleanupExecutionFenceLostError() from exc
        if not active:
            raise ArtifactCleanupExecutionFenceLostError()


class _ArtifactCleanupLeaseKeeper:
    """Renew one fenced lease through long-running cleanup awaits.

    Each foreground tenant attempt also renews synchronously.  The heartbeat
    keeps the same fence generation alive while an authoritative lifecycle
    adapter is awaiting IO, preventing a second worker from taking the cycle
    lease merely because one bounded tenant cleanup is slow.
    """

    def __init__(
        self,
        *,
        schedule: ArtifactCleanupScheduleStore,
        lease: ArtifactCleanupLease,
        lease_seconds: float,
        current_time: Callable[[], datetime],
        heartbeat_enabled: bool,
    ) -> None:
        self._schedule = schedule
        self._owner_id = lease.owner_id
        self._fence_token = lease.fence_token
        self._lease_seconds = lease_seconds
        self._current_time = current_time
        self._heartbeat_enabled = heartbeat_enabled
        self._lost = False
        self._task: asyncio.Task[None] | None = None

    @property
    def fence_token(self) -> int:
        return self._fence_token

    async def start(self) -> None:
        if not self._heartbeat_enabled:
            return
        self._task = asyncio.create_task(
            self._heartbeat(), name="artifact-cleanup-lease-heartbeat"
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def renew_now(self) -> bool:
        if self._lost:
            return False
        try:
            renewed = await self._schedule.renew_lease(
                owner_id=self._owner_id,
                fence_token=self._fence_token,
                now=self._current_time(),
                duration_seconds=self._lease_seconds,
            )
        except Exception:
            self._lost = True
            return False
        if renewed is None or renewed.fence_token != self._fence_token:
            self._lost = True
            return False
        return True

    async def _heartbeat(self) -> None:
        interval = max(min(self._lease_seconds / 3, 60.0), 0.05)
        while True:
            await asyncio.sleep(interval)
            if not await self.renew_now():
                return


class ArtifactCleanupExecutionLoop:
    """Periodic wrapper whose scheduling is explicitly opt-in at startup."""

    def __init__(
        self,
        *,
        runner: ArtifactCleanupExecutionRunner,
        interval_seconds: float | None = None,
    ) -> None:
        self._runner = runner
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else ArtifactCleanupExecutionEnv.env_float(
                ArtifactCleanupExecutionEnv.INTERVAL_SECONDS,
                default=ArtifactCleanupExecutionEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            # ``stop`` intentionally asks the runner to cancel a live pass.
            # A new loop generation must therefore create a fresh local signal
            # and explicitly resume the scheduler; it never clears retained
            # quarantined tasks or their tenant execution fences.
            self._stop = asyncio.Event()
            self._runner.resume_scheduling()
            task = asyncio.create_task(
                self._run(), name="artifact-cleanup-execution-loop"
            )
            self._task = task
            task.add_done_callback(self._clear_completed_task)

    async def stop(self) -> None:
        self._stop.set()
        self._runner.request_stop()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self._runner.stop_grace_seconds
            )
        except TimeoutError:
            # Do not await an uncooperative lifecycle task indefinitely. The
            # runner has already cancelled it and retains its per-tenant fence
            # until it exits. Keep the loop task registered so a second loop
            # cannot be started while this shutdown tail is still unwinding.
            _LOGGER.warning("artifact_cleanup_execution_stop_grace_exceeded")
        except asyncio.CancelledError:
            return
        else:
            if self._task is task:
                self._task = None

    def _clear_completed_task(self, completed: asyncio.Task[None]) -> None:
        """Make a timed-out shutdown restartable only after its tail stops."""

        if self._task is completed:
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            try:
                await self._runner.run_once()
            except Exception:
                # Runner contains per-tenant failure handling. This final
                # boundary protects the worker process without logging opaque
                # backend exception text.
                _LOGGER.warning("artifact_cleanup_execution_cycle_failed")


def _replace(
    value: ArtifactCleanupExecutionResult, **changes: int | bool
) -> ArtifactCleanupExecutionResult:
    return ArtifactCleanupExecutionResult(
        tenants_scanned=changes.get("tenants_scanned", value.tenants_scanned),
        purged_artifacts=changes.get("purged_artifacts", value.purged_artifacts),
        quarantined_blobs=changes.get("quarantined_blobs", value.quarantined_blobs),
        reaped_blobs=changes.get("reaped_blobs", value.reaped_blobs),
        restored_blobs=changes.get("restored_blobs", value.restored_blobs),
        withheld_blobs=changes.get("withheld_blobs", value.withheld_blobs),
        already_clean_tenants=changes.get(
            "already_clean_tenants", value.already_clean_tenants
        ),
        failures=changes.get("failures", value.failures),
        deferred_tenants=changes.get("deferred_tenants", value.deferred_tenants),
        hung_tenants=changes.get("hung_tenants", value.hung_tenants),
        quarantine_capacity_reached=changes.get(
            "quarantine_capacity_reached", value.quarantine_capacity_reached
        ),
        audit_failures=changes.get("audit_failures", value.audit_failures),
    )


def _is_already_clean(outcome: ArtifactPhysicalCleanupOutcome) -> bool:
    return (
        outcome.purged_artifacts
        + outcome.quarantined_blobs
        + outcome.reaped_blobs
        + outcome.restored_blobs
        + outcome.withheld_blobs
        == 0
    )


def _execution_state_for_attempt(attempt: _LifecycleAttempt) -> str:
    """Return a closed, body-free audit state for a failed lifecycle attempt."""

    return {
        "failed": "failed",
        "cancelled": "cancelled_on_shutdown",
        "timed_out": "deadline_cancelled",
        "quarantined": "hung_quarantined",
    }.get(attempt.state, "failed")


async def _completed_cleanup_task() -> ArtifactPhysicalCleanupOutcome:
    """Placeholder only for a pre-task setup failure that still owns a fence."""

    return ArtifactPhysicalCleanupOutcome(org_id="artifact_cleanup_worker")


def _fair_org_page(
    *,
    org_ids: Sequence[str],
    cursor_after_org_id: str | None,
    limit: int,
) -> tuple[str, ...]:
    """Return one bounded circular page after the durable completed cursor.

    The source inventory is intentionally re-read every cycle.  A tenant that
    disappears after completion simply falls out of the next page; a tenant
    added before the cursor is reached on the wrapped portion.  Duplicate or
    malformed source values never yield duplicate work.
    """

    if limit < 1:
        raise ValueError("artifact cleanup page limit is invalid")
    ordered = tuple(sorted({org_id for org_id in org_ids if _valid_org_id(org_id)}))
    if not ordered:
        return ()
    if cursor_after_org_id is None or cursor_after_org_id not in ordered:
        return ordered[:limit]
    start = ordered.index(cursor_after_org_id) + 1
    circular = ordered[start:] + ordered[:start]
    return circular[:limit]


def _valid_org_id(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 256


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = (
    "ArtifactCleanupExecutionEnv",
    "ArtifactCleanupExecutionLoop",
    "ArtifactCleanupExecutionPort",
    "ArtifactCleanupExecutionResult",
    "ArtifactCleanupExecutionRunner",
)
