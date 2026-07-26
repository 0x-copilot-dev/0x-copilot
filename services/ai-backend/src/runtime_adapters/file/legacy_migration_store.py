"""Crash-safe file checkpoint adapter for the E2 legacy migration."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from pydantic import ValidationError

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationCheckpoint,
    LegacyMigrationStateError,
    LegacyMigrationStatus,
)
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileLegacyMigrationCheckpointStore:
    """Tenant-scoped atomic checkpoint state for the desktop file backend."""

    _SUBDIR = "e2_legacy_migration"
    _LOCK = ".e2-legacy-migration.lock"
    _DIR_MODE = 0o700
    _FILE_MODE = 0o600

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._lock_path = self._dir / self._LOCK
        self._lock = asyncio.Lock()

    async def load_or_create(
        self, *, checkpoint: LegacyMigrationCheckpoint
    ) -> LegacyMigrationCheckpoint:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(checkpoint.org_id, checkpoint.migration_id)
                if not path.exists():
                    self._write(path=path, checkpoint=checkpoint)
                    return checkpoint
                existing = self._read(path=path)
                if not _same_source(existing, checkpoint):
                    raise LegacyMigrationStateError()
                return existing

    async def load(
        self, *, org_id: str, migration_id: str
    ) -> LegacyMigrationCheckpoint | None:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(org_id, migration_id)
                if not path.exists():
                    return None
                state = self._read(path=path)
                self._assert_scope(state, org_id=org_id, migration_id=migration_id)
                return state

    async def compare_and_set(
        self,
        *,
        expected: LegacyMigrationCheckpoint,
        after_draft_id: str | None,
        status: LegacyMigrationStatus,
        report_digest: str | None,
        updated_at: datetime,
    ) -> LegacyMigrationCheckpoint | None:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(expected.org_id, expected.migration_id)
                if not path.exists():
                    raise LegacyMigrationStateError()
                current = self._read(path=path)
                self._assert_scope(
                    current,
                    org_id=expected.org_id,
                    migration_id=expected.migration_id,
                )
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
                self._write(path=path, checkpoint=updated)
                return updated

    def _read(self, *, path: Path) -> LegacyMigrationCheckpoint:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"checkpoint"}:
                raise ValueError
            return LegacyMigrationCheckpoint.model_validate(raw["checkpoint"])
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise LegacyMigrationStateError() from exc

    def _write(self, *, path: Path, checkpoint: LegacyMigrationCheckpoint) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            payload = json.dumps(
                {"checkpoint": checkpoint.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._FILE_MODE,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                path.chmod(self._FILE_MODE)
            except OSError:
                pass
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise LegacyMigrationStateError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _path(self, org_id: str, migration_id: str) -> Path:
        digest = hashlib.sha256(f"{org_id}\0{migration_id}".encode()).hexdigest()
        return self._dir / f"{digest}.json"

    @staticmethod
    def _assert_scope(
        checkpoint: LegacyMigrationCheckpoint, *, org_id: str, migration_id: str
    ) -> None:
        if checkpoint.org_id != org_id or checkpoint.migration_id != migration_id:
            raise LegacyMigrationStateError()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            descriptor = os.open(
                self._lock_path,
                os.O_CREAT | os.O_RDWR,
                self._FILE_MODE,
            )
            acquired = False
            try:
                acquire_exclusive(descriptor)
                acquired = True
                yield
            finally:
                if acquired:
                    release_exclusive(descriptor)
                os.close(descriptor)
        except OSError as exc:
            raise LegacyMigrationStateError() from exc

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


__all__ = ("FileLegacyMigrationCheckpointStore",)
