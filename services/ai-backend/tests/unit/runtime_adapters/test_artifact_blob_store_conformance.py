"""Shared bounded-streaming contract for artifact blob adapters."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import pytest

from agent_runtime.artifacts.errors import (
    ArtifactDigestMismatchError,
    ArtifactRangeError,
    ArtifactTooLargeError,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.in_memory.artifact_blob_store import (
    InMemoryArtifactBlobStore,
)


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _read(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


@pytest.fixture(params=("in_memory", "file"))
def artifact_blob_store(request, tmp_path):
    if request.param == "in_memory":
        return InMemoryArtifactBlobStore()
    return FileArtifactBlobStore(FileStoreLayout(tmp_path / "artifact-store"))


class TestArtifactBlobStoreConformance:
    async def test_put_stat_full_read_range_and_dedup(
        self, artifact_blob_store
    ) -> None:
        body = b"0123456789"
        expected = hashlib.sha256(body).hexdigest()

        created = await artifact_blob_store.put_stream(
            expected_digest=expected,
            chunks=_chunks(body[:3], body[3:]),
            byte_limit=len(body),
        )
        duplicate = await artifact_blob_store.put_stream(
            expected_digest=expected,
            chunks=_chunks(body),
            byte_limit=len(body),
        )
        stat = await artifact_blob_store.stat(created.blob_key)
        full = await artifact_blob_store.open_stream(created.blob_key)
        partial = await artifact_blob_store.open_stream(
            created.blob_key,
            start=3,
            end=6,
        )

        assert created.created is True
        assert duplicate.created is False
        assert created.blob_key == expected
        assert stat.byte_size == len(body)
        assert stat.range_supported is True
        assert await _read(full) == body
        assert await _read(partial) == b"3456"

    async def test_empty_body_has_a_valid_full_stream(
        self, artifact_blob_store
    ) -> None:
        written = await artifact_blob_store.put_stream(
            expected_digest=hashlib.sha256(b"").hexdigest(),
            chunks=_chunks(),
            byte_limit=1,
        )
        stream = await artifact_blob_store.open_stream(written.blob_key)

        assert written.byte_size == 0
        assert await _read(stream) == b""
        with pytest.raises(ArtifactRangeError):
            await artifact_blob_store.open_stream(
                written.blob_key,
                start=0,
                end=0,
            )

    async def test_invalid_range_fails_closed(self, artifact_blob_store) -> None:
        written = await artifact_blob_store.put_stream(
            expected_digest=None,
            chunks=_chunks(b"abc"),
            byte_limit=3,
        )

        for start, end in ((-1, 1), (2, 1), (0, 3)):
            with pytest.raises(ArtifactRangeError):
                await artifact_blob_store.open_stream(
                    written.blob_key,
                    start=start,
                    end=end,
                )

    async def test_digest_mismatch_and_limit_leave_no_readable_blob(
        self, artifact_blob_store
    ) -> None:
        body = b"too much"
        with pytest.raises(ArtifactDigestMismatchError):
            await artifact_blob_store.put_stream(
                expected_digest="0" * 64,
                chunks=_chunks(body),
                byte_limit=len(body),
            )
        with pytest.raises(ArtifactTooLargeError):
            await artifact_blob_store.put_stream(
                expected_digest=None,
                chunks=_chunks(body),
                byte_limit=len(body) - 1,
            )

    def test_legacy_delete_hook_is_not_exposed(self, artifact_blob_store) -> None:
        assert not hasattr(artifact_blob_store, "delete_if_unreferenced")
