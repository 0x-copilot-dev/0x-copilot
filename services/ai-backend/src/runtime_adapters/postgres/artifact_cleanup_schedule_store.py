"""Postgres cursor, retry, and fenced-lease adapter for artifact cleanup."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import logging
from typing import Any
from uuid import uuid4

import psycopg

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupDeferredTenant,
    ArtifactCleanupLease,
    ArtifactCleanupScheduleStateError,
    ArtifactCleanupTenantExecutionLease,
    ArtifactCleanupTrackedExecution,
)


_TABLE = "runtime_artifact_cleanup_schedule_state"
_DEFERRED_TABLE = "runtime_artifact_cleanup_deferred_tenants"
_EXECUTIONS_TABLE = "runtime_artifact_cleanup_tenant_executions"
_SOURCE = "artifact_cleanup_execution"
_WORKER_ROLE = "worker"
_LOGGER = logging.getLogger(__name__)


class PostgresArtifactCleanupScheduleStore:
    """Worker-owned global cursor/retry state with DB-time fenced leases."""

    def __init__(self, *, store: object) -> None:
        self._store = store
        self._tenant_execution_handles: dict[
            str, tuple[ArtifactCleanupTenantExecutionLease, Any]
        ] = {}

    async def load_cursor(self) -> str | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"SELECT cursor_after_org_id FROM {_TABLE} WHERE source = %s",
                    (_SOURCE,),
                )
                row = await cursor.fetchone()
            return _cursor_from_row(row) if row is not None else None
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def load_deferred_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        org_id: str,
        now: datetime,
    ) -> ArtifactCleanupDeferredTenant | None:
        del now  # PostgreSQL lease validity is always judged by clock_timestamp().
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_fence(fence_token)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                active = await conn.execute(
                    f"""
                    SELECT 1
                      FROM {_TABLE}
                     WHERE source = %s
                       AND lease_owner_id = %s
                       AND lease_fence_token = %s
                       AND lease_expires_at > clock_timestamp()
                    """,
                    (_SOURCE, owner_id, fence_token),
                )
                if await active.fetchone() is None:
                    raise ArtifactCleanupScheduleStateError(
                        "cleanup scheduler lease is stale"
                    )
                cursor = await conn.execute(
                    f"""
                    SELECT org_id, failure_count, retry_not_before, last_failed_at
                      FROM {_DEFERRED_TABLE}
                     WHERE source = %s
                       AND org_id = %s
                       AND retry_not_before > clock_timestamp()
                    """,
                    (_SOURCE, org_id),
                )
                row = await cursor.fetchone()
            return _deferred_from_row(row) if row is not None else None
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def complete_tenant(
        self,
        *,
        owner_id: str,
        fence_token: int,
        expected_cursor: str | None,
        org_id: str,
        now: datetime,
    ) -> bool:
        del now  # PostgreSQL lease validity is always judged by clock_timestamp().
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_fence(fence_token)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await _ensure_state_row(conn)
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET cursor_after_org_id = %s, updated_at = clock_timestamp()
                         WHERE source = %s
                           AND cursor_after_org_id IS NOT DISTINCT FROM %s
                           AND lease_owner_id = %s
                           AND lease_fence_token = %s
                           AND lease_expires_at > clock_timestamp()
                        RETURNING source
                        """,
                        (
                            org_id,
                            _SOURCE,
                            expected_cursor,
                            owner_id,
                            fence_token,
                        ),
                    )
                    if await cursor.fetchone() is None:
                        return False
                    await conn.execute(
                        f"DELETE FROM {_DEFERRED_TABLE} WHERE source = %s AND org_id = %s",
                        (_SOURCE, org_id),
                    )
                    return True
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

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
        del now  # PostgreSQL retry timestamps are authoritative DB timestamps.
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_fence(fence_token)
        _validate_retry(retry_base_seconds, retry_max_seconds)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await _ensure_state_row(conn)
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET cursor_after_org_id = %s, updated_at = clock_timestamp()
                         WHERE source = %s
                           AND cursor_after_org_id IS NOT DISTINCT FROM %s
                           AND lease_owner_id = %s
                           AND lease_fence_token = %s
                           AND lease_expires_at > clock_timestamp()
                        RETURNING source
                        """,
                        (
                            org_id,
                            _SOURCE,
                            expected_cursor,
                            owner_id,
                            fence_token,
                        ),
                    )
                    if await cursor.fetchone() is None:
                        return None
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_DEFERRED_TABLE} (
                            source, org_id, failure_count, retry_not_before,
                            last_failed_at, updated_at
                        ) VALUES (
                            %s, %s, 1,
                            clock_timestamp() + make_interval(secs => LEAST(%s, %s)),
                            clock_timestamp(), clock_timestamp()
                        )
                        ON CONFLICT (source, org_id) DO UPDATE
                           SET failure_count = {_DEFERRED_TABLE}.failure_count + 1,
                               retry_not_before = clock_timestamp() + make_interval(
                                   secs => LEAST(
                                       %s * power(
                                           2::double precision,
                                           LEAST({_DEFERRED_TABLE}.failure_count, 20)
                                       ),
                                       %s
                                   )
                               ),
                               last_failed_at = clock_timestamp(),
                               updated_at = clock_timestamp()
                        RETURNING org_id, failure_count, retry_not_before, last_failed_at
                        """,
                        (
                            _SOURCE,
                            org_id,
                            retry_base_seconds,
                            retry_max_seconds,
                            retry_base_seconds,
                            retry_max_seconds,
                        ),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise ArtifactCleanupScheduleStateError()
                    return _deferred_from_row(row)
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        duration_seconds: float,
    ) -> ArtifactCleanupLease | None:
        del now  # Never trust a worker clock for PostgreSQL lease ownership.
        _validate_id(owner_id)
        _validate_duration(duration_seconds)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await _ensure_state_row(conn)
                    row = await _locked_state_row(conn)
                    if _row_lease_active(row):
                        return None
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET lease_owner_id = %s,
                               lease_fence_token = lease_fence_token + 1,
                               lease_expires_at = clock_timestamp() + make_interval(secs => %s),
                               updated_at = clock_timestamp()
                         WHERE source = %s
                        RETURNING lease_owner_id, lease_fence_token, lease_expires_at
                        """,
                        (owner_id, duration_seconds, _SOURCE),
                    )
                    updated = await cursor.fetchone()
                    if updated is None:
                        raise ArtifactCleanupScheduleStateError()
                    return _lease_from_row(updated)
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def renew_lease(
        self,
        *,
        owner_id: str,
        fence_token: int,
        now: datetime,
        duration_seconds: float,
    ) -> ArtifactCleanupLease | None:
        del now  # Never trust a worker clock for PostgreSQL lease ownership.
        _validate_id(owner_id)
        _validate_fence(fence_token)
        _validate_duration(duration_seconds)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    UPDATE {_TABLE}
                       SET lease_expires_at = clock_timestamp() + make_interval(secs => %s),
                           updated_at = clock_timestamp()
                     WHERE source = %s
                       AND lease_owner_id = %s
                       AND lease_fence_token = %s
                       AND lease_expires_at > clock_timestamp()
                    RETURNING lease_owner_id, lease_fence_token, lease_expires_at
                    """,
                    (duration_seconds, _SOURCE, owner_id, fence_token),
                )
                row = await cursor.fetchone()
            return _lease_from_row(row) if row is not None else None
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def release_lease(
        self, *, owner_id: str, fence_token: int, now: datetime
    ) -> None:
        del now  # PostgreSQL release is also governed by clock_timestamp().
        _validate_id(owner_id)
        _validate_fence(fence_token)
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                await conn.execute(
                    f"""
                    UPDATE {_TABLE}
                       SET lease_owner_id = NULL, lease_expires_at = NULL,
                           updated_at = clock_timestamp()
                     WHERE source = %s
                       AND lease_owner_id = %s
                       AND lease_fence_token = %s
                       AND lease_expires_at > clock_timestamp()
                    """,
                    (_SOURCE, owner_id, fence_token),
                )
        except Exception as exc:  # pragma: no cover - best-effort release
            raise ArtifactCleanupScheduleStateError() from exc

    async def acquire_tenant_execution(
        self,
        *,
        owner_id: str,
        fence_token: int,
        org_id: str,
        now: datetime,
        maximum_active_executions: int = 4,
    ) -> ArtifactCleanupTenantExecutionLease | None:
        """Hold a server-owned tenant advisory lock across the lifecycle pass.

        The connection is deliberately dedicated rather than borrowed from the
        runtime pool: the pass itself uses that pool for its existing
        transactional legal-hold/reference fences, so retaining a pool slot
        here could deadlock a single-slot deployment. PostgreSQL releases this
        session lock automatically if a stalled worker process dies.
        """

        del now  # PostgreSQL eligibility is always determined by DB time.
        _validate_id(owner_id)
        _validate_id(org_id)
        _validate_fence(fence_token)
        _validate_maximum_active_executions(maximum_active_executions)
        connection = await self._open_execution_connection()
        key = _tenant_execution_advisory_lock_key(org_id)
        advisory_acquired = False
        try:
            cursor = await connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (key,)
            )
            row = await cursor.fetchone()
            if not _row_bool(row, "acquired"):
                await connection.close()
                return None
            advisory_acquired = True
            execution = ArtifactCleanupTenantExecutionLease(
                org_id=org_id,
                owner_id=owner_id,
                fence_token=fence_token,
                execution_token=uuid4().hex,
            )
            reserved = False
            async with connection.transaction():
                if await _global_fence_active(
                    connection,
                    owner_id=owner_id,
                    fence_token=fence_token,
                ):
                    count_cursor = await connection.execute(
                        f"SELECT count(*) AS count FROM {_EXECUTIONS_TABLE} WHERE source = %s",
                        (_SOURCE,),
                    )
                    count_row = await count_cursor.fetchone()
                    if _row_int(count_row, "count") < maximum_active_executions:
                        inserted = await connection.execute(
                            f"""
                            INSERT INTO {_EXECUTIONS_TABLE} (
                                source, execution_token, org_id, owner_id,
                                lease_fence_token, state, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, 'active', clock_timestamp())
                            ON CONFLICT (source, org_id) DO NOTHING
                            RETURNING execution_token
                            """,
                            (
                                _SOURCE,
                                execution.execution_token,
                                execution.org_id,
                                execution.owner_id,
                                execution.fence_token,
                            ),
                        )
                        reserved = await inserted.fetchone() is not None
            if not reserved:
                await connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
                advisory_acquired = False
                await connection.close()
                return None
            self._tenant_execution_handles[execution.execution_token] = (
                execution,
                connection,
            )
            return execution
        except Exception as exc:
            try:
                if advisory_acquired:
                    await connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
            finally:
                await connection.close()
            raise ArtifactCleanupScheduleStateError() from exc

    async def validate_tenant_execution(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> bool:
        del now  # PostgreSQL eligibility is always determined by DB time.
        handle = self._tenant_execution_handles.get(execution.execution_token)
        if handle is None or handle[0] != execution:
            return False
        try:
            return await _global_fence_active(
                handle[1],
                owner_id=execution.owner_id,
                fence_token=execution.fence_token,
            )
        except Exception:
            return False

    async def release_tenant_execution(
        self, *, execution: ArtifactCleanupTenantExecutionLease
    ) -> bool:
        handle = self._tenant_execution_handles.get(execution.execution_token)
        if handle is None or handle[0] != execution:
            return False
        connection = handle[1]
        try:
            cursor = await connection.execute(
                "SELECT pg_advisory_unlock(%s)",
                (_tenant_execution_advisory_lock_key(execution.org_id),),
            )
            row = await cursor.fetchone()
            if not _row_bool(row, "pg_advisory_unlock"):
                await connection.close()
                self._tenant_execution_handles.pop(execution.execution_token, None)
                return False
            await connection.close()
            # The physical fence is conclusively gone. A metadata-delete
            # failure below remains capacity-consuming, but is now safely
            # reclaimable by the durable orphan reconciler.
            self._tenant_execution_handles.pop(execution.execution_token, None)
        except Exception as exc:
            # Keep the exact live connection in the local handle map.  The
            # caller will durably mark release-pending and retry it; dropping
            # this handle could permit a same-tenant overlap.
            raise ArtifactCleanupScheduleStateError() from exc
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    DELETE FROM {_EXECUTIONS_TABLE}
                     WHERE source = %s
                       AND execution_token = %s
                       AND org_id = %s
                       AND owner_id = %s
                       AND lease_fence_token = %s
                    RETURNING execution_token
                    """,
                    (
                        _SOURCE,
                        execution.execution_token,
                        execution.org_id,
                        execution.owner_id,
                        execution.fence_token,
                    ),
                )
                removed = await cursor.fetchone() is not None
        except Exception as exc:
            # The advisory lock is gone, but the durable row deliberately
            # remains capacity-consuming until a retry/reconciliation confirms
            # cleanup. The now-closed handle was removed above so the orphan
            # reconciler can safely prove the advisory lock is absent.
            raise ArtifactCleanupScheduleStateError() from exc
        if removed:
            return True
        return False

    async def mark_tenant_execution_quarantined(
        self, *, execution: ArtifactCleanupTenantExecutionLease, now: datetime
    ) -> bool:
        del now
        return (
            await self._update_execution_state(
                execution=execution,
                state="quarantined",
                retry_base_seconds=None,
                retry_max_seconds=None,
            )
            is not None
        )

    async def mark_tenant_execution_release_pending(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> ArtifactCleanupTrackedExecution | None:
        del now
        _validate_retry(retry_base_seconds, retry_max_seconds)
        return await self._update_execution_state(
            execution=execution,
            state="release_pending",
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )

    async def load_tenant_execution_release_pending(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        now: datetime,
    ) -> ArtifactCleanupTrackedExecution | None:
        del now
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT execution_token, org_id, owner_id, lease_fence_token,
                           state, release_failure_count, retry_not_before
                      FROM {_EXECUTIONS_TABLE}
                     WHERE source = %s
                       AND execution_token = %s
                       AND org_id = %s
                       AND owner_id = %s
                       AND lease_fence_token = %s
                       AND state = 'release_pending'
                       AND retry_not_before <= clock_timestamp()
                    """,
                    (
                        _SOURCE,
                        execution.execution_token,
                        execution.org_id,
                        execution.owner_id,
                        execution.fence_token,
                    ),
                )
                row = await cursor.fetchone()
            return _tracked_execution_from_row(row) if row is not None else None
        except Exception as exc:  # pragma: no cover - driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def list_tracked_tenant_executions(
        self,
    ) -> tuple[ArtifactCleanupTrackedExecution, ...]:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT execution_token, org_id, owner_id, lease_fence_token,
                           state, release_failure_count, retry_not_before
                      FROM {_EXECUTIONS_TABLE}
                     WHERE source = %s
                     ORDER BY execution_token ASC
                    """,
                    (_SOURCE,),
                )
                rows = await cursor.fetchall()
            return tuple(_tracked_execution_from_row(row) for row in rows)
        except Exception as exc:  # pragma: no cover - driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def reconcile_orphaned_tenant_executions(self) -> int:
        tracked = await self.list_tracked_tenant_executions()
        reclaimed = 0
        for value in tracked:
            if value.execution.execution_token in self._tenant_execution_handles:
                continue
            connection = await self._open_execution_connection()
            key = _tenant_execution_advisory_lock_key(value.execution.org_id)
            acquired = False
            try:
                cursor = await connection.execute(
                    "SELECT pg_try_advisory_lock(%s) AS acquired", (key,)
                )
                if not _row_bool(await cursor.fetchone(), "acquired"):
                    continue
                acquired = True
                async with connection.transaction():
                    cursor = await connection.execute(
                        f"""
                        DELETE FROM {_EXECUTIONS_TABLE}
                         WHERE source = %s
                           AND execution_token = %s
                           AND org_id = %s
                           AND owner_id = %s
                           AND lease_fence_token = %s
                        RETURNING execution_token
                        """,
                        (
                            _SOURCE,
                            value.execution.execution_token,
                            value.execution.org_id,
                            value.execution.owner_id,
                            value.execution.fence_token,
                        ),
                    )
                    reclaimed += int(await cursor.fetchone() is not None)
            except Exception:
                _LOGGER.warning("artifact_cleanup_execution_reconcile_unavailable")
            finally:
                try:
                    if acquired:
                        await connection.execute(
                            "SELECT pg_advisory_unlock(%s)", (key,)
                        )
                finally:
                    await connection.close()
        return reclaimed

    async def _update_execution_state(
        self,
        *,
        execution: ArtifactCleanupTenantExecutionLease,
        state: str,
        retry_base_seconds: float | None,
        retry_max_seconds: float | None,
    ) -> ArtifactCleanupTrackedExecution | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                if state == "quarantined":
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_EXECUTIONS_TABLE}
                           SET state = 'quarantined', retry_not_before = NULL,
                               updated_at = clock_timestamp()
                         WHERE source = %s AND execution_token = %s AND org_id = %s
                           AND owner_id = %s AND lease_fence_token = %s
                        RETURNING execution_token, org_id, owner_id, lease_fence_token,
                                  state, release_failure_count, retry_not_before
                        """,
                        _execution_identity_params(execution),
                    )
                else:
                    if retry_base_seconds is None or retry_max_seconds is None:
                        raise ArtifactCleanupScheduleStateError()
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_EXECUTIONS_TABLE}
                           SET state = 'release_pending',
                               release_failure_count = release_failure_count + 1,
                               retry_not_before = clock_timestamp() + make_interval(
                                   secs => LEAST(
                                       %s * power(2::double precision,
                                           LEAST(release_failure_count, 20)),
                                       %s
                                   )
                               ),
                               updated_at = clock_timestamp()
                         WHERE source = %s AND execution_token = %s AND org_id = %s
                           AND owner_id = %s AND lease_fence_token = %s
                        RETURNING execution_token, org_id, owner_id, lease_fence_token,
                                  state, release_failure_count, retry_not_before
                        """,
                        (
                            retry_base_seconds,
                            retry_max_seconds,
                            *_execution_identity_params(execution),
                        ),
                    )
                row = await cursor.fetchone()
            return _tracked_execution_from_row(row) if row is not None else None
        except ArtifactCleanupScheduleStateError:
            raise
        except Exception as exc:  # pragma: no cover - driver failure
            raise ArtifactCleanupScheduleStateError() from exc

    async def _open_execution_connection(self) -> Any:
        database_url = getattr(self._store, "database_url", None)
        if not isinstance(database_url, str) or not database_url:
            raise ArtifactCleanupScheduleStateError(
                "Postgres cleanup execution fencing requires a database URL"
            )
        try:
            connection = await psycopg.AsyncConnection.connect(
                database_url,
                autocommit=True,
            )
            await connection.execute(
                "SELECT set_config('app.role', %s, false)",
                (_WORKER_ROLE,),
            )
            return connection
        except Exception as exc:  # pragma: no cover - database driver failure
            raise ArtifactCleanupScheduleStateError() from exc


async def _ensure_state_row(conn: object) -> None:
    await conn.execute(
        f"""
        INSERT INTO {_TABLE} (
            source, cursor_after_org_id, lease_owner_id, lease_fence_token,
            lease_expires_at, updated_at
        ) VALUES (%s, NULL, NULL, 0, NULL, clock_timestamp())
        ON CONFLICT (source) DO NOTHING
        """,
        (_SOURCE,),
    )


async def _locked_state_row(conn: object) -> Mapping[str, object]:
    cursor = await conn.execute(
        f"""
        SELECT cursor_after_org_id, lease_owner_id, lease_fence_token,
               lease_expires_at, clock_timestamp() AS db_now
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


