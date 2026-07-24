"""Exactly-once sandbox provider usage attribution."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from threading import RLock

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    SandboxUsageAttribution,
)
from agent_runtime.capabilities.sandbox.ports import SandboxUsageMeterPort


class InMemorySandboxUsageMeter(SandboxUsageMeterPort):
    """Hermetic exactly-once meter used by unit tests and local wiring."""

    def __init__(self) -> None:
        self._records: dict[str, SandboxUsageAttribution] = {}
        self._lock = asyncio.Lock()

    async def record_once(self, attribution: SandboxUsageAttribution) -> None:
        async with self._lock:
            prior = self._records.get(attribution.operation_id)
            if prior is None:
                self._records[attribution.operation_id] = attribution
            elif prior != attribution:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_LIFECYCLE_CONFLICT,
                    "Sandbox usage attribution conflicts with the prior operation record.",
                )

    def get(self, operation_id: str) -> SandboxUsageAttribution | None:
        """Test/read-side helper; production reads its chosen durable ledger."""

        return self._records.get(operation_id)


class FileSandboxUsageMeter(SandboxUsageMeterPort):
    """Atomic local durable adapter for one sandbox-operation usage row.

    Desktop deployments can use this adapter until their shared usage store is
    injected.  The filename is a digest of the operation ID, and every read or
    write rejects symlinks so a local attacker cannot redirect accounting.
    """

    _MODE = 0o600

    def __init__(self, *, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._process_lock = RLock()

    async def record_once(self, attribution: SandboxUsageAttribution) -> None:
        await asyncio.to_thread(self._record_sync, attribution)

    def _record_sync(self, attribution: SandboxUsageAttribution) -> None:
        with self._locked(attribution.operation_id):
            existing = self._read(attribution.operation_id)
            if existing is not None:
                if existing != attribution:
                    raise SandboxError(
                        SandboxErrorCode.SANDBOX_LIFECYCLE_CONFLICT,
                        "Sandbox usage attribution conflicts with the prior operation record.",
                    )
                return
            target = self._path(attribution.operation_id)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._root, delete=False
            ) as handle:
                handle.write(attribution.model_dump_json())
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.chmod(temporary, self._MODE)
            os.replace(temporary, target)
            descriptor = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _read(self, operation_id: str) -> SandboxUsageAttribution | None:
        path = self._path(operation_id)
        if not path.exists() and not path.is_symlink():
            return None
        mode = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            mode |= os.O_NOFOLLOW
        descriptor = os.open(path, mode)
        try:
            return SandboxUsageAttribution.model_validate_json(
                os.read(descriptor, 1_000_000).decode("utf-8")
            )
        finally:
            os.close(descriptor)

    def _path(self, operation_id: str) -> Path:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self._root / f"{digest}.json"

    @contextmanager
    def _locked(self, operation_id: str):
        try:
            import fcntl  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - Unix desktop product
            raise SandboxError(
                SandboxErrorCode.SANDBOX_LIFECYCLE_CONFLICT,
                "Sandbox usage accounting locking is unavailable.",
            ) from exc
        lock_path = self._path(operation_id).with_suffix(".lock")
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


__all__ = ("FileSandboxUsageMeter", "InMemorySandboxUsageMeter")
