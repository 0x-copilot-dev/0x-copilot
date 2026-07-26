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
from typing import Callable, Protocol, runtime_checkable
from uuid import uuid4

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupDeferredTenant,
    ArtifactCleanupLease,
    ArtifactCleanupScheduleStore,
)

from agent_runtime.observability.lifecycle_metrics import (
    LifecycleOperationalMetrics,
    get_lifecycle_operational_metrics,
)
from runtime_adapters.artifact_lifecycle import ArtifactPhysicalCleanupOutcome


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

    DEFAULT_INTERVAL_SECONDS = 900.0
    DEFAULT_MAX_ORGS = 100
    DEFAULT_LIMIT_PER_ORG = 100
    DEFAULT_LEASE_SECONDS = 1_800.0
    DEFAULT_RETRY_BASE_SECONDS = 60.0
    DEFAULT_RETRY_MAX_SECONDS = 3_600.0

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
    audit_failures: int = 0


class ArtifactCleanupExecutionRunner:
    """Run bounded tenant cleanup with safe audit and metric evidence.

    Redelivery is safe because the lifecycle state machine is its own durable
    idempotency key: candidates/quarantine are atomically advanced, references
    restore rather than delete, and an interrupted reaping lane is recovered on
    the next pass.  This runner never advances a cursor before that state has
    been durably reconciled.
    """

    _AUDIT_EVENT_TYPE = "artifact_cleanup.executed"

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
        metrics: LifecycleOperationalMetrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not 1 <= max_orgs <= 500
            or not 1 <= limit_per_org <= 500
            or lease_seconds <= 0
            or retry_base_seconds <= 0
            or retry_max_seconds < retry_base_seconds
        ):
            raise ValueError("artifact cleanup execution bounds are invalid")
        self._persistence = persistence
        self._schedule = schedule
        self._max_orgs = max_orgs
        self._limit_per_org = limit_per_org
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._owner_id = f"artifact-cleanup-{uuid4().hex}"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._metrics = (
            metrics if metrics is not None else get_lifecycle_operational_metrics()
        )

    async def run_once(
        self, *, now: datetime | None = None
    ) -> ArtifactCleanupExecutionResult:
        """Run one page without logging tenant, artifact, or content details."""

        fixed_now = _utc(now) if now is not None else None

        def current_time() -> datetime:
            return fixed_now if fixed_now is not None else _utc(self._clock())

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
                outcome = await self._persistence.execute_artifact_cleanup(
                    org_id=org_id,
                    now=reference_now,
                    limit=self._limit_per_org,
                )
            except Exception:
                self._record_metric(outcome="failed")
                # Exception text can include a backend path or body-derived
                # value.  Keep the worker log deliberately body-free.
                _LOGGER.warning("artifact_cleanup_execution_tenant_failed")
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
                )
                if audit_failed:
                    self._record_metric(outcome="audit_failed")
                result = _replace(
                    result,
                    tenants_scanned=result.tenants_scanned + 1,
                    failures=result.failures + 1,
                    deferred_tenants=result.deferred_tenants + 1,
                    audit_failures=result.audit_failures + int(audit_failed),
                )
                # The failed tenant is durably visible with a bounded retry;
                # its cursor transition is atomic with that defer state, so
                # later tenants can continue without silently losing it.
                cursor = org_id
                continue

            # A long-running lifecycle call may span a heartbeat failure or a
            # takeover.  Revalidate the exact generation before recording an
            # audit outcome or changing the fair cursor; the lifecycle
            # adapter's own idempotent/reference-safe state machine handles a
            # conservative later retry if ownership was lost mid-call.
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
                purged_artifacts=result.purged_artifacts + outcome.purged_artifacts,
                quarantined_blobs=result.quarantined_blobs + outcome.quarantined_blobs,
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
                # lifecycle may be retried, which is safe; do not speculate
                # about later cursor positions in this cycle.
                _LOGGER.warning("artifact_cleanup_execution_cursor_not_advanced")
                return _replace(result, failures=result.failures + 1)
            cursor = org_id
        return result

    async def _write_audit(
        self,
        *,
        outcome: ArtifactPhysicalCleanupOutcome,
        deferred: ArtifactCleanupDeferredTenant | None = None,
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
            self._task = asyncio.create_task(
                self._run(), name="artifact-cleanup-execution-loop"
            )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            return

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
    value: ArtifactCleanupExecutionResult, **changes: int
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
