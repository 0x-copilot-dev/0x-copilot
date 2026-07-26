"""Durable fair-scheduling state for physical artifact cleanup.

The cleanup worker is intentionally separate from the lifecycle adapters that
perform destructive work.  This contract owns only cursor, retry, and lease
metadata; it never accepts artifact, blob, reference, legal-hold, or body
data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable


class ArtifactCleanupScheduleStateError(RuntimeError):
    """The scheduler state is unavailable or malformed; callers fail closed."""


@dataclass(frozen=True, slots=True)
class ArtifactCleanupLease:
    """One exclusive lease generation for a bounded cleanup execution cycle."""

    owner_id: str
    fence_token: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ArtifactCleanupDeferredTenant:
    """Durable retry metadata for one failed tenant cleanup pass."""

    org_id: str
    failure_count: int
    retry_not_before: datetime
    last_failed_at: datetime

    def is_eligible(self, *, now: datetime) -> bool:
        """Return whether its next bounded retry is due."""

        return self.retry_not_before <= now


@dataclass(frozen=True, slots=True)
class ArtifactCleanupTenantExecutionLease:
    """Exclusive tenant execution handle bound to one scheduler generation.

    This is deliberately identifier-only.  It carries no artifact, blob,
    reference, hold, or content data, and exists solely to keep a successor
    from running the same tenant's destructive lifecycle pass concurrently.
    """

    org_id: str
    owner_id: str
    fence_token: int
    execution_token: str


@dataclass(frozen=True, slots=True)
class ArtifactCleanupTrackedExecution:
    """Durable global admission and release state for a tenant execution fence."""

    execution: ArtifactCleanupTenantExecutionLease
    state: Literal["active", "quarantined", "release_pending"]
    release_failure_count: int = 0
    retry_not_before: datetime | None = None


@runtime_checkable
class ArtifactCleanupScheduleStore(Protocol):
    """CAS cursor, deferred retry, and renewable fenced-lease state.

    A failed tenant is durably deferred and advances the global cursor as one
    atomic transition.  It remains visible for a later eligible retry, while
    later tenants can continue in the current rotation.  Every state mutation
    is bound to a lease-generation token, so a stale worker cannot advance the
    cursor after an expiry/takeover.
    """

    async def load_cursor(self) -> str | None:
        """Return the last completed or deferred tenant, if any."""

    async def load_deferred_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        org_id: str,
        now: datetime,
    ) -> ArtifactCleanupDeferredTenant | None:
        """Return a not-yet-due retry state after validating the active fence.

        ``None`` means either the tenant has no retry state or its retry is
        now eligible.  This lets Postgres decide eligibility from database
        time rather than letting a skewed worker clock run it early.
        """

    async def complete_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        expected_cursor: str | None,
        org_id: str,
        now: datetime,
    ) -> bool:
        """Advance a successful tenant and clear its previous defer state."""

    async def defer_failed_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        expected_cursor: str | None,
        org_id: str,
        now: datetime,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> ArtifactCleanupDeferredTenant | None:
        """Record a bounded retry and advance exactly this failed tenant."""

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        duration_seconds: float,
    ) -> ArtifactCleanupLease | None:
        """Acquire a fresh lease generation if no current owner is active."""

    async def renew_lease(
        self,
        *,
        owner_id: str,
        fence_token: int,
        now: datetime,
        duration_seconds: float,
    ) -> ArtifactCleanupLease | None:
        """Extend only the exact active lease generation."""

    async def release_lease(
        self, *, owner_id: str, fence_token: int, now: datetime
    ) -> None:
        """Release only the exact unexpired generation; stale owners are inert."""

    async def acquire_tenant_execution(
        self,
        *,
        owner_id: str,
        fence_token: int,
        org_id: str,
        now: datetime,
        maximum_active_executions: int = 4,
    ) -> ArtifactCleanupTenantExecutionLease | None:
        """Atomically reserve global capacity and acquire one tenant fence."""

    async def validate_tenant_execution(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> bool:
        """Return whether the held tenant lock and global fence are still active."""

    async def release_tenant_execution(
        self, *, execution: ArtifactCleanupTenantExecutionLease
    ) -> bool:
        """Release only this handle and report whether its durable record cleared."""

    async def mark_tenant_execution_quarantined(
        self, *, execution: ArtifactCleanupTenantExecutionLease, now: datetime
    ) -> bool:
        """Record that this still-running fence consumes global capacity."""

    async def mark_tenant_execution_release_pending(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> ArtifactCleanupTrackedExecution | None:
        """Retain capacity after a failed release and set its bounded retry."""

    async def load_tenant_execution_release_pending(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> ArtifactCleanupTrackedExecution | None:
        """Return this execution only when its durable release retry is due."""

    async def list_tracked_tenant_executions(
        self,
    ) -> tuple[ArtifactCleanupTrackedExecution, ...]:
        """Return every capacity-consuming execution in deterministic order."""

    async def reconcile_orphaned_tenant_executions(self) -> int:
        """Remove records only after proving their adapter fence is absent."""


__all__ = (
    "ArtifactCleanupDeferredTenant",
    "ArtifactCleanupLease",
    "ArtifactCleanupTrackedExecution",
    "ArtifactCleanupTenantExecutionLease",
    "ArtifactCleanupScheduleStateError",
    "ArtifactCleanupScheduleStore",
)
