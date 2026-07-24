"""Crash-safe streaming artifact blobs for file and shared-volume deployments."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agent_runtime.artifacts.contracts import (
    ArtifactBlobStat,
    ArtifactBlobWriteResult,
    ArtifactGcCandidate,
)
from agent_runtime.artifacts.errors import (
    ArtifactBlobUnavailableError,
    ArtifactDigestMismatchError,
    ArtifactRangeError,
    ArtifactStorageError,
    ArtifactTooLargeError,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class _BlobIntegrity:
    digest: str
    byte_size: int
    device: int
    inode: int
    modified_ns: int
    changed_ns: int
    created_at: datetime


class FileArtifactBlobStore:
    """Content-addressed blobs with unique temp files and durable publication."""

    def __init__(
        self,
        layout: FileStoreLayout,
        coordinator: FileArtifactPublicationCoordinator | None = None,
    ) -> None:
        self._layout = layout
        self.coordinator = coordinator or FileArtifactPublicationCoordinator(layout)
        self._incoming = layout.objects_dir / ".incoming"
        self._partial_quarantine = layout.objects_dir / ".partial-quarantine"
        self._integrity = layout.objects_dir / ".integrity"
        FileStoreLayout.ensure_dir(self._incoming)
        FileStoreLayout.ensure_dir(self._partial_quarantine)
        FileStoreLayout.ensure_dir(self._integrity)
        self._quarantine_partials()

    async def put_stream(
        self,
        *,
        expected_digest: str | None,
        chunks: AsyncIterator[bytes],
        byte_limit: int,
    ) -> ArtifactBlobWriteResult:
        temp = self._incoming / f"{uuid4().hex}.part"
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with open(temp, "xb") as handle:
                FileStoreLayout.restrict_file(temp)
                async for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise TypeError("artifact stream chunks must be bytes")
                    byte_size += len(chunk)
                    if byte_size > byte_limit:
                        raise ArtifactTooLargeError()
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            actual = digest.hexdigest()
            if expected_digest is not None and expected_digest != actual:
                raise ArtifactDigestMismatchError()
            created = await asyncio.to_thread(
                self._publish,
                temp,
                actual,
                byte_size,
            )
            return ArtifactBlobWriteResult(
                blob_key=actual,
                content_digest=actual,
                byte_size=byte_size,
                range_supported=True,
                created=created,
            )
        except (
            ArtifactDigestMismatchError,
            ArtifactStorageError,
            ArtifactTooLargeError,
            TypeError,
        ):
            raise
        except BaseException as exc:
            if isinstance(
                exc,
                (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt, SystemExit),
            ):
                raise
            raise ArtifactStorageError() from exc
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    async def open_stream(
        self,
        blob_key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        path = self._path(blob_key)
        descriptor, snapshot = await asyncio.to_thread(
            self._open_trusted_descriptor,
            path,
            blob_key,
        )
        byte_size = snapshot.byte_size
        if byte_size == 0 and start is None and end is None:
            first, length = 0, 0
        else:
            first = 0 if start is None else start
            last = byte_size - 1 if end is None else end
            if first < 0 or last < first or last >= byte_size:
                os.close(descriptor)
                raise ArtifactRangeError()
            length = last - first + 1

        async def _stream() -> AsyncIterator[bytes]:
            remaining = length
            try:
                with os.fdopen(descriptor, "rb", closefd=True) as handle:
                    handle.seek(first)
                    while remaining:
                        chunk = await asyncio.to_thread(
                            handle.read,
                            min(_READ_CHUNK_BYTES, remaining),
                        )
                        if not chunk:
                            raise ArtifactBlobUnavailableError()
                        remaining -= len(chunk)
                        yield chunk
                    self._require_same_snapshot(handle.fileno(), snapshot)
            except (FileNotFoundError, OSError) as exc:
                raise ArtifactBlobUnavailableError() from exc

        return _stream()

    async def stat(self, blob_key: str) -> ArtifactBlobStat:
        path = self._path(blob_key)
        try:
            descriptor, snapshot = await asyncio.to_thread(
                self._open_trusted_descriptor,
                path,
                blob_key,
            )
            os.close(descriptor)
        except (FileNotFoundError, OSError) as exc:
            raise ArtifactBlobUnavailableError() from exc
        return ArtifactBlobStat(
            blob_key=blob_key,
            byte_size=snapshot.byte_size,
            range_supported=True,
            created_at=snapshot.created_at,
        )

    async def list_candidates(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> Sequence[ArtifactGcCandidate]:
        candidates: list[ArtifactGcCandidate] = []
        if not self._layout.objects_dir.exists():
            return ()
        for shard in self._layout.objects_dir.iterdir():
            if not shard.is_dir() or shard.name.startswith("."):
                continue
            for blob in shard.iterdir():
                if not blob.is_file() or _DIGEST.fullmatch(blob.name) is None:
                    continue
                modified = datetime.fromtimestamp(blob.stat().st_mtime, tz=timezone.utc)
                if modified < older_than:
                    candidates.append(
                        ArtifactGcCandidate(
                            blob_key=blob.name,
                            unreferenced_since=modified,
                        )
                    )
        candidates.sort(key=lambda item: (item.unreferenced_since, item.blob_key))
        return tuple(candidates[:limit])

    def _publish(self, temp: Path, digest: str, byte_size: int) -> bool:
        target = self._path(digest)
        FileStoreLayout.ensure_dir(target.parent)
        with self.coordinator.locked():
            self.coordinator.restore_locked(digest)
            if target.exists():
                self._verify_existing(target, digest, byte_size)
                return False
            try:
                os.replace(temp, target)
            except OSError as exc:
                raise ArtifactStorageError() from exc
            FileStoreLayout.restrict_file(target)
            self._fsync_directory(target.parent)
            self._write_integrity_locked(
                digest,
                self._snapshot_from_stat(
                    digest=digest,
                    stat_result=target.stat(),
                ),
            )
            return True

    def _verify_existing(self, path: Path, digest: str, byte_size: int) -> None:
        descriptor, snapshot = self._open_trusted_descriptor(path, digest)
        os.close(descriptor)
        if snapshot.byte_size != byte_size:
            raise ArtifactBlobUnavailableError()

    def _open_trusted_descriptor(
        self,
        path: Path,
        digest: str,
    ) -> tuple[int, _BlobIntegrity]:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        with self.coordinator.locked():
            try:
                descriptor = os.open(path, flags)
            except OSError as exc:
                raise ArtifactBlobUnavailableError() from exc
            try:
                stat_result = os.fstat(descriptor)
                snapshot = self._load_integrity_locked(digest)
                if snapshot is None:
                    actual, byte_size = self._hash_descriptor(descriptor)
                    if actual != digest:
                        raise ArtifactBlobUnavailableError()
                    stat_result = os.fstat(descriptor)
                    if stat_result.st_size != byte_size:
                        raise ArtifactBlobUnavailableError()
                    snapshot = self._snapshot_from_stat(
                        digest=digest,
                        stat_result=stat_result,
                    )
                    self._write_integrity_locked(digest, snapshot)
                if snapshot.digest != digest:
                    raise ArtifactBlobUnavailableError()
                self._require_matching_stat(stat_result, snapshot)
                return descriptor, snapshot
            except BaseException:
                os.close(descriptor)
                raise

    @staticmethod
    def _hash_descriptor(descriptor: int) -> tuple[str, int]:
        hasher = hashlib.sha256()
        size = 0
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            size += len(chunk)
            hasher.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return hasher.hexdigest(), size

    def _load_integrity_locked(self, digest: str) -> _BlobIntegrity | None:
        path = self.integrity_path(digest)
        try:
            value = json.loads(path.read_text())
            return _BlobIntegrity(
                digest=str(value["digest"]),
                byte_size=int(value["byte_size"]),
                device=int(value["device"]),
                inode=int(value["inode"]),
                modified_ns=int(value["modified_ns"]),
                changed_ns=int(value["changed_ns"]),
                created_at=datetime.fromisoformat(
                    str(value["created_at"]).replace("Z", "+00:00")
                ),
            )
        except FileNotFoundError:
            return None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactBlobUnavailableError() from exc

    def _write_integrity_locked(
        self,
        digest: str,
        snapshot: _BlobIntegrity,
    ) -> None:
        path = self.integrity_path(digest)
        FileStoreLayout.ensure_dir(path.parent)
        temp = path.parent / f".{digest}-{uuid4().hex}.tmp"
        payload = json.dumps(
            {
                "digest": snapshot.digest,
                "byte_size": snapshot.byte_size,
                "device": snapshot.device,
                "inode": snapshot.inode,
                "modified_ns": snapshot.modified_ns,
                "changed_ns": snapshot.changed_ns,
                "created_at": snapshot.created_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        try:
            with open(temp, "xb") as handle:
                FileStoreLayout.restrict_file(temp)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            self._fsync_directory(path.parent)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _snapshot_from_stat(
        *,
        digest: str,
        stat_result: os.stat_result,
    ) -> _BlobIntegrity:
        return _BlobIntegrity(
            digest=digest,
            byte_size=stat_result.st_size,
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
            modified_ns=stat_result.st_mtime_ns,
            changed_ns=stat_result.st_ctime_ns,
            created_at=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc),
        )

    @classmethod
    def _require_same_snapshot(
        cls,
        descriptor: int,
        snapshot: _BlobIntegrity,
    ) -> None:
        cls._require_matching_stat(os.fstat(descriptor), snapshot)

    @staticmethod
    def _require_matching_stat(
        stat_result: os.stat_result,
        snapshot: _BlobIntegrity,
    ) -> None:
        if (
            snapshot.digest == ""
            or stat_result.st_size != snapshot.byte_size
            or stat_result.st_dev != snapshot.device
            or stat_result.st_ino != snapshot.inode
            or stat_result.st_mtime_ns != snapshot.modified_ns
        ):
            raise ArtifactBlobUnavailableError()

    def integrity_path(self, blob_key: str) -> Path:
        if _DIGEST.fullmatch(blob_key) is None:
            raise ArtifactBlobUnavailableError()
        return self._integrity / blob_key[:2] / f"{blob_key}.json"

    def _path(self, blob_key: str) -> Path:
        if _DIGEST.fullmatch(blob_key) is None:
            raise ArtifactBlobUnavailableError()
        return self._layout.object_path(blob_key)

    def _quarantine_partials(self) -> None:
        moved = False
        for partial in self._incoming.glob("*.part"):
            destination = self._partial_quarantine / (
                f"{partial.stem}-{uuid4().hex}.partial"
            )
            try:
                os.replace(partial, destination)
                moved = True
            except FileNotFoundError:
                continue
        if moved:
            self._fsync_directory(self._incoming)
            self._fsync_directory(self._partial_quarantine)
        for partial in self._integrity.glob("*/*.tmp"):
            destination = self._partial_quarantine / (
                f"integrity-{partial.stem}-{uuid4().hex}.partial"
            )
            try:
                os.replace(partial, destination)
            except FileNotFoundError:
                continue

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ("FileArtifactBlobStore",)
