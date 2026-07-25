"""Atomic file-backed D7/D12 audit-export verification state.

The file contains safe manifests and outcomes only.  Legacy raw payloads are
never written: v1 manifests carry row digests/signature envelopes and are
rehydrated by the worker from the authoritative event stream.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Iterator
from uuid import uuid4

from pydantic import ValidationError

from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportBundleManifest,
    AuditExportVerificationCursor,
    AuditExportVerificationRecord,
    AuditExportVerificationStateError,
)
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileAuditExportVerificationStore:
    """Restart-safe single-writer store with CAS cursor and time-bound lease."""

    _SUBDIR: ClassVar[str] = "audit_export_verification"
    _STATE_FILENAME: ClassVar[str] = "state.json"
    _LOCK_FILENAME: ClassVar[str] = ".audit-export-verification.lock"
    _DIR_MODE: ClassVar[int] = 0o700
    _FILE_MODE: ClassVar[int] = 0o600

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._path = self._dir / self._STATE_FILENAME
        self._lock_path = self._dir / self._LOCK_FILENAME
        self._lock = asyncio.Lock()

    async def record_manifest(self, *, manifest: AuditExportBundleManifest) -> None:
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                key = _manifest_identity(manifest)
                existing = {
                    _manifest_identity(row): row for row in state["manifests"]
                }.get(key)
                if existing is not None:
                    if not existing.same_capture_as(manifest):
                        raise AuditExportVerificationStateError()
                    return
                state["manifests"].append(manifest)
                state["manifests"].sort(key=_manifest_key)
                self._write(state)

    async def list_manifests_after(
        self,
        *,
        cursor: AuditExportVerificationCursor | None,
        limit: int,
    ) -> Sequence[AuditExportBundleManifest]:
        if not 1 <= limit <= 500:
            raise ValueError("audit export sample limit is invalid")
        async with self._lock:
            with self._exclusive_lock():
                rows = self._read()["manifests"]
                if cursor is not None:
                    after = _cursor_key(cursor)
                    rows = [row for row in rows if _manifest_key(row) > after]
                return tuple(rows[:limit])

    async def load_scan_cursor(self) -> AuditExportVerificationCursor | None:
        async with self._lock:
            with self._exclusive_lock():
                return self._read()["cursor"]

    async def advance_scan_cursor(
        self,
        *,
        expected: AuditExportVerificationCursor | None,
        next_cursor: AuditExportVerificationCursor | None,
    ) -> bool:
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if state["cursor"] != expected:
                    return False
                state["cursor"] = next_cursor
                self._write(state)
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
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                lease_owner = state["lease_owner"]
                lease_expires_at = state["lease_expires_at"]
                if (
                    lease_owner is not None
                    and lease_owner != owner_id
                    and lease_expires_at is not None
                    and lease_expires_at > now
                ):
                    return False
                state["lease_owner"] = owner_id
                state["lease_expires_at"] = expires_at
                self._write(state)
                return True

    async def release_lease(self, *, owner_id: str) -> None:
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if state["lease_owner"] != owner_id:
                    return
                state["lease_owner"] = None
                state["lease_expires_at"] = None
                self._write(state)

    async def record_outcome(
        self, *, record: AuditExportVerificationRecord
    ) -> AuditExportVerificationRecord:
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                key = _outcome_identity(record)
                indexed = {_outcome_identity(row): row for row in state["outcomes"]}
                existing = indexed.get(key)
                persisted = record.model_copy(
                    update={"attempts": (existing.attempts if existing else 0) + 1}
                )
                indexed[key] = persisted
                state["outcomes"] = sorted(indexed.values(), key=_outcome_identity)
                self._write(state)
                return persisted

    async def list_outcomes(
        self, *, org_id: str, bundle_ref: str
    ) -> Sequence[AuditExportVerificationRecord]:
        async with self._lock:
            with self._exclusive_lock():
                return tuple(
                    row
                    for row in self._read()["outcomes"]
                    if row.org_id == org_id and row.bundle_ref == bundle_ref
                )

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {
                "manifests": [],
                "outcomes": [],
                "cursor": None,
                "lease_owner": None,
                "lease_expires_at": None,
            }
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "manifests",
                "outcomes",
                "cursor",
                "lease_owner",
                "lease_expires_at",
            }:
                raise ValueError
            raw_manifests = raw["manifests"]
            raw_outcomes = raw["outcomes"]
            if not isinstance(raw_manifests, list) or not isinstance(
                raw_outcomes, list
            ):
                raise ValueError
            manifests = [
                AuditExportBundleManifest.model_validate(item) for item in raw_manifests
            ]
            outcomes = [
                AuditExportVerificationRecord.model_validate(item)
                for item in raw_outcomes
            ]
            cursor = (
                None
                if raw["cursor"] is None
                else AuditExportVerificationCursor.model_validate(raw["cursor"])
            )
            owner = raw["lease_owner"]
            lease_expires_at = raw["lease_expires_at"]
            if owner is not None and not isinstance(owner, str):
                raise ValueError
            if lease_expires_at is not None:
                lease_expires_at = datetime.fromisoformat(str(lease_expires_at))
                if lease_expires_at.tzinfo is None:
                    raise ValueError
            if (
                [_manifest_key(row) for row in manifests]
                != sorted(_manifest_key(row) for row in manifests)
                or len({_manifest_identity(row) for row in manifests}) != len(manifests)
                or len({_outcome_identity(row) for row in outcomes}) != len(outcomes)
            ):
                raise ValueError
            return {
                "manifests": manifests,
                "outcomes": outcomes,
                "cursor": cursor,
                "lease_owner": owner,
                "lease_expires_at": lease_expires_at,
            }
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise AuditExportVerificationStateError() from exc

    def _write(self, state: dict[str, Any]) -> None:
        payload = {
            "manifests": [row.model_dump(mode="json") for row in state["manifests"]],
            "outcomes": [row.model_dump(mode="json") for row in state["outcomes"]],
            "cursor": (
                state["cursor"].model_dump(mode="json")
                if state["cursor"] is not None
                else None
            ),
            "lease_owner": state["lease_owner"],
            "lease_expires_at": (
                state["lease_expires_at"].isoformat()
                if state["lease_expires_at"] is not None
                else None
            ),
        }
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            try:
                self._path.chmod(self._FILE_MODE)
            except OSError:
                pass
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise AuditExportVerificationStateError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        descriptor: int | None = None
        acquired = False
        try:
            descriptor = os.open(
                self._lock_path,
                os.O_CREAT | os.O_RDWR,
                self._FILE_MODE,
            )
            acquire_exclusive(descriptor)
            acquired = True
            yield
        finally:
            if descriptor is not None:
                try:
                    if acquired:
                        release_exclusive(descriptor)
                finally:
                    os.close(descriptor)

    def _sync_directory(self) -> None:
        try:
            descriptor = os.open(self._dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


def _manifest_identity(
    row: AuditExportBundleManifest,
) -> tuple[str, str, str]:
    return (row.org_id, row.bundle_ref, row.bundle_digest)


def _outcome_identity(
    row: AuditExportVerificationRecord,
) -> tuple[str, str, str]:
    return (row.org_id, row.bundle_ref, row.bundle_digest)


def _manifest_key(
    row: AuditExportBundleManifest,
) -> tuple[datetime, str, str]:
    return (row.captured_at, row.org_id, row.bundle_ref)


def _cursor_key(
    row: AuditExportVerificationCursor,
) -> tuple[datetime, str, str]:
    return (row.after_captured_at, row.after_org_id, row.after_bundle_ref)


__all__ = ("FileAuditExportVerificationStore",)
