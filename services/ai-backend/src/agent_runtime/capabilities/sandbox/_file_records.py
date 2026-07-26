"""Private secure JSON-record primitives for D3's missing file projections."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import tempfile
from threading import Lock
from typing import Any
import unicodedata

from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive
from runtime_adapters.file._paths import FileStoreLayout


_DIR_MODE = 0o700
_FILE_MODE = 0o600
_MAX_RECORD_BYTES = 1_048_576
_RECORD_NAME = re.compile(r"^[0-9a-f]{64}\.json$")


class SandboxFileRecordError(RuntimeError):
    """A sandbox record cannot be safely read, written, or locked."""


def canonical_record_key(value: str, *, field: str) -> str:
    """Reject a path-shaped logical id before hashing it into a record name."""

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or any(not character.isprintable() for character in value)
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    ):
        raise SandboxFileRecordError(f"sandbox {field} is not a safe logical id")
    return value


class SandboxFileRecords:
    """One private SHA-named record directory below ``layout.root/sandbox``."""

    def __init__(self, *, layout: FileStoreLayout, category: str) -> None:
        self._layout = layout
        self._category = category
        self._locks_guard = Lock()
        self._locks: dict[str, Lock] = {}
        FileStoreLayout.ensure_dir(layout.root)
        self._sandbox_dir = self._ensure_private_child(layout.root, "sandbox")
        self._dir = self._ensure_private_child(self._sandbox_dir, category)

    @property
    def directory(self) -> Path:
        return self._dir

    def path_for(self, key: str, *, field: str) -> Path:
        key = canonical_record_key(key, field=field)
        return self._dir / f"{FileStoreLayout.safe_key(key)}.json"

    @contextmanager
    def locked(self, key: str, *, field: str) -> Iterator[None]:
        """Serialize one logical record across threads and worker processes."""

        path = self.path_for(key, field=field)
        self._assert_private_dirs()
        with self._lock_for(path.name):
            descriptor: int | None = None
            acquired = False
            try:
                mode = os.O_RDWR | os.O_CREAT
                if hasattr(os, "O_NOFOLLOW"):
                    mode |= os.O_NOFOLLOW
                descriptor = os.open(path.with_suffix(".lock"), mode, _FILE_MODE)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
                    or metadata.st_nlink != 1
                ):
                    raise SandboxFileRecordError("sandbox record lock is unsafe")
                acquire_exclusive(descriptor)
                acquired = True
                yield
            except SandboxFileRecordError:
                raise
            except OSError as exc:
                raise SandboxFileRecordError(
                    "sandbox record lock is unavailable"
                ) from exc
            finally:
                if descriptor is not None:
                    try:
                        if acquired:
                            release_exclusive(descriptor)
                    finally:
                        os.close(descriptor)

    def read(self, key: str, *, field: str) -> dict[str, Any] | None:
        return self._read_path(self.path_for(key, field=field))

    def write(self, key: str, *, field: str, value: dict[str, Any]) -> None:
        """Commit JSON with temp write, file fsync, rename, and parent fsync."""

        self._assert_private_dirs()
        path = self.path_for(key, field=field)
        try:
            self._assert_private_record(path)
        except FileNotFoundError:
            pass
        try:
            payload = json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SandboxFileRecordError(
                "sandbox record is not JSON serializable"
            ) from exc
        if len(payload) > _MAX_RECORD_BYTES:
            raise SandboxFileRecordError("sandbox record exceeds the bounded size")

        descriptor: int | None = None
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".sandbox-record-", suffix=".tmp", dir=self._dir
            )
            temporary = Path(temporary_name)
            os.fchmod(descriptor, _FILE_MODE)
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, path)
            temporary = None
            self._sync_directory()
        except OSError as exc:
            raise SandboxFileRecordError(
                "sandbox record could not be committed"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def remove(self, key: str, *, field: str) -> None:
        self._assert_private_dirs()
        path = self.path_for(key, field=field)
        try:
            self._assert_private_record(path)
        except FileNotFoundError:
            return
        try:
            path.unlink()
            self._sync_directory()
        except OSError as exc:
            raise SandboxFileRecordError("sandbox record could not be removed") from exc

    def iter_records(self) -> Iterator[tuple[Path, dict[str, Any]]]:
        self._assert_private_dirs()
        try:
            entries = sorted(os.scandir(self._dir), key=lambda entry: entry.name)
        except OSError as exc:
            raise SandboxFileRecordError(
                "sandbox record directory is unreadable"
            ) from exc
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            if not _RECORD_NAME.fullmatch(entry.name):
                raise SandboxFileRecordError("sandbox record name is noncanonical")
            path = Path(entry.path)
            self._assert_private_record(path)
            payload = self._read_path(path)
            if payload is None:  # pragma: no cover - hostile concurrent removal
                raise SandboxFileRecordError("sandbox record disappeared during scan")
            yield path, payload

    def _read_path(self, path: Path) -> dict[str, Any] | None:
        self._assert_private_dirs()
        try:
            self._assert_private_record(path)
        except FileNotFoundError:
            return None
        descriptor: int | None = None
        try:
            mode = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                mode |= os.O_NOFOLLOW
            descriptor = os.open(path, mode)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
                or metadata.st_nlink != 1
            ):
                raise SandboxFileRecordError("sandbox record is unsafe")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                payload = handle.read(_MAX_RECORD_BYTES + 1)
            if len(payload) > _MAX_RECORD_BYTES:
                raise SandboxFileRecordError("sandbox record exceeds the bounded size")
            value = json.loads(payload.decode("utf-8"))
            if not isinstance(value, dict):
                raise SandboxFileRecordError("sandbox record is not a JSON object")
            return value
        except SandboxFileRecordError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxFileRecordError("sandbox record is corrupt") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _lock_for(self, filename: str) -> Lock:
        with self._locks_guard:
            return self._locks.setdefault(filename, Lock())

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("sandbox record write made no progress")
            offset += written

    @staticmethod
    def _ensure_private_child(parent: Path, name: str) -> Path:
        SandboxFileRecords._assert_directory(parent)
        child = parent / name
        try:
            metadata = os.lstat(child)
        except FileNotFoundError:
            try:
                os.mkdir(child, _DIR_MODE)
            except FileExistsError:
                pass
            except OSError as exc:
                raise SandboxFileRecordError(
                    "sandbox directory cannot be created"
                ) from exc
            try:
                metadata = os.lstat(child)
            except OSError as exc:
                raise SandboxFileRecordError(
                    "sandbox directory cannot be inspected"
                ) from exc
        except OSError as exc:
            raise SandboxFileRecordError(
                "sandbox directory cannot be inspected"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SandboxFileRecordError("sandbox directory is unsafe")
        try:
            os.chmod(child, _DIR_MODE)
        except OSError as exc:
            raise SandboxFileRecordError(
                "sandbox directory cannot be made private"
            ) from exc
        SandboxFileRecords._assert_directory(child, private=True)
        return child

    def _assert_private_dirs(self) -> None:
        self._assert_directory(self._sandbox_dir, private=True)
        self._assert_directory(self._dir, private=True)

    @staticmethod
    def _assert_directory(path: Path, *, private: bool = False) -> None:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise SandboxFileRecordError("sandbox directory is unreadable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SandboxFileRecordError("sandbox directory is unsafe")
        if private and stat.S_IMODE(metadata.st_mode) != _DIR_MODE:
            raise SandboxFileRecordError("sandbox directory does not have private mode")

    @staticmethod
    def _assert_private_record(path: Path) -> None:
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise SandboxFileRecordError("sandbox record is unreadable") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SandboxFileRecordError("sandbox record is not a regular file")
        if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
            raise SandboxFileRecordError("sandbox record does not have private mode")
        if metadata.st_nlink != 1:
            raise SandboxFileRecordError("sandbox record has an unsafe link count")

    def _sync_directory(self) -> None:
        descriptor: int | None = None
        try:
            mode = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                mode |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                mode |= os.O_NOFOLLOW
            descriptor = os.open(self._dir, mode)
            os.fsync(descriptor)
        except OSError as exc:
            raise SandboxFileRecordError(
                "sandbox directory could not be synced"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)


__all__ = ("SandboxFileRecordError", "SandboxFileRecords", "canonical_record_key")
