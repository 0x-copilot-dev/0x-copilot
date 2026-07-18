"""Snapshot persistence round-trips through the real content-addressed store."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.interpreter.contracts import (
    InterpreterError,
    InterpreterErrorCode,
    InterpreterLimitKind,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ObjectStoreSnapshotStore,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore


class SnapshotStoreMixin:
    ENVELOPE = {
        "adapter": "monty",
        "abi_version": "0.0.18",
        "source_sha256": "a" * 64,
        "limit_profile_hash": "profhash",
    }

    def _store(self, tmp_path) -> ObjectStoreSnapshotStore:
        blob = FileObjectStore(FileStoreLayout(tmp_path))
        return ObjectStoreSnapshotStore(blob)


class TestSnapshotRoundTrip(SnapshotStoreMixin):
    def test_put_then_get_returns_same_bytes(self, tmp_path) -> None:
        store = self._store(tmp_path)
        data = b"monty-snapshot-bytes-\x00\x01\x02"
        ref = store.put(
            data,
            invocation_index=3,
            max_snapshot_bytes=1024,
            **self.ENVELOPE,
        )
        assert ref.size == len(data)
        assert ref.invocation_index == 3
        assert store.get(ref) == data

    def test_oversized_snapshot_rejected_with_typed_limit(self, tmp_path) -> None:
        store = self._store(tmp_path)
        with pytest.raises(InterpreterError) as excinfo:
            store.put(
                b"x" * 100,
                invocation_index=0,
                max_snapshot_bytes=10,
                **self.ENVELOPE,
            )
        assert excinfo.value.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert excinfo.value.limit_kind is InterpreterLimitKind.SNAPSHOT_BYTES

    def test_missing_blob_is_snapshot_invalid(self, tmp_path) -> None:
        store = self._store(tmp_path)
        ref = store.put(
            b"present", invocation_index=0, max_snapshot_bytes=1024, **self.ENVELOPE
        )
        phantom = ref.model_copy(update={"sha256": "b" * 64})
        with pytest.raises(InterpreterError) as excinfo:
            store.get(phantom)
        assert excinfo.value.code is InterpreterErrorCode.SNAPSHOT_INVALID


class TestSnapshotCompatibility(SnapshotStoreMixin):
    def test_matching_envelope_passes(self, tmp_path) -> None:
        store = self._store(tmp_path)
        ref = store.put(
            b"data", invocation_index=0, max_snapshot_bytes=1024, **self.ENVELOPE
        )
        # Does not raise.
        ObjectStoreSnapshotStore.ensure_compatible(ref, **self.ENVELOPE)

    @pytest.mark.parametrize(
        "field",
        ["adapter", "abi_version", "source_sha256", "limit_profile_hash"],
    )
    def test_mismatch_fails_incompatible(self, tmp_path, field) -> None:
        store = self._store(tmp_path)
        ref = store.put(
            b"data", invocation_index=0, max_snapshot_bytes=1024, **self.ENVELOPE
        )
        mutated = dict(self.ENVELOPE)
        mutated[field] = "different-value-" + "c" * 50
        if field in {"source_sha256"}:
            mutated[field] = "d" * 64
        with pytest.raises(InterpreterError) as excinfo:
            ObjectStoreSnapshotStore.ensure_compatible(ref, **mutated)
        assert excinfo.value.code is InterpreterErrorCode.SNAPSHOT_INCOMPATIBLE
