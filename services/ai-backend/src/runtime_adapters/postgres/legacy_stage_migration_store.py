"""Postgres mapping store for E2 D5 legacy-stage migration."""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationStateError,
    LegacyStageMigrationOutcome,
    LegacyStageMigrationRecord,
)


_TABLE = "runtime_e2_legacy_stage_migrations"
_WORKER_ROLE = "worker"
_COLUMNS = (
    "org_id",
    "migration_id",
    "run_id",
    "legacy_stage_id",
    "source_digest",
    "outcome",
    "canonical_stage_id",
    "queue_cancelled",
    "reconciler_frozen",
    "revision",
    "created_at",
    "updated_at",
)
_SELECT = ", ".join(_COLUMNS)


class PostgresLegacyStageMigrationStore:
    """Atomic source-fenced per-stage mapping for worker-only D5 migration."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def load_or_create(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_TABLE} ({_SELECT})
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (org_id, migration_id, run_id, legacy_stage_id)
                        DO NOTHING
                        RETURNING {_SELECT}
                        """,
                        _values(record),
                    )
                    row = await cursor.fetchone()
                    if row is not None:
                        return _from_row(row)
                    existing = await _load(
                        conn,
                        org_id=record.org_id,
                        migration_id=record.migration_id,
                        run_id=record.run_id,
                        legacy_stage_id=record.legacy_stage_id,
                        for_update=True,
                    )
                    if existing is None or _facts(existing) != _facts(record):
                        raise LegacyMigrationStateError()
                    return existing
        except LegacyMigrationStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver boundary
            raise LegacyMigrationStateError() from exc

    async def load(
        self,
        *,
        org_id: str,
        migration_id: str,
        run_id: str,
        legacy_stage_id: str,
    ) -> LegacyStageMigrationRecord | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                return await _load(
                    conn,
                    org_id=org_id,
                    migration_id=migration_id,
                    run_id=run_id,
                    legacy_stage_id=legacy_stage_id,
                )
        except LegacyMigrationStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver boundary
            raise LegacyMigrationStateError() from exc

    async def replace_frozen(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        """Replace only a frozen checkpoint under the row lock."""

        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET source_digest = %s,
                               outcome = %s,
                               canonical_stage_id = %s,
                               queue_cancelled = %s,
                               reconciler_frozen = %s,
                               revision = %s,
                               updated_at = %s
                         WHERE org_id = %s AND migration_id = %s AND run_id = %s
                           AND legacy_stage_id = %s
                           AND outcome = %s
                        RETURNING {_SELECT}
                        """,
                        (
                            record.source_digest,
                            record.outcome.value,
                            record.canonical_stage_id,
                            record.queue_cancelled,
                            record.reconciler_frozen,
                            record.revision,
                            record.updated_at,
                            record.org_id,
                            record.migration_id,
                            record.run_id,
                            record.legacy_stage_id,
                            LegacyStageMigrationOutcome.FROZEN_RECONCILE.value,
                        ),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise LegacyMigrationStateError()
                    return _from_row(row)
        except LegacyMigrationStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver boundary
            raise LegacyMigrationStateError() from exc


async def _load(
    conn: object,
    *,
    org_id: str,
    migration_id: str,
    run_id: str,
    legacy_stage_id: str,
    for_update: bool = False,
) -> LegacyStageMigrationRecord | None:
    suffix = " FOR UPDATE" if for_update else ""
    cursor = await conn.execute(  # type: ignore[attr-defined]
        f"""
        SELECT {_SELECT}
          FROM {_TABLE}
         WHERE org_id = %s AND migration_id = %s AND run_id = %s
           AND legacy_stage_id = %s{suffix}
        """,
        (org_id, migration_id, run_id, legacy_stage_id),
    )
    row = await cursor.fetchone()
    return _from_row(row) if row is not None else None


def _values(record: LegacyStageMigrationRecord) -> tuple[object, ...]:
    return (
        record.org_id,
        record.migration_id,
        record.run_id,
        record.legacy_stage_id,
        record.source_digest,
        record.outcome.value,
        record.canonical_stage_id,
        record.queue_cancelled,
        record.reconciler_frozen,
        record.revision,
        record.created_at,
        record.updated_at,
    )


def _from_row(
    row: Mapping[str, object] | tuple[object, ...],
) -> LegacyStageMigrationRecord:
    try:
        values = (
            row if isinstance(row, Mapping) else dict(zip(_COLUMNS, row, strict=True))
        )
        return LegacyStageMigrationRecord(
            org_id=str(values["org_id"]),
            migration_id=str(values["migration_id"]),
            run_id=str(values["run_id"]),
            legacy_stage_id=str(values["legacy_stage_id"]),
            source_digest=str(values["source_digest"]),
            outcome=LegacyStageMigrationOutcome(str(values["outcome"])),
            canonical_stage_id=(
                str(values["canonical_stage_id"])
                if values["canonical_stage_id"] is not None
                else None
            ),
            queue_cancelled=bool(values["queue_cancelled"]),
            reconciler_frozen=bool(values["reconciler_frozen"]),
            revision=int(values["revision"]),
            created_at=values["created_at"],  # type: ignore[arg-type]
            updated_at=values["updated_at"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationStateError() from exc


def _facts(record: LegacyStageMigrationRecord) -> tuple[object, ...]:
    return (
        record.source_digest,
        record.outcome,
        record.canonical_stage_id,
        record.queue_cancelled,
        record.reconciler_frozen,
    )


__all__ = ("PostgresLegacyStageMigrationStore",)
