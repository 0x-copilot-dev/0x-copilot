"""Postgres cursor and lease adapter for fair artifact cleanup scheduling."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupScheduleStateError,
)


_TABLE = "runtime_artifact_cleanup_schedule_state"
_SOURCE = "artifact_cleanup_execution"
_WORKER_ROLE = "worker"


class PostgresArtifactCleanupScheduleStore:
    """Worker-owned global cursor/lease state with owner-checked CAS writes."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def load_cursor(self) -> str | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"SELECT cursor_after_org_id FROM {_TABLE} WHERE source = %s",
                    (_SOURCE,),
                )
                row = await cursor.fetchone()
            if row is None:
                return None
            return _cursor_from_row(row)
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def advance_cursor(
        self,
        *,
        owner_id: str,
        expected: str | None,
        next_cursor: str,
    ) -> bool:
        _validate_id(owner_id)
        _validate_id(next_cursor)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await _ensure_state_row(conn)
                    row = await _locked_state_row(conn)
                    if (
                        _cursor_from_row(row) != expected
                        or row["lease_owner_id"] != owner_id
                    ):
                        return False
                    await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET cursor_after_org_id = %s, updated_at = now()
                         WHERE source = %s AND lease_owner_id = %s
                        """,
                        (next_cursor, _SOURCE, owner_id),
                    )
                    return True
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

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
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await _ensure_state_row(conn)
                    row = await _locked_state_row(conn)
                    active_owner = row["lease_owner_id"]
                    active_until = row["lease_expires_at"]
                    if (
                        active_owner is not None
                        and active_owner != owner_id
                        and isinstance(active_until, datetime)
                        and active_until > now
                    ):
                        return False
                    await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET lease_owner_id = %s,
                               lease_expires_at = %s,
                               updated_at = now()
                         WHERE source = %s
                        """,
                        (owner_id, expires_at, _SOURCE),
                    )
                    return True
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def release_lease(self, *, owner_id: str) -> None:
        _validate_id(owner_id)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                await conn.execute(
                    f"""
                    UPDATE {_TABLE}
                       SET lease_owner_id = NULL, lease_expires_at = NULL,
                           updated_at = now()
                     WHERE source = %s AND lease_owner_id = %s
                    """,
                    (_SOURCE, owner_id),
                )
        except Exception as exc:  # pragma: no cover - best-effort release
            raise ArtifactCleanupScheduleStateError() from exc


async def _ensure_state_row(conn: object) -> None:
    await conn.execute(
        f"""
        INSERT INTO {_TABLE} (
            source, cursor_after_org_id, lease_owner_id, lease_expires_at, updated_at
        ) VALUES (%s, NULL, NULL, NULL, now())
        ON CONFLICT (source) DO NOTHING
        """,
        (_SOURCE,),
    )


async def _locked_state_row(conn: object) -> Mapping[str, object]:
    cursor = await conn.execute(
        f"""
        SELECT cursor_after_org_id, lease_owner_id, lease_expires_at
          FROM {_TABLE}
         WHERE source = %s
         FOR UPDATE
        """,
        (_SOURCE,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ArtifactCleanupScheduleStateError()
    return row


def _cursor_from_row(row: Mapping[str, object]) -> str | None:
    value = row["cursor_after_org_id"]
    if value is None:
        return None
    _validate_id(value)
    return str(value)


def _validate_id(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler identifier is invalid"
        )


__all__ = ("PostgresArtifactCleanupScheduleStore",)