async def _global_fence_active(
    conn: object, *, owner_id: str, fence_token: int
) -> bool:
    cursor = await conn.execute(
        f"""
        SELECT 1
          FROM {_TABLE}
         WHERE source = %s
           AND lease_owner_id = %s
           AND lease_fence_token = %s
           AND lease_expires_at > clock_timestamp()
        """,
        (_SOURCE, owner_id, fence_token),
    )
    return await cursor.fetchone() is not None


def _tenant_execution_advisory_lock_key(org_id: str) -> int:
    raw = hashlib.sha256(
        f"artifact-cleanup-execution:{_SOURCE}:{org_id}".encode("utf-8")
    ).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def _row_bool(row: object, key: str) -> bool:
    if isinstance(row, Mapping):
        return bool(row.get(key))
    if isinstance(row, tuple) and row:
        return bool(row[0])
    return False


def _row_int(row: object, key: str) -> int:
    value: object | None
    if isinstance(row, Mapping):
        value = row.get(key)
    elif isinstance(row, tuple) and row:
        value = row[0]
    else:
        value = None
    if not isinstance(value, int) or value < 0:
        raise ArtifactCleanupScheduleStateError()
    return value


def _row_lease_active(row: Mapping[str, object]) -> bool:
    expires = row["lease_expires_at"]
    now = row["db_now"]
    return isinstance(expires, datetime) and isinstance(now, datetime) and expires > now


