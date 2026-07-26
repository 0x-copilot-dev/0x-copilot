"""Postgres CAS state for the E2 legacy migration prerequisite."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationCheckpoint,
    LegacyMigrationStateError,
    LegacyMigrationStatus,
)


_TABLE = "runtime_e2_legacy_migrations"
_WORKER_ROLE = "worker"
_COLUMNS = (
    "org_id",
    "migration_id",
    "source_digest",
    "after_draft_id",
    "status",
    "report_digest",
    "revision",
    "created_at",
    "updated_at",
)
_SELECT = ", ".join(_COLUMNS)


class PostgresLegacyMigrationCheckpointStore:
    """One tenant-scoped checkpoint row with revision-based CAS."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def load_or_create(
        self, *, checkpoint: LegacyMigrationCheckpoint
    ) -> LegacyMigrationCheckpoint:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_TABLE} (
                            org_id, migration_id, source_digest, after_draft_id,
                            status, report_digest, revision, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (org_id, migration_id) DO NOTHING
                        RETURNING {_SELECT}
                        """,
                        _values(checkpoint),
                    )
                    row = await cursor.fetchone()
                    if row is not None:
                        return _from_row(row)
                    existing = await _load(
                        conn,
                        org_id=checkpoint.org_id,
                        migration_id=checkpoint.migration_id,
                        for_update=True,
                    )
                    if existing is None or not _same_source(existing, checkpoint):
                        raise LegacyMigrationStateError()
                    return existing
        except LegacyMigrationStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver failure boundary
            raise LegacyMigrationStateError() from exc

    async def load(
        self, *, org_id: str, migration_id: str
    ) -> LegacyMigrationCheckpoint | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                return await _load(conn, org_id=org_id, migration_id=migration_id)
        except LegacyMigrationStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver failure boundary
            raise LegacyMigrationStateError() from exc

    async def compare_and_set(
        self,
        *,
        expected: LegacyMigrationCheckpoint,
        after_draft_id: str | None,
        status: LegacyMigrationStatus,
        report_digest: str | None,
        updated_at: datetime,
    ) -> LegacyMigrationCheckpoint | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    current = await _load(
                        conn,
                        org_id=expected.org_id,
                        migration_id=expected.migration_id,
                        for_update=True,
                    )
                    if current is None:
                        raise LegacyMigrationStateError()
                    if current != expected:
                        return None
                    _validate_transition(
                        current=current,
                        after_draft_id=after_draft_id,
                        status=status,
                    )
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET after_draft_id = %s,
                               status = %s,
                               report_digest = %s,
                               revision = revision + 1,
                               updated_at = %s
                         WHERE org_id = %s
                           AND migration_id = %s
                           AND revision = %s
                        RETURNING {_SELECT}
                        """,
                        (
                            after_draft_id,
                            status.value,
                            report_digest,
                            updated_at,
                            expected.org_id,
                            expected.migration_id,
                            expected.revision,
                        ),
                    )
                    row = await cursor.fetchone()
                    return _from_row(row) if row is not None else None
        except LegacyMigrationStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver failure boundary
            raise LegacyMigrationStateError() from exc


async def _load(
    conn: object,
    *,
    org_id: str,
    migration_id: str,
    for_update: bool = False,
) -> LegacyMigrationCheckpoint | None:
    suffix = " FOR UPDATE" if for_update else ""
    cursor = await conn.execute(  # type: ignore[attr-defined]
        f"""
        SELECT {_SELECT}
          FROM {_TABLE}
         WHERE org_id = %s AND migration_id = %s{suffix}
        """,
        (org_id, migration_id),
    )
    row = await cursor.fetchone()
    return _from_row(row) if row is not None else None


def _values(checkpoint: LegacyMigrationCheckpoint) -> tuple[object, ...]:
    return (
        checkpoint.org_id,
        checkpoint.migration_id,
        checkpoint.source_digest,
        checkpoint.after_draft_id,
        checkpoint.status.value,
        checkpoint.report_digest,
        checkpoint.revision,
        checkpoint.created_at,
        checkpoint.updated_at,
    )


def _from_row(
    row: Mapping[str, object] | tuple[object, ...],
) -> LegacyMigrationCheckpoint:
    try:
        if isinstance(row, Mapping):
            values = row
        else:
            values = dict(zip(_COLUMNS, row, strict=True))
        return LegacyMigrationCheckpoint(
            org_id=str(values["org_id"]),
            migration_id=str(values["migration_id"]),
            source_digest=str(values["source_digest"]),
            after_draft_id=(
                str(values["after_draft_id"])
                if values["after_draft_id"] is not None
                else None
            ),
            status=LegacyMigrationStatus(str(values["status"])),
            report_digest=(
                str(values["report_digest"])
                if values["report_digest"] is not None
                else None
            ),
            revision=int(values["revision"]),
            created_at=values["created_at"],  # type: ignore[arg-type]
            updated_at=values["updated_at"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationStateError() from exc


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


__all__ = ("PostgresLegacyMigrationCheckpointStore",)
