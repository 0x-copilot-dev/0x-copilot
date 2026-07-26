"""In-memory CAS checkpoint store for the E2 legacy migration."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationCheckpoint,
    LegacyMigrationStateError,
    LegacyMigrationStatus,
)


class InMemoryLegacyMigrationCheckpointStore:
    """Process-local semantic twin of the durable checkpoint adapters."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[tuple[str, str], LegacyMigrationCheckpoint] = {}

    async def load_or_create(
        self, *, checkpoint: LegacyMigrationCheckpoint
    ) -> LegacyMigrationCheckpoint:
        with self._lock:
            key = (checkpoint.org_id, checkpoint.migration_id)
            existing = self._states.get(key)
            if existing is None:
                self._states[key] = checkpoint
                return checkpoint
            if not _same_source(existing, checkpoint):
                raise LegacyMigrationStateError()
            return existing

    async def load(
        self, *, org_id: str, migration_id: str
    ) -> LegacyMigrationCheckpoint | None:
        with self._lock:
            return self._states.get((org_id, migration_id))

    async def compare_and_set(
        self,
        *,
        expected: LegacyMigrationCheckpoint,
        after_draft_id: str | None,
        status: LegacyMigrationStatus,
        report_digest: str | None,
        updated_at: datetime,
    ) -> LegacyMigrationCheckpoint | None:
        with self._lock:
            key = (expected.org_id, expected.migration_id)
            current = self._states.get(key)
            if current is None:
                raise LegacyMigrationStateError()
            if current != expected:
                return None
            _validate_transition(
                current=current,
                after_draft_id=after_draft_id,
                status=status,
            )
            updated = current.model_copy(
                update={
                    "after_draft_id": after_draft_id,
                    "status": status,
                    "report_digest": report_digest,
                    "revision": current.revision + 1,
                    "updated_at": updated_at,
                }
            )
            self._states[key] = updated
            return updated


def _same_source(
    left: LegacyMigrationCheckpoint, right: LegacyMigrationCheckpoint
) -> bool:
    return (
        left.org_id == right.org_id
        and left.migration_id == right.migration_id
        and left.source_digest == right.source_digest
    )


def _validate_transition(
    *,
    current: LegacyMigrationCheckpoint,
    after_draft_id: str | None,
    status: LegacyMigrationStatus,
) -> None:
    if current.status is LegacyMigrationStatus.BLOCKED and status != current.status:
        raise LegacyMigrationStateError()
    if current.status is LegacyMigrationStatus.COMPLETED and status not in {
        LegacyMigrationStatus.COMPLETED,
        LegacyMigrationStatus.BLOCKED,
        LegacyMigrationStatus.AUDIT_PENDING,
    }:
        raise LegacyMigrationStateError()
    if current.after_draft_id is not None and (
        after_draft_id is None or after_draft_id < current.after_draft_id
    ):
        raise LegacyMigrationStateError()


__all__ = ("InMemoryLegacyMigrationCheckpointStore",)
