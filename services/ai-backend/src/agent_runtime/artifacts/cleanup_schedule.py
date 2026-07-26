"""Durable fair-scheduling state for physical artifact cleanup.

The cleanup worker is intentionally separate from the lifecycle adapters that
perform destructive work.  This contract owns only cursor, retry, and lease
metadata; it never accepts artifact, blob, reference, legal-hold, or body
data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


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
    ) -> ArtifactCleanupTenantExecutionLease | None:
        """Acquire the tenant-exclusive lifecycle execution lock if available."""

    async def validate_tenant_execution(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> bool:
        """Return whether the held tenant lock and global fence are still active."""

    async def release_tenant_execution(
        self, *, execution: ArtifactCleanupTenantExecutionLease
    ) -> None:
        """Release only this execution handle after its lifecycle pass ends."""


__all__ = (
    "ArtifactCleanupDeferredTenant",
    "ArtifactCleanupLease",
    "ArtifactCleanupTenantExecutionLease",
    "ArtifactCleanupScheduleStateError",
    "ArtifactCleanupScheduleStore",
)
