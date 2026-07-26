"""In-memory semantic twin for E2 D5 legacy-stage mappings."""

from __future__ import annotations

from threading import RLock

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationStateError,
    LegacyStageMigrationOutcome,
    LegacyStageMigrationRecord,
)


class InMemoryLegacyStageMigrationStore:
    """Tenant-scoped source-bound records; never stores source bodies or commands."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str, str, str], LegacyStageMigrationRecord] = {}

    async def load_or_create(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        key = _key(record)
        with self._lock:
            existing = self._records.get(key)
            if existing is None:
                self._records[key] = record
                return record
            if _facts(existing) != _facts(record):
                raise LegacyMigrationStateError()
            return existing

    async def load(
        self,
        *,
        org_id: str,
        migration_id: str,
        run_id: str,
        legacy_stage_id: str,
    ) -> LegacyStageMigrationRecord | None:
        with self._lock:
            return self._records.get((org_id, migration_id, run_id, legacy_stage_id))

    async def replace_frozen(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        """Advance an observation-only frozen record after a new safe scan."""

        key = _key(record)
        with self._lock:
            existing = self._records.get(key)
            if (
                existing is None
                or existing.outcome is not LegacyStageMigrationOutcome.FROZEN_RECONCILE
            ):
                raise LegacyMigrationStateError()
            self._records[key] = record
            return record


def _key(record: LegacyStageMigrationRecord) -> tuple[str, str, str, str]:
    return (record.org_id, record.migration_id, record.run_id, record.legacy_stage_id)


def _facts(record: LegacyStageMigrationRecord) -> tuple[object, ...]:
    return (
        record.source_digest,
        record.outcome,
        record.canonical_stage_id,
        record.queue_cancelled,
        record.reconciler_frozen,
    )


__all__ = ("InMemoryLegacyStageMigrationStore",)
