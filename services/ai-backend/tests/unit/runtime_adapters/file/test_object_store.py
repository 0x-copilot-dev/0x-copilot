"""Content-addressed object store: put/get round-trip, idempotency, integrity."""

from __future__ import annotations

import pytest

from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import (
    FileObjectStore,
    ObjectRef,
    ObjectStoreError,
)


class TestFileObjectStore:
    def _store(self, tmp_path) -> FileObjectStore:
        layout = FileStoreLayout(tmp_path / "root")
        layout.ensure_scaffold()
        return FileObjectStore(layout)

    def test_put_get_round_trip(self, tmp_path) -> None:
        store = self._store(tmp_path)
        ref = store.put(b"hello world", media_type="text/plain", preview="hello")
        assert isinstance(ref, ObjectRef)
        assert ref.size == 11
        assert ref.media_type == "text/plain"
        assert len(ref.sha256) == 64
        assert store.get(ref) == b"hello world"
        # A bare digest also resolves.
        assert store.get(ref.sha256) == b"hello world"

    def test_put_is_idempotent_by_content(self, tmp_path) -> None:
        store = self._store(tmp_path)
        first = store.put(b"same bytes")
        second = store.put(b"same bytes")
        assert first.sha256 == second.sha256
        assert store.get(first) == b"same bytes"

    def test_get_missing_raises(self, tmp_path) -> None:
        store = self._store(tmp_path)
        with pytest.raises(ObjectStoreError):
            store.get("0" * 64)

    def test_corrupted_blob_fails_integrity(self, tmp_path) -> None:
        layout = FileStoreLayout(tmp_path / "root")
        layout.ensure_scaffold()
        store = FileObjectStore(layout)
        ref = store.put(b"trusted content")
        # Tamper with the stored bytes on disk.
        path = layout.object_path(ref.sha256)
        path.write_bytes(b"tampered content!")
        with pytest.raises(ObjectStoreError):
            store.get(ref)
