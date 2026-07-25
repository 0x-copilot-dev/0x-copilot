"""Hermetic in-memory implementation of ``ArtifactBlobStorePort``."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone

from agent_runtime.artifacts.contracts import (
    ArtifactBlobStat,
    ArtifactBlobWriteResult,
    ArtifactGcCandidate,
)
from agent_runtime.artifacts.errors import (
    ArtifactBlobUnavailableError,
    ArtifactDigestMismatchError,
    ArtifactRangeError,
    ArtifactTooLargeError,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)


class InMemoryArtifactBlobStore:
    """Process-local, bounded, content-addressed artifact bytes."""

    def __init__(
        self,
        coordinator: InMemoryArtifactPublicationCoordinator | None = None,
    ) -> None:
        self.coordinator = coordinator or InMemoryArtifactPublicationCoordinator()
        self._lock = self.coordinator.lock
        self._blobs = self.coordinator.blobs
        self._created_at = self.coordinator.created_at

    async def put_stream(
        self,
        *,
        expected_digest: str | None,
        chunks: AsyncIterator[bytes],
        byte_limit: int,
    ) -> ArtifactBlobWriteResult:
        digest = hashlib.sha256()
        body = bytearray()
        async for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise TypeError("artifact stream chunks must be bytes")
            if len(body) + len(chunk) > byte_limit:
                raise ArtifactTooLargeError()
            digest.update(chunk)
            body.extend(chunk)
        actual = digest.hexdigest()
        if expected_digest is not None and expected_digest != actual:
            raise ArtifactDigestMismatchError()
        with self._lock:
            restored = self.coordinator.restore_locked(actual)
            created = actual not in self._blobs
            if created:
                self._blobs[actual] = bytes(body)
                self._created_at[actual] = datetime.now(timezone.utc)
            elif self._blobs[actual] != bytes(body):
                raise ArtifactBlobUnavailableError()
            if restored:
                created = False
        return ArtifactBlobWriteResult(
            blob_key=actual,
            content_digest=actual,
            byte_size=len(body),
            range_supported=True,
            created=created,
        )

    async def open_stream(
        self,
        blob_key: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        with self._lock:
            try:
                body = self._blobs[blob_key]
            except KeyError as exc:
                raise ArtifactBlobUnavailableError() from exc
        if not body and start is None and end is None:
            first, length = 0, 0
        else:
            first = 0 if start is None else start
            last = len(body) - 1 if end is None else end
            if first < 0 or last < first or last >= len(body):
                raise ArtifactRangeError()
            length = last - first + 1

        async def _stream() -> AsyncIterator[bytes]:
            if length:
                yield body[first : first + length]

        return _stream()

    async def stat(self, blob_key: str) -> ArtifactBlobStat:
        with self._lock:
            try:
                body = self._blobs[blob_key]
                created_at = self._created_at[blob_key]
            except KeyError as exc:
                raise ArtifactBlobUnavailableError() from exc
        return ArtifactBlobStat(
            blob_key=blob_key,
            byte_size=len(body),
            range_supported=True,
            created_at=created_at,
        )

    async def list_candidates(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> Sequence[ArtifactGcCandidate]:
        with self._lock:
            candidates = [
                ArtifactGcCandidate(blob_key=key, unreferenced_since=created_at)
                for key, created_at in self._created_at.items()
                if created_at < older_than
            ]
        candidates.sort(key=lambda item: (item.unreferenced_since, item.blob_key))
        return tuple(candidates[:limit])


__all__ = ("InMemoryArtifactBlobStore",)
