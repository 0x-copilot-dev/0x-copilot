"""Durable fair-scheduling state for physical artifact cleanup.

The cleanup worker is intentionally separate from the lifecycle adapters that
perform destructive work.  This module owns only a global cursor and a bounded
lease; it never accepts artifact, blob, reference, or legal-hold data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


class ArtifactCleanupScheduleStateError(RuntimeError):
    """The scheduler state is unavailable or malformed; callers fail closed."""


@runtime_checkable
class ArtifactCleanupScheduleStore(Protocol):
    """CAS-backed global cursor plus an exclusive bounded worker lease.

    ``cursor_after_org_id`` is the last tenant whose physical cleanup
    completed.  A runner advances it only after that tenant's trusted cleanup
    call returns; a failed tenant therefore remains next in rotation rather
    than being silently skipped.
    """

    async def load_cursor(self) -> str | None:
        """Return the last successfully completed tenant, if any."""

    async def advance_cursor(
        self,
        *,
        owner_id: str,
        expected: str | None,
        next_cursor: str,
    ) -> bool:
        """Advance one completed tenant under the caller's current lease."""

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Acquire the exclusive scheduler lease if it is absent or expired."""

    async def release_lease(self, *, owner_id: str) -> None:
        """Release an owned lease; stale owners cannot release a successor."""


__all__ = (
    "ArtifactCleanupScheduleStateError",
    "ArtifactCleanupScheduleStore",
)
