"""In-memory semantic-parity state for artifact cleanup scheduling."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupDeferredTenant,
    ArtifactCleanupLease,
    ArtifactCleanupScheduleStateError,
    ArtifactCleanupTenantExecutionLease,
)


class InMemoryArtifactCleanupScheduleStore:
    """Thread-safe fenced cursor/lease store for tests and local development.

    It intentionally does not claim restart durability, but preserves the same
    owner, generation, retry, and expiry semantics as durable adapters.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._cursor: str | None = None
        self._lease_owner: str | None = None
        self._lease_fence_token = 0
        self._lease_expires_at: datetime | None = None
        self._deferred: dict[str, ArtifactCleanupDeferredTenant] = {}
        self._tenant_locks: dict[str, asyncio.Lock] = {}
        self._tenant_executions: dict[str, ArtifactCleanupTenantExecutionLease] = {}

    async def load_cursor(self) -> str | None:
        with self._lock:
            return self._cursor

    async def load_deferred_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        org_id: str,
        now: datetime,
    ) -> ArtifactCleanupDeferredTenant | None:
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_time(now)
        with self._lock:
            _require_active_fence(
                owner_id=owner_id,
                fence_token=fence_token,
                now=now,
                active_owner=self._lease_owner,
                active_fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            )
            deferred = self._deferred.get(org_id)
            return (
                deferred
                if deferred is not None and not deferred.is_eligible(now=now)
                else None
            )

    async def complete_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        expected_cursor: str | None,
        org_id: str,
        now: datetime,
    ) -> bool:
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_time(now)
        with self._lock:
            if (
                not _active_fence_matches(
                    owner_id=owner_id,
                    fence_token=fence_token,
                    now=now,
                    active_owner=self._lease_owner,
                    active_fence_token=self._lease_fence_token,
                    expires_at=self._lease_expires_at,
                )
                or self._cursor != expected_cursor
            ):
                return False
            self._cursor = org_id
            self._deferred.pop(org_id, None)
            return True

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
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_time(now)
        _validate_retry(retry_base_seconds, retry_max_seconds)
        with self._lock:
            if (
                not _active_fence_matches(
                    owner_id=owner_id,
                    fence_token=fence_token,
                    now=now,
                    active_owner=self._lease_owner,
                    active_fence_token=self._lease_fence_token,
                    expires_at=self._lease_expires_at,
                )
                or self._cursor != expected_cursor
            ):
                return None
            prior = self._deferred.get(org_id)
            failure_count = (prior.failure_count if prior is not None else 0) + 1
            retry_after = _retry_seconds(
                failure_count=failure_count,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
            deferred = ArtifactCleanupDeferredTenant(
                org_id=org_id,
                failure_count=failure_count,
                retry_not_before=now + timedelta(seconds=retry_after),
                last_failed_at=now,
            )
            self._deferred[org_id] = deferred
            self._cursor = org_id
            return deferred

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        duration_seconds: float,
    ) -> ArtifactCleanupLease | None:
        _validate_id(owner_id)
        _validate_time(now)
        _validate_duration(duration_seconds)
        with self._lock:
            if _lease_is_active(self._lease_expires_at, now):
                return None
            self._lease_fence_token += 1
            self._lease_owner = owner_id
            self._lease_expires_at = now + timedelta(seconds=duration_seconds)
            return _lease(
                owner_id=owner_id,
                fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            )

    async def renew_lease(
        self,
        *,
        owner_id: str,
        fence_token: int,
        now: datetime,
        duration_seconds: float,
    ) -> ArtifactCleanupLease | None:
        _validate_id(owner_id)
        _validate_time(now)
        _validate_duration(duration_seconds)
        with self._lock:
            if not _active_fence_matches(
                owner_id=owner_id,
                fence_token=fence_token,
                now=now,
                active_owner=self._lease_owner,
                active_fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            ):
                return None
            self._lease_expires_at = now + timedelta(seconds=duration_seconds)
            return _lease(
                owner_id=owner_id,
                fence_token=fence_token,
                expires_at=self._lease_expires_at,
            )

    async def release_lease(
        self, *, owner_id: str, fence_token: int, now: datetime
    ) -> None:
        _validate_id(owner_id)
        _validate_time(now)
        with self._lock:
            if _active_fence_matches(
                owner_id=owner_id,
                fence_token=fence_token,
                now=now,
                active_owner=self._lease_owner,
                active_fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            ):
                self._lease_owner = None
                self._lease_expires_at = None

    async def acquire_tenant_execution(
        self,
        *,
        owner_id: str,
        fence_token: int,
        org_id: str,
        now: datetime,
    ) -> ArtifactCleanupTenantExecutionLease | None:
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_time(now)
        with self._lock:
            if not _active_fence_matches(
                owner_id=owner_id,
                fence_token=fence_token,
                now=now,
                active_owner=self._lease_owner,
                active_fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            ):
                return None
            lock = self._tenant_locks.setdefault(org_id, asyncio.Lock())
            if lock.locked():
                return None
        await lock.acquire()
        with self._lock:
            if not _active_fence_matches(
                owner_id=owner_id,
                fence_token=fence_token,
                now=now,
                active_owner=self._lease_owner,
                active_fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            ):
                lock.release()
                return None
            execution = ArtifactCleanupTenantExecutionLease(
                org_id=org_id,
                owner_id=owner_id,
                fence_token=fence_token,
                execution_token=uuid4().hex,
            )
            self._tenant_executions[execution.execution_token] = execution
            return execution

    async def validate_tenant_execution(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> bool:
        _validate_time(now)
        with self._lock:
            return self._tenant_executions.get(
                execution.execution_token
            ) == execution and _active_fence_matches(
                owner_id=execution.owner_id,
                fence_token=execution.fence_token,
                now=now,
                active_owner=self._lease_owner,
                active_fence_token=self._lease_fence_token,
                expires_at=self._lease_expires_at,
            )

    async def release_tenant_execution(
        self, *, execution: ArtifactCleanupTenantExecutionLease
    ) -> None:
        with self._lock:
            if self._tenant_executions.get(execution.execution_token) != execution:
                return
            self._tenant_executions.pop(execution.execution_token, None)
            lock = self._tenant_locks.get(execution.org_id)
            if lock is not None and lock.locked():
                lock.release()


def _lease(
    *, owner_id: str, fence_token: int, expires_at: datetime | None
) -> ArtifactCleanupLease:
    if expires_at is None:
        raise ArtifactCleanupScheduleStateError("cleanup scheduler lease is invalid")
    return ArtifactCleanupLease(
        owner_id=owner_id,
        fence_token=fence_token,
        expires_at=expires_at,
    )


def _active_fence_matches(
    *,
    owner_id: str,
    fence_token: int,
    now: datetime,
    active_owner: str | None,
    active_fence_token: int,
    expires_at: datetime | None,
) -> bool:
    return (
        active_owner == owner_id
        and active_fence_token == fence_token
        and _lease_is_active(expires_at, now)
    )


def _require_active_fence(**kwargs: object) -> None:
    if not _active_fence_matches(**kwargs):  # type: ignore[arg-type]
        raise ArtifactCleanupScheduleStateError("cleanup scheduler lease is stale")


def _lease_is_active(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at > now


def _retry_seconds(
    *, failure_count: int, base_seconds: float, max_seconds: float
) -> float:
    return min(base_seconds * (2 ** min(failure_count - 1, 20)), max_seconds)


def _validate_id(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler identifier is invalid"
        )


def _validate_time(value: datetime) -> None:
    if value.tzinfo is None:
        raise ArtifactCleanupScheduleStateError("cleanup scheduler time is invalid")


def _validate_duration(value: float) -> None:
    if value <= 0:
        raise ArtifactCleanupScheduleStateError("cleanup scheduler duration is invalid")


def _validate_retry(base_seconds: float, max_seconds: float) -> None:
    if base_seconds <= 0 or max_seconds < base_seconds:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler retry bounds are invalid"
        )


__all__ = ("InMemoryArtifactCleanupScheduleStore",)
