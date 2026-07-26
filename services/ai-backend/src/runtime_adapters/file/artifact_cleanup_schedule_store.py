"""Restart-safe cursor and lease state for the desktop cleanup scheduler."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from agent_runtime.artifacts.cleanup_schedule import (
    ArtifactCleanupScheduleStateError,
)
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileArtifactCleanupScheduleStore:
    """Atomically persisted fair-scheduling state with a cross-process lock."""

    _SUBDIR = "artifact_cleanup_schedule"
    _STATE_FILENAME = "state.json"
    _LOCK_FILENAME = ".artifact-cleanup-schedule.lock"
    _DIR_MODE = 0o700
    _FILE_MODE = 0o600

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._path = self._dir / self._STATE_FILENAME
        self._lock_path = self._dir / self._LOCK_FILENAME
        self._lock = asyncio.Lock()

    async def load_cursor(self) -> str | None:
        async with self._lock:
            with self._exclusive_lock():
                return self._read()["cursor"]

    async def advance_cursor(
        self,
        *,
        owner_id: str,
        expected: str | None,
        next_cursor: str,
    ) -> bool:
        _validate_id(owner_id)
        _validate_id(next_cursor)
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if state["lease_owner"] != owner_id or state["cursor"] != expected:
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
        _validate_id(owner_id)
        if now.tzinfo is None or expires_at.tzinfo is None or expires_at <= now:
            raise ArtifactCleanupScheduleStateError(
                "cleanup scheduler lease is invalid"
            )
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                active_owner = state["lease_owner"]
                active_until = state["lease_expires_at"]
                if (
                    active_owner is not None
                    and active_owner != owner_id
                    and active_until is not None
                    and active_until > now
                ):
                    return False
                state["lease_owner"] = owner_id
                state["lease_expires_at"] = expires_at
                self._write(state)
                return True

    async def release_lease(self, *, owner_id: str) -> None:
        _validate_id(owner_id)
        async with self._lock:
            with self._exclusive_lock():
                state = self._read()
                if state["lease_owner"] != owner_id:
                    return
                state["lease_owner"] = None
                state["lease_expires_at"] = None
                self._write(state)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"cursor": None, "lease_owner": None, "lease_expires_at": None}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "cursor",
                "lease_owner",
                "lease_expires_at",
            }:
                raise ValueError
            cursor = raw["cursor"]
            owner = raw["lease_owner"]
            expires = raw["lease_expires_at"]
            if cursor is not None:
                _validate_id(cursor)
            if owner is not None:
                _validate_id(owner)
            if expires is not None:
                expires = datetime.fromisoformat(str(expires))
                if expires.tzinfo is None:
                    raise ValueError
            if (owner is None) != (expires is None):
                raise ValueError
            return {"cursor": cursor, "lease_owner": owner, "lease_expires_at": expires}
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactCleanupScheduleStateError() from exc

    def _write(self, state: dict[str, Any]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        try:
            payload = {
                "cursor": state["cursor"],
                "lease_owner": state["lease_owner"],
                "lease_expires_at": (
                    state["lease_expires_at"].isoformat()
                    if state["lease_expires_at"] is not None
                    else None
                ),
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, self._FILE_MODE
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise ArtifactCleanupScheduleStateError() from exc
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
                self._lock_path, os.O_CREAT | os.O_RDWR, self._FILE_MODE
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


def _validate_id(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("cleanup scheduler identifier is invalid")


__all__ = ("FileArtifactCleanupScheduleStore",)
