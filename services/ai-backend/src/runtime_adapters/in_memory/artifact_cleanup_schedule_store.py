"""In-memory semantic-parity state for artifact cleanup scheduling."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupScheduleStateError,
)


class InMemoryArtifactCleanupScheduleStore:
    """Thread-safe cursor/lease store used by tests and local development.

    This adapter intentionally does not claim restart durability.  It mirrors
    the same owner-checked CAS and lease semantics as the durable adapters.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._cursor: str | None = None
        self._lease_owner: str | None = None
        self._lease_expires_at: datetime | None = None

    async def load_cursor(self) -> str | None:
        with self._lock:
            return self._cursor

    async def advance_cursor(
        self,
        *,
        owner_id: str,
        expected: str | None,
        next_cursor: str,
    ) -> bool:
        _validate_id(owner_id)
        _validate_id(next_cursor)
        with self._lock:
            if self._lease_owner != owner_id or self._cursor != expected:
                return False
            self._cursor = next_cursor
            return True

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        _validate_id(owner_id)
        if now.tzinfo is None or expires_at.tzinfo is None or expires_at <= now:
            raise ArtifactCleanupScheduleStateError(
                "cleanup scheduler lease is invalid"
            )
        with self._lock:
            if (
                self._lease_owner is not None
                and self._lease_owner != owner_id
                and self._lease_expires_at is not None
                and self._lease_expires_at > now
            ):
                return False
            self._lease_owner = owner_id
            self._lease_expires_at = expires_at
            return True

    async def release_lease(self, *, owner_id: str) -> None:
        _validate_id(owner_id)
        with self._lock:
            if self._lease_owner == owner_id:
                self._lease_owner = None
                self._lease_expires_at = None


def _validate_id(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler identifier is invalid"
        )


__all__ = ("InMemoryArtifactCleanupScheduleStore",)
