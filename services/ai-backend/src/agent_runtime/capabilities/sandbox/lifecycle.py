"""Durable, idempotent sandbox lifecycle records.

The sandbox provider is an external system.  This module records the one fact
that prevents a worker restart from blindly repeating a potentially executed
command: whether provider execution has begun.  It is intentionally limited to
safe identifiers and digests; commands, output, host paths, grants, and tokens
are absent from durable state.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from threading import RLock

from pydantic import ValidationError

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxLifecycleRecord,
    SandboxLifecycleState,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxLifecycleAcquisition,
    SandboxLifecycleStore,
)


class SandboxLifecycleConflict(RuntimeError):
    """A lifecycle idempotency key was reused for different immutable facts."""


class SandboxLifecycleTransitionError(RuntimeError):
    """A lifecycle update attempted an unsafe or backward state transition."""


_TRANSITIONS: dict[SandboxLifecycleState, frozenset[SandboxLifecycleState]] = {
    SandboxLifecycleState.REQUESTED: frozenset(
        {
            SandboxLifecycleState.PROVISIONED,
            SandboxLifecycleState.FAILED,
            SandboxLifecycleState.CANCELLED,
        }
    ),
    SandboxLifecycleState.PROVISIONED: frozenset(
        {
            SandboxLifecycleState.UPLOADING,
            SandboxLifecycleState.FAILED,
            SandboxLifecycleState.CANCELLED,
            SandboxLifecycleState.CLEANUP_PENDING,
        }
    ),
    SandboxLifecycleState.UPLOADING: frozenset(
        {
            SandboxLifecycleState.RUNNING,
            SandboxLifecycleState.FAILED,
            SandboxLifecycleState.CANCELLED,
            SandboxLifecycleState.CLEANUP_PENDING,
        }
    ),
    SandboxLifecycleState.RUNNING: frozenset(
        {
            SandboxLifecycleState.COLLECTING,
            SandboxLifecycleState.FAILED,
            SandboxLifecycleState.CANCELLED,
            SandboxLifecycleState.INDETERMINATE,
            SandboxLifecycleState.CLEANUP_PENDING,
        }
    ),
    SandboxLifecycleState.COLLECTING: frozenset(
        {
            SandboxLifecycleState.COMPLETED,
            SandboxLifecycleState.FAILED,
            SandboxLifecycleState.INDETERMINATE,
            SandboxLifecycleState.CLEANUP_PENDING,
        }
    ),
    SandboxLifecycleState.COMPLETED: frozenset(
        {SandboxLifecycleState.CLEANED, SandboxLifecycleState.CLEANUP_PENDING}
    ),
    SandboxLifecycleState.FAILED: frozenset(
        {SandboxLifecycleState.CLEANED, SandboxLifecycleState.CLEANUP_PENDING}
    ),
    SandboxLifecycleState.CANCELLED: frozenset(
        {SandboxLifecycleState.CLEANED, SandboxLifecycleState.CLEANUP_PENDING}
    ),
    SandboxLifecycleState.INDETERMINATE: frozenset(
        {SandboxLifecycleState.CLEANED, SandboxLifecycleState.CLEANUP_PENDING}
    ),
    SandboxLifecycleState.CLEANUP_PENDING: frozenset(
        {SandboxLifecycleState.CLEANED, SandboxLifecycleState.CLEANUP_PENDING}
    ),
    SandboxLifecycleState.CLEANED: frozenset(),
}


def _same_identity(left: SandboxLifecycleRecord, right: SandboxLifecycleRecord) -> bool:
    return (
        left.operation_id == right.operation_id
        and left.run_id == right.run_id
        and left.idempotency_key == right.idempotency_key
        and left.request_digest == right.request_digest
    )


def validate_transition(
    *, previous: SandboxLifecycleRecord, replacement: SandboxLifecycleRecord
) -> None:
    """Fail closed on changed identity, stale replay, or execution regression."""

    if not _same_identity(previous, replacement):
        raise SandboxLifecycleConflict("sandbox lifecycle identity changed")
    # ``model_copy`` intentionally skips Pydantic validation, so revalidate
    # this replacement at the persistence boundary before it becomes durable.
    try:
        SandboxLifecycleRecord.model_validate(replacement.model_dump())
    except ValidationError as exc:
        raise SandboxLifecycleTransitionError(
            "sandbox lifecycle replacement is invalid"
        ) from exc
    if previous.execution_started and not replacement.execution_started:
        raise SandboxLifecycleTransitionError("sandbox execution fact cannot regress")
    if (
        previous.provider_session_ref is not None
        and replacement.provider_session_ref != previous.provider_session_ref
    ):
        raise SandboxLifecycleTransitionError(
            "sandbox provider session reference cannot change"
        )
    if replacement.state is previous.state:
        if replacement.cleanup_attempts < previous.cleanup_attempts:
            raise SandboxLifecycleTransitionError("cleanup attempts cannot regress")
        return
    if replacement.state not in _TRANSITIONS[previous.state]:
        raise SandboxLifecycleTransitionError("sandbox lifecycle transition is invalid")


class InMemorySandboxLifecycleStore(SandboxLifecycleStore):
    """Hermetic test implementation of the same atomic lifecycle contract."""

    def __init__(self) -> None:
        self._records: dict[str, SandboxLifecycleRecord] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self, *, record: SandboxLifecycleRecord
    ) -> SandboxLifecycleAcquisition:
        async with self._lock:
            existing = self._records.get(record.idempotency_key)
            if existing is None:
                self._records[record.idempotency_key] = record
                return SandboxLifecycleAcquisition(created=True, record=record)
            if not _same_identity(existing, record):
                raise SandboxLifecycleConflict("sandbox lifecycle identity changed")
            return SandboxLifecycleAcquisition(created=False, record=existing)

    async def get(self, *, idempotency_key: str) -> SandboxLifecycleRecord | None:
        async with self._lock:
            return self._records.get(idempotency_key)

    async def update(self, *, record: SandboxLifecycleRecord) -> SandboxLifecycleRecord:
        async with self._lock:
            previous = self._records.get(record.idempotency_key)
            if previous is None:
                raise SandboxLifecycleTransitionError("sandbox lifecycle is missing")
            validate_transition(previous=previous, replacement=record)
            self._records[record.idempotency_key] = record
            return record

    async def list_recoverable(
        self, *, limit: int = 100
    ) -> tuple[SandboxLifecycleRecord, ...]:
        async with self._lock:
            records = [
                record
                for record in self._records.values()
                if record.state is not SandboxLifecycleState.CLEANED
            ]
        return tuple(sorted(records, key=lambda item: item.updated_at)[:limit])


class FileSandboxLifecycleStore(SandboxLifecycleStore):
    """Crash-safe, multi-process file store for supervised desktop runtimes.

    Each idempotency key maps to one SHA-256 named JSON record.  An adjacent
    lock file is guarded with ``fcntl.flock`` so two worker processes cannot
    race a read-modify-write transition.  Atomic temp-write + fsync + rename
    prevents torn state after process or power failure.
    """

    _MODE = 0o600

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._process_lock = RLock()

    async def acquire(
        self, *, record: SandboxLifecycleRecord
    ) -> SandboxLifecycleAcquisition:
        return await asyncio.to_thread(self._acquire_sync, record)

    async def get(self, *, idempotency_key: str) -> SandboxLifecycleRecord | None:
        return await asyncio.to_thread(self._read_sync, idempotency_key)

    async def update(self, *, record: SandboxLifecycleRecord) -> SandboxLifecycleRecord:
        return await asyncio.to_thread(self._update_sync, record)

    async def list_recoverable(
        self, *, limit: int = 100
    ) -> tuple[SandboxLifecycleRecord, ...]:
        return await asyncio.to_thread(self._list_recoverable_sync, limit)

    def _acquire_sync(
        self, record: SandboxLifecycleRecord
    ) -> SandboxLifecycleAcquisition:
        with self._locked(record.idempotency_key):
            existing = self._read_sync(record.idempotency_key)
            if existing is None:
                self._write_sync(record)
                return SandboxLifecycleAcquisition(created=True, record=record)
            if not _same_identity(existing, record):
                raise SandboxLifecycleConflict("sandbox lifecycle identity changed")
            return SandboxLifecycleAcquisition(created=False, record=existing)

    def _update_sync(self, record: SandboxLifecycleRecord) -> SandboxLifecycleRecord:
        with self._locked(record.idempotency_key):
            previous = self._read_sync(record.idempotency_key)
            if previous is None:
                raise SandboxLifecycleTransitionError("sandbox lifecycle is missing")
            validate_transition(previous=previous, replacement=record)
            self._write_sync(record)
            return record

    def _read_sync(self, idempotency_key: str) -> SandboxLifecycleRecord | None:
        path = self._record_path(idempotency_key)
        if not path.exists() and not path.is_symlink():
            return None
        try:
            record = self._read_record_path(path)
        except (OSError, ValueError) as exc:
            raise SandboxLifecycleTransitionError(
                "sandbox lifecycle storage is unreadable"
            ) from exc
        if record.idempotency_key != idempotency_key:
            raise SandboxLifecycleTransitionError(
                "sandbox lifecycle storage is unreadable"
            )
        return record

    def _list_recoverable_sync(self, limit: int) -> tuple[SandboxLifecycleRecord, ...]:
        if limit < 1:
            return ()
        records: list[SandboxLifecycleRecord] = []
        for path in self._root.glob("*.json"):
            try:
                record = self._read_record_path(path)
            except (OSError, ValueError) as exc:
                raise SandboxLifecycleTransitionError(
                    "sandbox lifecycle storage is unreadable"
                ) from exc
            if record.state is not SandboxLifecycleState.CLEANED:
                records.append(record)
        return tuple(sorted(records, key=lambda item: item.updated_at)[:limit])

    def _write_sync(self, record: SandboxLifecycleRecord) -> None:
        target = self._record_path(record.idempotency_key)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._root,
            delete=False,
        ) as handle:
            handle.write(record.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, self._MODE)
        os.replace(temporary, target)
        directory_fd = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _read_record_path(self, path: Path) -> SandboxLifecycleRecord:
        mode = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            mode |= os.O_NOFOLLOW
        descriptor = os.open(path, mode)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("sandbox lifecycle record is not a regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
                return SandboxLifecycleRecord.model_validate_json(handle.read())
        finally:
            os.close(descriptor)

    def _record_path(self, idempotency_key: str) -> Path:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.json"

    @contextmanager
    def _locked(self, idempotency_key: str):
        """Acquire the record lock or fail safely on unsupported platforms."""

        try:
            import fcntl  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - Unix desktop product
            raise SandboxLifecycleTransitionError(
                "sandbox lifecycle locking is unavailable"
            ) from exc
        lock_path = self._record_path(idempotency_key).with_suffix(".lock")
        mode = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            mode |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, mode, self._MODE)
        with self._process_lock, os.fdopen(descriptor, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = (
    "FileSandboxLifecycleStore",
    "InMemorySandboxLifecycleStore",
    "SandboxLifecycleConflict",
    "SandboxLifecycleTransitionError",
    "validate_transition",
)
