"""Crash-safe file store for E2 D5 legacy-stage migration mappings."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from pydantic import ValidationError

from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationStateError,
    LegacyStageMigrationOutcome,
    LegacyStageMigrationRecord,
)
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileLegacyStageMigrationStore:
    """One atomic, tenant-scoped source-bound record per old stage."""

    _SUBDIR = "e2_legacy_stage_migration"
    _LOCK = ".e2-legacy-stage-migration.lock"
    _DIR_MODE = 0o700
    _FILE_MODE = 0o600

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._lock_path = self._dir / self._LOCK
        self._lock = asyncio.Lock()

    async def load_or_create(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(record)
                if not path.exists():
                    self._write(path=path, record=record)
                    return record
                existing = self._read(path=path)
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
        async with self._lock:
            with self._exclusive_lock():
                path = self._path_values(org_id, migration_id, run_id, legacy_stage_id)
                if not path.exists():
                    return None
                record = self._read(path=path)
                if _key(record) != (org_id, migration_id, run_id, legacy_stage_id):
                    raise LegacyMigrationStateError()
                return record

    async def replace_frozen(
        self, *, record: LegacyStageMigrationRecord
    ) -> LegacyStageMigrationRecord:
        """Atomically replace only a prior reconciliation observation."""

        async with self._lock:
            with self._exclusive_lock():
                path = self._path(record)
                if not path.exists():
                    raise LegacyMigrationStateError()
                existing = self._read(path=path)
                if existing.outcome is not LegacyStageMigrationOutcome.FROZEN_RECONCILE:
                    raise LegacyMigrationStateError()
                self._write(path=path, record=record)
                return record

    def _path(self, record: LegacyStageMigrationRecord) -> Path:
        return self._path_values(*_key(record))

    def _path_values(
        self, org_id: str, migration_id: str, run_id: str, stage_id: str
    ) -> Path:
        material = "\0".join((org_id, migration_id, run_id, stage_id)).encode()
        return self._dir / f"{hashlib.sha256(material).hexdigest()}.json"

    def _read(self, *, path: Path) -> LegacyStageMigrationRecord:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"record"}:
                raise ValueError
            return LegacyStageMigrationRecord.model_validate(raw["record"])
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise LegacyMigrationStateError() from exc

    def _write(self, *, path: Path, record: LegacyStageMigrationRecord) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            payload = json.dumps(
                {"record": record.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            fd = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, self._FILE_MODE
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, self._FILE_MODE)
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise LegacyMigrationStateError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, self._FILE_MODE)
            acquired = False
            try:
                acquire_exclusive(fd)
                acquired = True
                yield
            finally:
                if acquired:
                    release_exclusive(fd)
                os.close(fd)
        except OSError as exc:
            raise LegacyMigrationStateError() from exc

    def _sync_directory(self) -> None:
        try:
            fd = os.open(self._dir, os.O_RDONLY)
            os.fsync(fd)
        except OSError:
            return
        finally:
            try:
                os.close(fd)
            except UnboundLocalError:
                pass


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


__all__ = ("FileLegacyStageMigrationStore",)
