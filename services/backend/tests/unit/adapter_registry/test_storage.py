"""``LocalFilesystemSourceStorage`` round-trip + path-traversal guard."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend_app.adapter_registry.storage import (
    LocalFilesystemSourceStorage,
    InMemorySourceStorage,
)


class TestLocalFilesystemSourceStorage:
    def test_put_get_round_trip(self, tmp_path: Path) -> None:
        storage = LocalFilesystemSourceStorage(tmp_path)
        source = b"export const renderCurrent = () => null;"
        stored = storage.put(scheme="saas:salesforce", version=1, source=source)
        assert stored.size_bytes == len(source)
        assert stored.digest == hashlib.sha256(source).hexdigest()
        loaded = storage.get(key=stored.key)
        assert loaded == source

    def test_delete_removes_artifact(self, tmp_path: Path) -> None:
        storage = LocalFilesystemSourceStorage(tmp_path)
        stored = storage.put(scheme="saas:slack", version=2, source=b"x")
        assert storage.delete(key=stored.key) is True
        assert storage.delete(key=stored.key) is False
        assert storage.get(key=stored.key) is None

    def test_digest_stable_for_identical_payload(self, tmp_path: Path) -> None:
        storage = LocalFilesystemSourceStorage(tmp_path)
        first = storage.put(scheme="saas:notion", version=1, source=b"hello")
        second = storage.put(scheme="saas:notion", version=1, source=b"hello")
        assert first.digest == second.digest

    def test_rejects_path_traversal_scheme(self, tmp_path: Path) -> None:
        storage = LocalFilesystemSourceStorage(tmp_path)
        with pytest.raises(ValueError):
            storage.put(scheme="../escape", version=1, source=b"x")

    def test_get_outside_root_returns_none(self, tmp_path: Path) -> None:
        storage = LocalFilesystemSourceStorage(tmp_path)
        assert storage.get(key="../outside.js") is None


class TestInMemorySourceStorage:
    def test_round_trip(self) -> None:
        storage = InMemorySourceStorage()
        stored = storage.put(scheme="saas:linear", version=1, source=b"z")
        assert storage.get(key=stored.key) == b"z"
        assert storage.delete(key=stored.key) is True
        assert storage.get(key=stored.key) is None
