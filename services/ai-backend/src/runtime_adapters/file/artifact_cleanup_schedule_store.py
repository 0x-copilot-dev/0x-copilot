"""Restart-safe fenced cursor, retry, and lease state for desktop cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupDeferredTenant,
    ArtifactCleanupLease,
    ArtifactCleanupScheduleStateError,
    ArtifactCleanupTenantExecutionLease,
)
from runtime_adapters.file._advisory_lock import (
    acquire_exclusive,
    release_exclusive,
    try_acquire_exclusive,
)


class FileArtifactCleanupScheduleStore:
    """Atomically persisted, cross-process-locked scheduler metadata.

    The desktop backend is normally single worker, but this file state still
    uses an advisory lock and a monotonically increasing fence token so a
    stale process after expiry cannot overwrite a newer scheduler generation.
    """

    _SUBDIR = "artifact_cleanup_schedule"
    _STATE_FILENAME = "state.json"
    _LOCK_FILENAME = ".artifact-cleanup-schedule.lock"
    _DIR_MODE = 0o700
    _FILE_MODE = 0o600

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._path = self._dir / self._STATE_FILENAME
        self._lock_path = self._dir / self._LOCK_FILENAME
        self._lock = asyncio.Lock()
        self._tenant_execution_handles: dict[
            str, tuple[ArtifactCleanupTenantExecutionLease, int]
        ] = {}

    async def load_cursor(self) -> str | None:
        async with self._lock:
            with self._exclusive_lock():
                return self._read()["cursor"]

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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                _require_active_fence(
                    state=state,
                    owner_id=owner_id,
                    fence_token=fence_token,
                    now=now,
                )
                deferred = state["deferred"].get(org_id)
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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if (
                    not _active_fence_matches(
                        state=state,
                        owner_id=owner_id,
                        fence_token=fence_token,
                        now=now,
                    )
                    or state["cursor"] != expected_cursor
                ):
                    return False
                state["cursor"] = org_id
                state["deferred"].pop(org_id, None)
                self._write(state)
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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if (
                    not _active_fence_matches(
                        state=state,
                        owner_id=owner_id,
                        fence_token=fence_token,
                        now=now,
                    )
                    or state["cursor"] != expected_cursor
                ):
                    return None
                prior = state["deferred"].get(org_id)
                failure_count = (prior.failure_count if prior is not None else 0) + 1
                deferred = ArtifactCleanupDeferredTenant(
                    org_id=org_id,
                    failure_count=failure_count,
                    retry_not_before=now
                    + timedelta(
                        seconds=_retry_seconds(
                            failure_count=failure_count,
                            base_seconds=retry_base_seconds,
                            max_seconds=retry_max_seconds,
                        )
                    ),
                    last_failed_at=now,
                )
                state["deferred"][org_id] = deferred
                state["cursor"] = org_id
                self._write(state)
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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if _lease_is_active(state["lease_expires_at"], now):
                    return None
                state["lease_fence_token"] += 1
                state["lease_owner"] = owner_id
                state["lease_expires_at"] = now + timedelta(seconds=duration_seconds)
                self._write(state)
                return _lease_from_state(state)

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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if not _active_fence_matches(
                    state=state,
                    owner_id=owner_id,
                    fence_token=fence_token,
                    now=now,
                ):
                    return None
                state["lease_expires_at"] = now + timedelta(seconds=duration_seconds)
                self._write(state)
                return _lease_from_state(state)

    async def release_lease(
        self, *, owner_id: str, fence_token: int, now: datetime
    ) -> None:
        _validate_id(owner_id)
        _validate_time(now)
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if not _active_fence_matches(
                    state=state,
                    owner_id=owner_id,
                    fence_token=fence_token,
                    now=now,
                ):
                    return
                state["lease_owner"] = None
                state["lease_expires_at"] = None
                self._write(state)

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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if not _active_fence_matches(
                    state=state,
                    owner_id=owner_id,
                    fence_token=fence_token,
                    now=now,
                ):
                    return None
            descriptor = os.open(
                self._tenant_execution_lock_path(org_id),
                os.O_CREAT | os.O_RDWR,
                self._FILE_MODE,
            )
            try:
                if not try_acquire_exclusive(descriptor):
                    os.close(descriptor)
                    return None
                with self._exclusive_lock():
                    state = self._read()
                    if not _active_fence_matches(
                        state=state,
                        owner_id=owner_id,
                        fence_token=fence_token,
                        now=now,
                    ):
                        release_exclusive(descriptor)
                        os.close(descriptor)
                        return None
                execution = ArtifactCleanupTenantExecutionLease(
                    org_id=org_id,
                    owner_id=owner_id,
                    fence_token=fence_token,
                    execution_token=uuid4().hex,
                )
                self._tenant_execution_handles[execution.execution_token] = (
                    execution,
                    descriptor,
                )
                return execution
            except OSError as exc:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise ArtifactCleanupScheduleStateError() from exc

    async def validate_tenant_execution(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> bool:
        _validate_time(now)
        async with self._lock:
            handle = self._tenant_execution_handles.get(execution.execution_token)
            if handle is None or handle[0] != execution:
                return False
            with self._exclusive_lock():
                state = self._read()
                return _active_fence_matches(
                    state=state,
                    owner_id=execution.owner_id,
                    fence_token=execution.fence_token,
                    now=now,
                )

    async def release_tenant_execution(
        self, *, execution: ArtifactCleanupTenantExecutionLease
    ) -> None:
        async with self._lock:
            handle = self._tenant_execution_handles.get(execution.execution_token)
            if handle is None or handle[0] != execution:
                return
            self._tenant_execution_handles.pop(execution.execution_token, None)
            try:
                release_exclusive(handle[1])
            finally:
                os.close(handle[1])

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_state()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError
            # 0017 persisted an unfenced lease.  Preserve its cursor on
            # upgrade, but deliberately discard that legacy ownership: it
            # cannot safely prove a generation against a newer worker.
            if set(raw) == {"cursor", "lease_owner", "lease_expires_at"}:
                cursor = raw["cursor"]
                if cursor is not None:
                    _validate_id(cursor)
                return {
                    "cursor": cursor,
                    "lease_owner": None,
                    "lease_fence_token": 0,
                    "lease_expires_at": None,
                    "deferred": {},
                }
            if set(raw) != {
                "cursor",
                "lease_owner",
                "lease_fence_token",
                "lease_expires_at",
                "deferred",
            }:
                raise ValueError
            cursor = raw["cursor"]
            owner = raw["lease_owner"]
            token = raw["lease_fence_token"]
            expires = raw["lease_expires_at"]
            deferred_raw = raw["deferred"]
            if cursor is not None:
                _validate_id(cursor)
            if owner is not None:
                _validate_id(owner)
            if not isinstance(token, int) or token < 0:
                raise ValueError
            if expires is not None:
                expires = datetime.fromisoformat(str(expires))
                _validate_time(expires)
            if (owner is None) != (expires is None):
                raise ValueError
            if not isinstance(deferred_raw, list):
                raise ValueError
            deferred = {
                row.org_id: row
                for row in (_deferred_from_json(item) for item in deferred_raw)
            }
            if len(deferred) != len(deferred_raw):
                raise ValueError
            return {
                "cursor": cursor,
                "lease_owner": owner,
                "lease_fence_token": token,
                "lease_expires_at": expires,
                "deferred": deferred,
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactCleanupScheduleStateError() from exc

    def _write(self, state: dict[str, Any]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        try:
            payload = {
                "cursor": state["cursor"],
                "lease_owner": state["lease_owner"],
                "lease_fence_token": state["lease_fence_token"],
                "lease_expires_at": _time_to_json(state["lease_expires_at"]),
                "deferred": [
                    _deferred_to_json(row)
                    for row in sorted(
                        state["deferred"].values(), key=lambda row: row.org_id
                    )
                ],
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, self._FILE_MODE
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise ArtifactCleanupScheduleStateError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        descriptor: int | None = None
        acquired = False
        try:
            descriptor = os.open(
                self._lock_path, os.O_CREAT | os.O_RDWR, self._FILE_MODE
            )
            acquire_exclusive(descriptor)
            acquired = True
            yield
        finally:
            if descriptor is not None:
                try:
                    if acquired:
                        release_exclusive(descriptor)
                finally:
                    os.close(descriptor)

    def _sync_directory(self) -> None:
        try:
            descriptor = os.open(self._dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _tenant_execution_lock_path(self, org_id: str) -> Path:
        digest = hashlib.sha256(org_id.encode("utf-8")).hexdigest()
        return self._dir / f".artifact-cleanup-tenant-{digest}.lock"


def _empty_state() -> dict[str, Any]:
    return {
        "cursor": None,
        "lease_owner": None,
        "lease_fence_token": 0,
        "lease_expires_at": None,
        "deferred": {},
    }


def _lease_from_state(state: dict[str, Any]) -> ArtifactCleanupLease:
    owner = state["lease_owner"]
    expires_at = state["lease_expires_at"]
    if not isinstance(owner, str) or not isinstance(expires_at, datetime):
        raise ArtifactCleanupScheduleStateError("cleanup scheduler lease is invalid")
    return ArtifactCleanupLease(
        owner_id=owner,
        fence_token=state["lease_fence_token"],
        expires_at=expires_at,
    )


def _active_fence_matches(
    *, state: dict[str, Any], owner_id: str, fence_token: int, now: datetime
) -> bool:
    return (
        state["lease_owner"] == owner_id
        and state["lease_fence_token"] == fence_token
        and _lease_is_active(state["lease_expires_at"], now)
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


def _deferred_from_json(raw: object) -> ArtifactCleanupDeferredTenant:
    if not isinstance(raw, dict) or set(raw) != {
        "org_id",
        "failure_count",
        "retry_not_before",
        "last_failed_at",
    }:
        raise ValueError
    org_id = raw["org_id"]
    count = raw["failure_count"]
    _validate_id(org_id)
    if not isinstance(count, int) or count < 1:
        raise ValueError
    retry_not_before = datetime.fromisoformat(str(raw["retry_not_before"]))
    last_failed_at = datetime.fromisoformat(str(raw["last_failed_at"]))
    _validate_time(retry_not_before)
    _validate_time(last_failed_at)
    return ArtifactCleanupDeferredTenant(
        org_id=org_id,
        failure_count=count,
        retry_not_before=retry_not_before,
        last_failed_at=last_failed_at,
    )


def _deferred_to_json(value: ArtifactCleanupDeferredTenant) -> dict[str, object]:
    return {
        "org_id": value.org_id,
        "failure_count": value.failure_count,
        "retry_not_before": value.retry_not_before.isoformat(),
        "last_failed_at": value.last_failed_at.isoformat(),
    }


def _time_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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


__all__ = ("FileArtifactCleanupScheduleStore",)
