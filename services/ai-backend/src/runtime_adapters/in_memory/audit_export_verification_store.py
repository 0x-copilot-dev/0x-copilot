"""In-memory semantic-parity store for audit-export verification sampling."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import RLock

from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportBundleManifest,
    AuditExportVerificationCursor,
    AuditExportVerificationRecord,
    AuditExportVerificationStateError,
)


class InMemoryAuditExportVerificationStore:
    """Thread-safe port implementation for tests and local development.

    It deliberately offers the same keyset/CAS/lease semantics as durable
    adapters while making no restart-durability claim.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._manifests: dict[tuple[str, str, str], AuditExportBundleManifest] = {}
        self._outcomes: dict[tuple[str, str, str], AuditExportVerificationRecord] = {}
        self._cursor: AuditExportVerificationCursor | None = None
        self._lease_owner: str | None = None
        self._lease_expires_at: datetime | None = None

    async def record_manifest(self, *, manifest: AuditExportBundleManifest) -> None:
        key = (manifest.org_id, manifest.bundle_ref, manifest.bundle_digest)
        with self._lock:
            existing = self._manifests.get(key)
            if existing is None:
                self._manifests[key] = manifest
                return
            if not existing.same_capture_as(manifest):
                raise AuditExportVerificationStateError()

    async def list_manifests_after(
        self,
        *,
        cursor: AuditExportVerificationCursor | None,
        limit: int,
    ) -> Sequence[AuditExportBundleManifest]:
        if not 1 <= limit <= 500:
            raise ValueError("audit export sample limit is invalid")
        with self._lock:
            rows = sorted(self._manifests.values(), key=_manifest_key)
            if cursor is not None:
                after = _cursor_key(cursor)
                rows = [row for row in rows if _manifest_key(row) > after]
            return tuple(rows[:limit])

    async def load_scan_cursor(self) -> AuditExportVerificationCursor | None:
        with self._lock:
            return self._cursor

    async def advance_scan_cursor(
        self,
        *,
        expected: AuditExportVerificationCursor | None,
        next_cursor: AuditExportVerificationCursor | None,
    ) -> bool:
        with self._lock:
            if self._cursor != expected:
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
        if expires_at <= now:
            raise ValueError("audit export verification lease must be positive")
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
        with self._lock:
            if self._lease_owner == owner_id:
                self._lease_owner = None
                self._lease_expires_at = None

    async def record_outcome(
        self, *, record: AuditExportVerificationRecord
    ) -> AuditExportVerificationRecord:
        key = (record.org_id, record.bundle_ref, record.bundle_digest)
        with self._lock:
            existing = self._outcomes.get(key)
            persisted = record.model_copy(
                update={"attempts": (existing.attempts if existing else 0) + 1}
            )
            self._outcomes[key] = persisted
            return persisted

    async def list_outcomes(
        self, *, org_id: str, bundle_ref: str
    ) -> Sequence[AuditExportVerificationRecord]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        record
                        for (
                            stored_org,
                            stored_ref,
                            _digest,
                        ), record in self._outcomes.items()
                        if stored_org == org_id and stored_ref == bundle_ref
                    ),
                    key=lambda record: record.bundle_digest,
                )
            )


def _manifest_key(
    manifest: AuditExportBundleManifest,
) -> tuple[datetime, str, str]:
    return (manifest.captured_at, manifest.org_id, manifest.bundle_ref)


def _cursor_key(
    cursor: AuditExportVerificationCursor,
) -> tuple[datetime, str, str]:
    return (cursor.after_captured_at, cursor.after_org_id, cursor.after_bundle_ref)


__all__ = ("InMemoryAuditExportVerificationStore",)