def _cursor_from_row(row: Mapping[str, object]) -> str | None:
    value = row["cursor_after_org_id"]
    if value is None:
        return None
    _validate_id(value)
    return str(value)


def _lease_from_row(row: Mapping[str, object]) -> ArtifactCleanupLease:
    owner = row["lease_owner_id"]
    token = row["lease_fence_token"]
    expires_at = row["lease_expires_at"]
    if (
        not isinstance(owner, str)
        or not isinstance(token, int)
        or not isinstance(expires_at, datetime)
    ):
        raise ArtifactCleanupScheduleStateError()
    _validate_id(owner)
    _validate_fence(token)
    return ArtifactCleanupLease(
        owner_id=owner,
        fence_token=token,
        expires_at=expires_at,
    )


def _deferred_from_row(row: Mapping[str, object]) -> ArtifactCleanupDeferredTenant:
    org_id = row["org_id"]
    failure_count = row["failure_count"]
    retry_not_before = row["retry_not_before"]
    last_failed_at = row["last_failed_at"]
    if (
        not isinstance(org_id, str)
        or not isinstance(failure_count, int)
        or not isinstance(retry_not_before, datetime)
        or not isinstance(last_failed_at, datetime)
    ):
        raise ArtifactCleanupScheduleStateError()
    _validate_id(org_id)
    if failure_count < 1:
        raise ArtifactCleanupScheduleStateError()
    return ArtifactCleanupDeferredTenant(
        org_id=org_id,
        failure_count=failure_count,
        retry_not_before=retry_not_before,
        last_failed_at=last_failed_at,
    )


