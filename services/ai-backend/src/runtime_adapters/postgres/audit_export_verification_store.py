"""Postgres D7/D12 catalog, outcome, cursor, and lease adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from psycopg.types.json import Jsonb

from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportBundleManifest,
    AuditExportFormat,
    AuditExportVerificationCursor,
    AuditExportVerificationFailureClass,
    AuditExportVerificationOutcome,
    AuditExportVerificationRecord,
    AuditExportVerificationStateError,
)


_MANIFESTS_TABLE = "runtime_audit_export_verification_manifests"
_OUTCOMES_TABLE = "runtime_audit_export_verification_outcomes"
_SCAN_STATE_TABLE = "runtime_audit_export_verification_scan_state"
_SOURCE = "audit_export_verification"
_WORKER_ROLE = "worker"


class PostgresAuditExportVerificationStore:
    """Worker-owned safe metadata store over the canonical runtime pool."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def record_manifest(self, *, manifest: AuditExportBundleManifest) -> None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    cursor = await conn.execute(
                        f"""
                        INSERT INTO {_MANIFESTS_TABLE} (
                            org_id, bundle_ref, bundle_digest, run_id, format,
                            legacy_version_key, generated_at_wire, generated_at, captured_at, key_id, head_hash,
                            receipt_digest, rows_json
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (org_id, bundle_ref, bundle_digest) DO NOTHING
                        RETURNING org_id, bundle_ref, bundle_digest, run_id, format,
                                  legacy_version_key, generated_at_wire, generated_at, captured_at, key_id, head_hash,
                                  receipt_digest, rows_json
                        """,
                        _manifest_values(manifest),
                    )
                    if await cursor.fetchone() is not None:
                        return
                    existing = await _load_manifest(
                        conn,
                        org_id=manifest.org_id,
                        bundle_ref=manifest.bundle_ref,
                        bundle_digest=manifest.bundle_digest,
                        for_update=True,
                    )
                    if existing is None or not existing.same_capture_as(manifest):
                        raise AuditExportVerificationStateError()
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc

    async def list_manifests_after(
        self,
        *,
        cursor: AuditExportVerificationCursor | None,
        limit: int,
    ) -> Sequence[AuditExportBundleManifest]:
        if not 1 <= limit <= 500:
            raise ValueError("audit export sample limit is invalid")
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                if cursor is None:
                    db_cursor = await conn.execute(
                        f"""
                        SELECT org_id, bundle_ref, bundle_digest, run_id, format,
                               legacy_version_key, generated_at_wire, generated_at, captured_at, key_id, head_hash,
                               receipt_digest, rows_json
                          FROM {_MANIFESTS_TABLE}
                         ORDER BY captured_at ASC, org_id ASC, bundle_ref ASC
                         LIMIT %s
                        """,
                        (limit,),
                    )
                else:
                    db_cursor = await conn.execute(
                        f"""
                        SELECT org_id, bundle_ref, bundle_digest, run_id, format,
                               legacy_version_key, generated_at_wire, generated_at, captured_at, key_id, head_hash,
                               receipt_digest, rows_json
                          FROM {_MANIFESTS_TABLE}
                         WHERE (captured_at, org_id, bundle_ref) > (%s, %s, %s)
                         ORDER BY captured_at ASC, org_id ASC, bundle_ref ASC
                         LIMIT %s
                        """,
                        (
                            cursor.after_captured_at,
                            cursor.after_org_id,
                            cursor.after_bundle_ref,
                            limit,
                        ),
                    )
                rows = await db_cursor.fetchall()
            return tuple(_manifest_from_row(row) for row in rows)
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc

    async def load_scan_cursor(self) -> AuditExportVerificationCursor | None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT after_captured_at, after_org_id, after_bundle_ref
                      FROM {_SCAN_STATE_TABLE}
                     WHERE source = %s
                    """,
                    (_SOURCE,),
                )
                row = await cursor.fetchone()
            return _cursor_from_row(row) if row is not None else None
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc

    async def advance_scan_cursor(
        self,
        *,
        expected: AuditExportVerificationCursor | None,
        next_cursor: AuditExportVerificationCursor | None,
    ) -> bool:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                async with conn.transaction():
                    await _ensure_state_row(conn)
                    row = await _locked_state_row(conn)
                    if _cursor_from_row(row) != expected:
                        return False
                    await conn.execute(
                        f"""
                        UPDATE {_SCAN_STATE_TABLE}
                           SET after_captured_at = %s,
                               after_org_id = %s,
                               after_bundle_ref = %s,
                               updated_at = now()
                         WHERE source = %s
                        """,
                        (
                            (
                                next_cursor.after_captured_at
                                if next_cursor is not None
                                else None
                            ),
                            next_cursor.after_org_id
                            if next_cursor is not None
                            else None,
                            (
                                next_cursor.after_bundle_ref
                                if next_cursor is not None
                                else None
                            ),
                            _SOURCE,
                        ),
                    )
                    return True
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc

    async def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        if expires_at <= now:
            raise ValueError("audit export verification lease must be positive")
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
                        UPDATE {_SCAN_STATE_TABLE}
                           SET lease_owner_id = %s, lease_expires_at = %s,
                               updated_at = now()
                         WHERE source = %s
                        """,
                        (owner_id, expires_at, _SOURCE),
                    )
                    return True
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc

    async def release_lease(self, *, owner_id: str) -> None:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                await conn.execute(
                    f"""
                    UPDATE {_SCAN_STATE_TABLE}
                       SET lease_owner_id = NULL, lease_expires_at = NULL,
                           updated_at = now()
                     WHERE source = %s AND lease_owner_id = %s
                    """,
                    (_SOURCE, owner_id),
                )
        except Exception as exc:  # pragma: no cover - best-effort release
            raise AuditExportVerificationStateError() from exc

    async def record_outcome(
        self, *, record: AuditExportVerificationRecord
    ) -> AuditExportVerificationRecord:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    INSERT INTO {_OUTCOMES_TABLE} (
                        org_id, bundle_ref, bundle_digest, format, outcome,
                        failure_class, broken_at_seq, sampled_at, attempts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    ON CONFLICT (org_id, bundle_ref, bundle_digest) DO UPDATE
                    SET format = EXCLUDED.format,
                        outcome = EXCLUDED.outcome,
                        failure_class = EXCLUDED.failure_class,
                        broken_at_seq = EXCLUDED.broken_at_seq,
                        sampled_at = EXCLUDED.sampled_at,
                        attempts = {_OUTCOMES_TABLE}.attempts + 1
                    RETURNING org_id, bundle_ref, bundle_digest, format, outcome,
                              failure_class, broken_at_seq, sampled_at, attempts
                    """,
                    _outcome_values(record),
                )
                row = await cursor.fetchone()
            if row is None:
                raise AuditExportVerificationStateError()
            return _outcome_from_row(row)
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc

    async def list_outcomes(
        self, *, org_id: str, bundle_ref: str
    ) -> Sequence[AuditExportVerificationRecord]:
        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SLF001
                cursor = await conn.execute(
                    f"""
                    SELECT org_id, bundle_ref, bundle_digest, format, outcome,
                           failure_class, broken_at_seq, sampled_at, attempts
                      FROM {_OUTCOMES_TABLE}
                     WHERE org_id = %s AND bundle_ref = %s
                     ORDER BY bundle_digest ASC
                    """,
                    (org_id, bundle_ref),
                )
                rows = await cursor.fetchall()
            return tuple(_outcome_from_row(row) for row in rows)
        except AuditExportVerificationStateError:
            raise
        except Exception as exc:  # pragma: no cover - database driver failure
            raise AuditExportVerificationStateError() from exc


async def _ensure_state_row(conn: object) -> None:
    await conn.execute(
        f"""
        INSERT INTO {_SCAN_STATE_TABLE} (
            source, after_captured_at, after_org_id, after_bundle_ref,
            lease_owner_id, lease_expires_at, updated_at
        ) VALUES (%s, NULL, NULL, NULL, NULL, NULL, now())
        ON CONFLICT (source) DO NOTHING
        """,
        (_SOURCE,),
    )


async def _locked_state_row(conn: object) -> Mapping[str, object]:
    cursor = await conn.execute(
        f"""
        SELECT after_captured_at, after_org_id, after_bundle_ref,
               lease_owner_id, lease_expires_at
          FROM {_SCAN_STATE_TABLE}
         WHERE source = %s
         FOR UPDATE
        """,
        (_SOURCE,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise AuditExportVerificationStateError()
    return row


async def _load_manifest(
    conn: object,
    *,
    org_id: str,
    bundle_ref: str,
    bundle_digest: str,
    for_update: bool = False,
) -> AuditExportBundleManifest | None:
    lock = " FOR UPDATE" if for_update else ""
    cursor = await conn.execute(
        f"""
        SELECT org_id, bundle_ref, bundle_digest, run_id, format,
               legacy_version_key, generated_at_wire, generated_at, captured_at, key_id, head_hash, receipt_digest,
               rows_json
          FROM {_MANIFESTS_TABLE}
         WHERE org_id = %s AND bundle_ref = %s AND bundle_digest = %s{lock}
        """,
        (org_id, bundle_ref, bundle_digest),
    )
    row = await cursor.fetchone()
    return _manifest_from_row(row) if row is not None else None


def _manifest_values(manifest: AuditExportBundleManifest) -> tuple[object, ...]:
    return (
        manifest.org_id,
        manifest.bundle_ref,
        manifest.bundle_digest,
        manifest.run_id,
        manifest.format.value,
        manifest.legacy_version_key,
        manifest.generated_at,
        _parse_time(manifest.generated_at),
        manifest.captured_at,
        manifest.key_id,
        manifest.head_hash,
        manifest.receipt_digest,
        Jsonb([row.model_dump(mode="json") for row in manifest.rows]),
    )


def _outcome_values(record: AuditExportVerificationRecord) -> tuple[object, ...]:
    return (
        record.org_id,
        record.bundle_ref,
        record.bundle_digest,
        record.format.value,
        record.outcome.value,
        record.failure_class.value,
        record.broken_at_seq,
        record.sampled_at,
    )


def _manifest_from_row(row: Mapping[str, object]) -> AuditExportBundleManifest:
    try:
        raw_rows = row["rows_json"]
        legacy_version_key = row["legacy_version_key"]
        generated_at_wire = row["generated_at_wire"]
        generated_at = row["generated_at"]
        captured_at = row["captured_at"]
        if (
            not isinstance(raw_rows, list)
            or not isinstance(generated_at_wire, str)
            or not isinstance(generated_at, datetime)
            or not isinstance(captured_at, datetime)
        ):
            raise ValueError
        return AuditExportBundleManifest(
            bundle_ref=str(row["bundle_ref"]),
            org_id=str(row["org_id"]),
            run_id=str(row["run_id"]),
            format=AuditExportFormat(str(row["format"])),
            bundle_digest=str(row["bundle_digest"]),
            generated_at=generated_at_wire,
            captured_at=captured_at,
            key_id=str(row["key_id"]) if row["key_id"] is not None else None,
            legacy_version_key=(
                str(legacy_version_key) if legacy_version_key is not None else None
            ),
            head_hash=str(row["head_hash"]),
            receipt_digest=(
                str(row["receipt_digest"])
                if row["receipt_digest"] is not None
                else None
            ),
            rows=tuple(raw_rows),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditExportVerificationStateError() from exc


def _outcome_from_row(row: Mapping[str, object]) -> AuditExportVerificationRecord:
    try:
        sampled_at = row["sampled_at"]
        if not isinstance(sampled_at, datetime):
            raise ValueError
        return AuditExportVerificationRecord(
            org_id=str(row["org_id"]),
            bundle_ref=str(row["bundle_ref"]),
            bundle_digest=str(row["bundle_digest"]),
            format=AuditExportFormat(str(row["format"])),
            outcome=AuditExportVerificationOutcome(str(row["outcome"])),
            failure_class=AuditExportVerificationFailureClass(
                str(row["failure_class"])
            ),
            broken_at_seq=(
                int(row["broken_at_seq"]) if row["broken_at_seq"] is not None else None
            ),
            sampled_at=sampled_at,
            attempts=int(row["attempts"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditExportVerificationStateError() from exc


def _cursor_from_row(
    row: Mapping[str, object],
) -> AuditExportVerificationCursor | None:
    try:
        values = (
            row["after_captured_at"],
            row["after_org_id"],
            row["after_bundle_ref"],
        )
        if all(value is None for value in values):
            return None
        if any(value is None for value in values) or not isinstance(
            values[0], datetime
        ):
            raise ValueError
        return AuditExportVerificationCursor(
            after_captured_at=values[0],
            after_org_id=str(values[1]),
            after_bundle_ref=str(values[2]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditExportVerificationStateError() from exc


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuditExportVerificationStateError() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditExportVerificationStateError()
    return parsed


__all__ = ("PostgresAuditExportVerificationStore",)
