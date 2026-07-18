"""Unit tests for the ``BrokeredWorkspaceBackend`` WRITE-through path (slice 3b).

Proves the security-critical contract for host mutations reached via Deep
Agents' ``write_file`` / ``edit_file`` (the approval gate is exercised
separately, at the harness level, in ``tests/unit/agent_runtime/execution``):

* snapshot-before-write persists the pre-image + emits a reference BEFORE an
  overwrite / edit runs, and a pure CREATE needs no pre-image;
* every mutating broker request carries the ``run_capability_context``;
* the pre-image durability failure is fail-closed (no mutation commits);
* a read-only mount / broker mode-gate surfaces safely (no host path leaks);
* the write path is inert (raises) without the write-authority triple.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceMount,
    WorkspaceMutationSnapshot,
    WorkspaceSnapshotError,
    WorkspaceWriteNotSupportedError,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    FakeBrokerFs,
    RecordingBroker,
)

RCX = "rcx_test_pinned"


@dataclass(frozen=True)
class _Ref:
    """Minimal ``ObjectRef``-shaped return from the fake store."""

    sha256: str
    size: int


@dataclass
class FakeObjectStore:
    """In-memory content-addressed store satisfying ``WorkspaceSnapshotStore``."""

    puts: list[bytes] = field(default_factory=list)

    def put(
        self,
        data: bytes,
        *,
        media_type: str = "application/octet-stream",
        preview: str | None = None,
    ) -> _Ref:
        self.puts.append(data)
        return _Ref(sha256=hashlib.sha256(data).hexdigest(), size=len(data))


@dataclass
class FailingObjectStore:
    """A store whose ``put`` always fails — drives the fail-closed path."""

    def put(self, data: bytes, *, media_type: str = "", preview: str | None = None):
        raise RuntimeError("disk full")


@dataclass
class RecordingEmitter:
    """Captures snapshot records + how many mutations the broker had seen at emit.

    Recording the broker mutation count AT emit time proves the reference event
    fires strictly BEFORE the host mutation (the count must be 0).
    """

    broker: RecordingBroker
    records: list[WorkspaceMutationSnapshot] = field(default_factory=list)
    mutations_seen_at_emit: list[int] = field(default_factory=list)
    raise_on_emit: bool = False

    _MUTATION_ROUTES = ("/v1/fs/write", "/v1/fs/edit")

    async def __call__(self, record: WorkspaceMutationSnapshot) -> None:
        self.mutations_seen_at_emit.append(
            sum(
                1
                for route, _h, _b in self.broker.requests
                if route in self._MUTATION_ROUTES
            )
        )
        self.records.append(record)
        if self.raise_on_emit:
            raise RuntimeError("event bus down")


class WriteBackendMixin:
    """Build a write-authorized backend over a fake broker with a pinned context."""

    GRANT_RW = "grant-rw"
    GRANT_RO = "grant-ro"

    @classmethod
    def broker(cls, files: dict[str, bytes] | None = None) -> RecordingBroker:
        broker = RecordingBroker(
            grants={
                cls.GRANT_RW: FakeBrokerFs(files=dict(files or {})),
                cls.GRANT_RO: FakeBrokerFs(files={"ro.txt": b"locked"}),
            },
            grant_meta={
                cls.GRANT_RW: {"mode": "read_write", "label": "proj"},
                cls.GRANT_RO: {"mode": "read_only", "label": "docs"},
            },
        )
        # Pin a run context authorizing the writable grant (as /v1/runs/begin would).
        broker.run_contexts[RCX] = {
            cls.GRANT_RW: "read_write",
            cls.GRANT_RO: "read_only",
        }
        return broker

    @classmethod
    def backend(
        cls,
        broker: RecordingBroker,
        *,
        store: object | None = None,
        emitter: object | None = None,
        run_capability_context: str | None = RCX,
    ) -> BrokeredWorkspaceBackend:
        return BrokeredWorkspaceBackend(
            client=broker.client(),
            mounts=[
                WorkspaceMount(name="proj", grant_id=cls.GRANT_RW, mode="read_write"),
                WorkspaceMount(name="docs", grant_id=cls.GRANT_RO, mode="read_only"),
            ],
            run_capability_context=run_capability_context,
            snapshot_store=store,
            snapshot_emitter=emitter,
        )

    @classmethod
    def wired(
        cls, files: dict[str, bytes] | None = None
    ) -> tuple[
        BrokeredWorkspaceBackend, RecordingBroker, FakeObjectStore, RecordingEmitter
    ]:
        broker = cls.broker(files)
        store = FakeObjectStore()
        emitter = RecordingEmitter(broker=broker)
        backend = cls.backend(broker, store=store, emitter=emitter)
        return backend, broker, store, emitter

    @staticmethod
    def _mutations(broker: RecordingBroker) -> list[tuple[str, dict[str, object]]]:
        return [
            (route, body)
            for route, _h, body in broker.requests
            if route in ("/v1/fs/write", "/v1/fs/edit")
        ]


class TestSupportsWrites(WriteBackendMixin):
    """The write path is live only with the full write-authority triple + a writable mount."""

    def test_read_only_backend_is_inert(self) -> None:
        # No context/store/emitter → byte-identical to the read-only slice.
        backend = self.backend(self.broker(), run_capability_context=None)
        assert backend.supports_writes is False
        with pytest.raises(WorkspaceWriteNotSupportedError):
            asyncio.run(backend.awrite("/proj/x.txt", "hi"))

    def test_missing_store_disables_writes(self) -> None:
        backend = self.backend(
            self.broker(), store=None, emitter=RecordingEmitter(self.broker())
        )
        assert backend.supports_writes is False

    def test_writable_when_triple_present(self) -> None:
        backend, *_ = self.wired()
        assert backend.supports_writes is True
        assert backend.run_capability_context == RCX


class TestCreateNeedsNoPreImage(WriteBackendMixin):
    """A pure create writes without snapshotting a pre-image."""

    def test_create_skips_snapshot(self) -> None:
        backend, broker, store, emitter = self.wired()
        result = asyncio.run(backend.awrite("/proj/new.txt", "hello"))
        assert result.error is None
        assert result.path == "/proj/new.txt"
        assert store.puts == []  # no pre-image
        assert emitter.records == []
        assert broker.grants[self.GRANT_RW].files["new.txt"] == b"hello"

    def test_create_carries_run_capability_context(self) -> None:
        backend, broker, _store, _emitter = self.wired()
        asyncio.run(backend.awrite("/proj/new.txt", "hello"))
        route, body = self._mutations(broker)[-1]
        assert route == "/v1/fs/write"
        assert body["run_capability_context"] == RCX


class TestSnapshotBeforeOverwrite(WriteBackendMixin):
    """Overwriting existing content snapshots the pre-image + emits BEFORE the write."""

    def test_overwrite_snapshots_pre_image_first(self) -> None:
        backend, broker, store, emitter = self.wired(files={"a.txt": b"OLD-BYTES"})
        result = asyncio.run(backend.awrite("/proj/a.txt", "NEW"))
        assert result.error is None
        # Pre-image durably captured…
        assert store.puts == [b"OLD-BYTES"]
        # …and referenced by a typed event that fired BEFORE the broker write.
        assert len(emitter.records) == 1
        record = emitter.records[0]
        assert record.op == "overwrite"
        assert record.mount == "proj"
        assert record.path == "/proj/a.txt"
        assert record.object_sha256 == hashlib.sha256(b"OLD-BYTES").hexdigest()
        assert record.run_capability_context == RCX
        assert emitter.mutations_seen_at_emit == [0]  # broker not yet mutated
        # …and only then did the overwrite land.
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"NEW"


class TestEditSnapshotAndSemantics(WriteBackendMixin):
    """``edit`` snapshots the pre-image, then applies string replacement via the broker."""

    def test_edit_replaces_unique_string(self) -> None:
        backend, broker, store, emitter = self.wired(files={"a.txt": b"hello world"})
        result = asyncio.run(backend.aedit("/proj/a.txt", "world", "there"))
        assert result.error is None
        assert result.occurrences == 1
        assert result.path == "/proj/a.txt"
        assert store.puts == [b"hello world"]
        assert emitter.records[0].op == "edit"
        assert emitter.mutations_seen_at_emit == [0]
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"hello there"

    def test_edit_replace_all(self) -> None:
        backend, broker, _store, _emitter = self.wired(files={"a.txt": b"a a a"})
        result = asyncio.run(backend.aedit("/proj/a.txt", "a", "b", True))
        assert result.occurrences == 3
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"b b b"

    def test_edit_missing_file_errors_without_mutation(self) -> None:
        backend, broker, store, _emitter = self.wired()
        result = asyncio.run(backend.aedit("/proj/missing.txt", "x", "y"))
        assert result.error is not None
        assert store.puts == []
        assert self._mutations(broker) == []

    def test_edit_non_unique_string_errors_without_snapshot(self) -> None:
        backend, broker, store, _emitter = self.wired(files={"a.txt": b"x x"})
        result = asyncio.run(backend.aedit("/proj/a.txt", "x", "y"))
        assert result.error is not None
        assert store.puts == []
        assert self._mutations(broker) == []  # no snapshot, no mutation

    def test_edit_carries_run_capability_context(self) -> None:
        backend, broker, _store, _emitter = self.wired(files={"a.txt": b"hi"})
        asyncio.run(backend.aedit("/proj/a.txt", "hi", "bye"))
        route, body = self._mutations(broker)[-1]
        assert route == "/v1/fs/edit"
        assert body["run_capability_context"] == RCX


class TestFailClosed(WriteBackendMixin):
    """A pre-image that cannot be made durable aborts the mutation (fail-closed)."""

    def test_put_failure_aborts_overwrite(self) -> None:
        broker = self.broker(files={"a.txt": b"OLD"})
        backend = self.backend(
            broker, store=FailingObjectStore(), emitter=RecordingEmitter(broker)
        )
        with pytest.raises(WorkspaceSnapshotError):
            asyncio.run(backend.awrite("/proj/a.txt", "NEW"))
        # The host file was never mutated.
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"OLD"
        assert self._mutations(broker) == []

    def test_put_failure_aborts_edit(self) -> None:
        broker = self.broker(files={"a.txt": b"OLD"})
        backend = self.backend(
            broker, store=FailingObjectStore(), emitter=RecordingEmitter(broker)
        )
        with pytest.raises(WorkspaceSnapshotError):
            asyncio.run(backend.aedit("/proj/a.txt", "OLD", "NEW"))
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"OLD"
        assert self._mutations(broker) == []

    def test_emit_failure_aborts_mutation(self) -> None:
        broker = self.broker(files={"a.txt": b"OLD"})
        store = FakeObjectStore()
        emitter = RecordingEmitter(broker=broker, raise_on_emit=True)
        backend = self.backend(broker, store=store, emitter=emitter)
        with pytest.raises(WorkspaceSnapshotError):
            asyncio.run(backend.awrite("/proj/a.txt", "NEW"))
        # Pre-image WAS persisted, but the mutation still did not commit.
        assert store.puts == [b"OLD"]
        assert self._mutations(broker) == []
        assert broker.grants[self.GRANT_RW].files["a.txt"] == b"OLD"


class TestModeAndSafety(WriteBackendMixin):
    """Read-only mounts / broker mode-gates surface safely, never a host path."""

    def test_read_only_mount_rejected_locally(self) -> None:
        backend, broker, store, _emitter = self.wired()
        result = asyncio.run(backend.awrite("/docs/ro.txt", "nope"))
        assert result.error is not None
        assert "/" not in result.error or "workspace" in result.error.lower()
        # Never reached the broker; no snapshot taken.
        assert store.puts == []
        assert self._mutations(broker) == []

    def test_broker_permission_denied_surfaces_as_error(self) -> None:
        # Pin the writable grant as read-only in the run context so the broker
        # mode-gate rejects the create (no pre-image to snapshot for a create).
        broker = self.broker()
        broker.run_contexts[RCX][self.GRANT_RW] = "read_only"
        store = FakeObjectStore()
        backend = self.backend(broker, store=store, emitter=RecordingEmitter(broker))
        result = asyncio.run(backend.awrite("/proj/new.txt", "x"))
        assert result.error is not None
        assert "/proj" not in result.error  # safe message, no path echo

    def test_write_to_mount_root_is_rejected(self) -> None:
        backend, broker, _store, _emitter = self.wired()
        result = asyncio.run(backend.awrite("/proj/", "x"))
        assert result.error is not None
        assert self._mutations(broker) == []


class TestRunContextRelease(WriteBackendMixin):
    """``aclose`` releases the pinned run context (``/v1/runs/end``)."""

    def test_aclose_releases_context(self) -> None:
        backend, broker, _store, _emitter = self.wired()
        assert RCX in broker.run_contexts
        asyncio.run(backend.aclose())
        assert RCX not in broker.run_contexts
        assert backend.run_capability_context is None

    def test_aclose_is_idempotent(self) -> None:
        backend, _broker, _store, _emitter = self.wired()
        asyncio.run(backend.aclose())
        asyncio.run(backend.aclose())  # no raise