def _tracked_execution_from_row(
    row: Mapping[str, object],
) -> ArtifactCleanupTrackedExecution:
    token = row["execution_token"]
    org_id = row["org_id"]
    owner_id = row["owner_id"]
    fence_token = row["lease_fence_token"]
    state = row["state"]
    failures = row["release_failure_count"]
    retry_not_before = row["retry_not_before"]
    if (
        not isinstance(token, str)
        or not isinstance(org_id, str)
        or not isinstance(owner_id, str)
        or not isinstance(fence_token, int)
        or state not in {"active", "quarantined", "release_pending"}
        or not isinstance(failures, int)
        or failures < 0
        or (retry_not_before is not None and not isinstance(retry_not_before, datetime))
    ):
        raise ArtifactCleanupScheduleStateError()
    _validate_id(token)
    _validate_id(org_id)
    _validate_id(owner_id)
    _validate_fence(fence_token)
    if state == "release_pending" and retry_not_before is None:
        raise ArtifactCleanupScheduleStateError()
    if state != "release_pending" and retry_not_before is not None:
        raise ArtifactCleanupScheduleStateError()
    return ArtifactCleanupTrackedExecution(
        execution=ArtifactCleanupTenantExecutionLease(
            org_id=org_id,
            owner_id=owner_id,
            fence_token=fence_token,
            execution_token=token,
        ),
        state=state,
        release_failure_count=failures,
        retry_not_before=retry_not_before,
    )


def _execution_identity_params(
    execution: ArtifactCleanupTenantExecutionLease,
) -> tuple[str, str, str, str, int]:
    return (
        _SOURCE,
        execution.execution_token,
        execution.org_id,
        execution.owner_id,
        execution.fence_token,
    )


def _validate_id(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler identifier is invalid"
        )


def _validate_fence(value: int) -> None:
    if not isinstance(value, int) or value < 1:
        raise ArtifactCleanupScheduleStateError("cleanup scheduler fence is invalid")


def _validate_duration(value: float) -> None:
    if value <= 0:
        raise ArtifactCleanupScheduleStateError("cleanup scheduler duration is invalid")


def _validate_maximum_active_executions(value: int) -> None:
    if not isinstance(value, int) or not 1 <= value <= 64:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler execution capacity is invalid"
        )


def _validate_retry(base_seconds: float, max_seconds: float) -> None:
    if base_seconds <= 0 or max_seconds < base_seconds:
        raise ArtifactCleanupScheduleStateError(
            "cleanup scheduler retry bounds are invalid"
        )


__all__ = ("PostgresArtifactCleanupScheduleStore",)
