"""Undo for the agent's writes to the user's real disk.

These tests compose the PRODUCTION stack the way
``factory._host_default_backend`` does — ``HostFilesystemRules`` +
``FilesystemBackend(virtual_mode=False)`` + ``HostFilesystemFloor`` — and drive
the REAL deepagents ``write_file`` / ``edit_file`` tools against a real
temporary directory, exactly as ``test_host_floor.py`` does.

That composition is the point. Asserting on a hand-called ``floor.write()``
would prove our own predicate to ourselves; a capture that never fires because
the model's tool takes a different path through the backend is precisely the
"landed but not wired" failure this journal exists to make impossible. So the
bytes on disk after a real tool call are what every assertion here reads.

The durable half is the same pair the desktop composes: ``FileObjectStore`` for
the pre-image bytes and ``StateLedger`` for the records.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import (
    FilesystemMiddleware,
    FilesystemPermission,
)

from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostFilesystemRules,
)
from agent_runtime.capabilities.desktop.host_floor import HostFilesystemFloor
from agent_runtime.capabilities.desktop.write_journal import (
    HostWriteJournal,
    HostWriteKind,
    HostWriteRecord,
    HostWriteReverter,
    RevertStatus,
    path_within,
)
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.host_write_journal_store import FileHostWriteJournalStore
from runtime_adapters.file.object_store import FileObjectStore

ORG = "org-acme"
CONVERSATION = "conv-1"
RUN = "run-1"


class JournalStackMixin:
    """The desktop composition: real tools over a floor that captures."""

    @staticmethod
    def store(tmp_path: Path) -> FileHostWriteJournalStore:
        """The durable journal, rooted where the file store lives."""

        layout = FileStoreLayout(tmp_path / "store")
        return FileHostWriteJournalStore(layout, FileObjectStore(layout))

    @staticmethod
    def journal(store: FileHostWriteJournalStore, **kwargs: Any) -> HostWriteJournal:
        return HostWriteJournal(
            store, org_id=ORG, conversation_id=CONVERSATION, run_id=RUN, **kwargs
        )

    @classmethod
    def middleware(cls, root: Path, journal: HostWriteJournal) -> FilesystemMiddleware:
        granted = (GrantedRoot(path=str(root), writable=True),)
        return FilesystemMiddleware(
            backend=HostFilesystemFloor(
                FilesystemBackend(virtual_mode=False),
                roots=granted,
                journal=journal,
            ),
            _permissions=[
                FilesystemPermission(**rule)  # type: ignore[arg-type]
                for rule in HostFilesystemRules.build(granted)
            ],
        )

    @staticmethod
    def call(
        middleware: FilesystemMiddleware,
        name: str,
        *,
        tool_call_id: str = "call-1",
        **kwargs: Any,
    ) -> str:
        """Invoke one real filesystem tool under a bound tool-call identity.

        The identity binding is what makes a single tool call addressable; the
        tool is the real one the model calls, not a stand-in.
        """

        tool = next(item for item in middleware.tools if item.name == name)
        identity = RuntimeToolCallIdentity(
            run_id=RUN,
            snapshot_id="snapshot-1",
            execution_scope="supervisor",
            model_turn=1,
            model_tool_call_id=tool_call_id,
            operation_id="operation-1",
            control_call_id="runtime-control:test",
        )
        with RuntimeCallContext.bind(identity):
            message = tool.func(  # type: ignore[union-attr]
                runtime=SimpleNamespace(tool_call_id=tool_call_id), **kwargs
            )
        return str(message.content)

    @staticmethod
    def records(store: FileHostWriteJournalStore) -> tuple[HostWriteRecord, ...]:
        return store.records_for_run(org_id=ORG, run_id=RUN)


class TestCaptureAndRevert(JournalStackMixin):
    """The core promise: what the agent wrote can be put back."""

    def test_revert_restores_byte_identical_prior_content(self, tmp_path):
        """An edit through the real tool is undone to the original bytes."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "notes.md"
        original = "the original line\nsecond line\n"
        target.write_text(original)

        store = self.store(tmp_path)
        middleware = self.middleware(root, self.journal(store))
        self.call(
            middleware,
            "write_file",
            file_path=str(target),
            content="the agent clobbered this",
        )
        assert target.read_text() == "the agent clobbered this"

        records = self.records(store)
        assert [record.kind for record in records] == [HostWriteKind.MODIFIED]
        outcomes = HostWriteReverter(store).revert(records)

        assert target.read_bytes() == original.encode()
        assert [outcome.status for outcome in outcomes] == [RevertStatus.RESTORED]

    def test_revert_of_one_tool_call_leaves_a_later_write_intact(self, tmp_path):
        """Undoing one call must not rewind the rest of the turn."""

        root = tmp_path / "Projects"
        root.mkdir()
        bad = root / "bad.md"
        good = root / "good.md"
        bad.write_text("keep me\n")
        good.write_text("original good\n")

        store = self.store(tmp_path)
        middleware = self.middleware(root, self.journal(store))
        self.call(
            middleware,
            "write_file",
            file_path=str(bad),
            content="mistake",
            tool_call_id="call-bad",
        )
        self.call(
            middleware,
            "write_file",
            file_path=str(good),
            content="deliberate later work",
            tool_call_id="call-good",
        )

        reverter = HostWriteReverter(store)
        selection = reverter.select(self.records(store), tool_call_id="call-bad")
        outcomes = reverter.revert(selection)

        assert [outcome.path for outcome in outcomes] == [str(bad)]
        assert bad.read_text() == "keep me\n"
        # The untouched half of the turn survives, which is the whole reason
        # capture is keyed to a tool call rather than to a run.
        assert good.read_text() == "deliberate later work"

    def test_repeated_writes_in_one_selection_restore_the_oldest(self, tmp_path):
        """Two writes to one file collapse to the content preceding both."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "notes.md"
        target.write_text("generation zero\n")

        store = self.store(tmp_path)
        middleware = self.middleware(root, self.journal(store))
        self.call(middleware, "write_file", file_path=str(target), content="one")
        self.call(middleware, "write_file", file_path=str(target), content="two")

        reverter = HostWriteReverter(store)
        reverter.revert(reverter.select(self.records(store)))

        assert target.read_text() == "generation zero\n"

    def test_a_created_file_is_reverted_by_removing_it(self, tmp_path):
        """Undo of a file that did not exist is a delete, not an empty file."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "new.md"

        store = self.store(tmp_path)
        middleware = self.middleware(root, self.journal(store))
        self.call(middleware, "write_file", file_path=str(target), content="brand new")
        assert target.exists()

        records = self.records(store)
        assert [record.kind for record in records] == [HostWriteKind.CREATED]
        outcomes = HostWriteReverter(store).revert(records)

        assert not target.exists()
        assert [outcome.status for outcome in outcomes] == [RevertStatus.REMOVED]

    def test_a_delete_is_revertible(self, tmp_path):
        """The floor now guards delete, so a removal is undoable."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "doomed.md"
        target.write_text("please come back\n")

        store = self.store(tmp_path)
        journal = self.journal(store)
        floor = HostFilesystemFloor(
            FilesystemBackend(virtual_mode=False),
            roots=(GrantedRoot(path=str(root), writable=True),),
            journal=journal,
        )
        floor.delete(str(target))
        assert not target.exists()

        records = self.records(store)
        assert [record.kind for record in records] == [HostWriteKind.DELETED]
        HostWriteReverter(store).revert(records)

        assert target.read_text() == "please come back\n"

    def test_a_binary_file_round_trips(self, tmp_path):
        """Pre-images are bytes, so no encoding can corrupt them."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "logo.png"
        blob = bytes(range(256)) * 8 + b"\x00\xff\xfe\x80"
        target.write_bytes(blob)

        store = self.store(tmp_path)
        journal = self.journal(store)
        floor = HostFilesystemFloor(
            FilesystemBackend(virtual_mode=False),
            roots=(GrantedRoot(path=str(root), writable=True),),
            journal=journal,
        )
        floor.write(str(target), "clobbered by text")

        HostWriteReverter(store).revert(self.records(store))

        assert target.read_bytes() == blob

    def test_an_oversized_file_is_recorded_but_not_revertible(self, tmp_path):
        """An honest "cannot undo this one" beats a missing row."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "huge.bin"
        target.write_bytes(b"x" * 4096)

        store = self.store(tmp_path)
        middleware = self.middleware(root, self.journal(store, max_capture_bytes=128))
        self.call(middleware, "write_file", file_path=str(target), content="small now")

        (record,) = self.records(store)
        assert record.kind is HostWriteKind.MODIFIED
        assert record.prior_sha256 is None
        assert record.revertible is False

        outcomes = HostWriteReverter(store).revert((record,))
        assert [outcome.status for outcome in outcomes] == [RevertStatus.NOT_REVERTIBLE]
        # The write stands: we could not restore it, and we did not pretend to.
        assert target.read_text() == "small now"


class TestRevertCannotEscapeTheFloor(JournalStackMixin):
    """A revert writes to disk, so it must be bounded by the same grant."""

    def _record(self, path: str, root: str, digest: str) -> HostWriteRecord:
        return HostWriteRecord(
            entry_id="e1",
            org_id=ORG,
            conversation_id=CONVERSATION,
            run_id=RUN,
            sequence=1,
            path=path,
            authorized_root=root,
            kind=HostWriteKind.MODIFIED,
            prior_sha256=digest,
            prior_size=4,
            captured_at=datetime.now(timezone.utc),
        )

    def test_a_path_outside_its_authorized_root_is_refused(self, tmp_path):
        """The record's own root bounds the restore, not the caller."""

        root = tmp_path / "Projects"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "victim.txt"
        outside.parent.mkdir()
        outside.write_text("untouched\n")

        store = self.store(tmp_path)
        digest = store.put_blob(b"evil")
        outcomes = HostWriteReverter(store).revert(
            (self._record(str(outside), str(root), digest),)
        )

        assert [outcome.status for outcome in outcomes] == [RevertStatus.REFUSED]
        assert outside.read_text() == "untouched\n"

    def test_a_sibling_prefix_root_is_refused(self, tmp_path):
        """``/a/Projects`` must never admit ``/a/ProjectsSecret``."""

        store = self.store(tmp_path)
        digest = store.put_blob(b"evil")
        secret = tmp_path / "ProjectsSecret" / "keys.txt"
        secret.parent.mkdir()
        secret.write_text("untouched\n")

        outcomes = HostWriteReverter(store).revert(
            (self._record(str(secret), str(tmp_path / "Projects"), digest),)
        )

        assert [outcome.status for outcome in outcomes] == [RevertStatus.REFUSED]
        assert secret.read_text() == "untouched\n"

    def test_a_symlink_planted_after_capture_is_refused(self, tmp_path):
        """Following it would write the bytes somewhere nobody granted."""

        root = tmp_path / "Projects"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("untouched\n")
        link = root / "innocent.txt"
        link.symlink_to(outside)

        store = self.store(tmp_path)
        digest = store.put_blob(b"evil")
        outcomes = HostWriteReverter(store).revert(
            (self._record(str(link), str(root), digest),)
        )

        assert [outcome.status for outcome in outcomes] == [RevertStatus.REFUSED]
        assert outside.read_text() == "untouched\n"

    def test_a_traversal_in_the_record_is_refused(self, tmp_path):
        """``..`` is never admitted, matching the floor's own containment."""

        store = self.store(tmp_path)
        digest = store.put_blob(b"evil")
        escaped = f"{tmp_path / 'Projects'}/../escaped.txt"

        outcomes = HostWriteReverter(store).revert(
            (self._record(escaped, str(tmp_path / "Projects"), digest),)
        )

        assert [outcome.status for outcome in outcomes] == [RevertStatus.REFUSED]
        assert not (tmp_path / "escaped.txt").exists()

    def test_containment_is_the_predicate_the_floor_itself_uses(self):
        """One spelling of the rule, shared, so the two cannot diverge."""

        floor = HostFilesystemFloor(
            object(), roots=(GrantedRoot(path="/Users/ada/Projects"),)
        )
        for path, root, expected in (
            ("/Users/ada/Projects/a.md", "/Users/ada/Projects", True),
            ("/Users/ada/ProjectsSecret/a.md", "/Users/ada/Projects", False),
            ("/Users/ada/Projects/../b.md", "/Users/ada/Projects", False),
            ("relative.md", "/Users/ada/Projects", False),
        ):
            assert path_within(path, root) is expected
            assert floor._within(path, root) is expected


class TestJournalCaptureBoundaries(JournalStackMixin):
    """The journal only ever holds writes the floor actually admitted."""

    def test_a_refused_write_is_never_journalled(self, tmp_path):
        """Capture runs after the verdict, so a refusal leaves no record."""

        root = tmp_path / "Projects"
        root.mkdir()
        forbidden = tmp_path / "Secrets" / "id_rsa"
        forbidden.parent.mkdir()
        forbidden.write_text("private\n")

        store = self.store(tmp_path)
        floor = HostFilesystemFloor(
            FilesystemBackend(virtual_mode=False),
            roots=(GrantedRoot(path=str(root), writable=True),),
            journal=self.journal(store),
        )
        result = floor.write(str(forbidden), "clobbered")

        assert result.error is not None
        assert forbidden.read_text() == "private\n"
        assert self.records(store) == ()

    def test_a_read_only_root_is_refused_and_not_journalled(self, tmp_path):
        """A non-writable grant admits nothing, so it records nothing."""

        root = tmp_path / "Reference"
        root.mkdir()
        target = root / "spec.md"
        target.write_text("read only\n")

        store = self.store(tmp_path)
        floor = HostFilesystemFloor(
            FilesystemBackend(virtual_mode=False),
            roots=(GrantedRoot(path=str(root), writable=False),),
            journal=self.journal(store),
        )
        result = floor.write(str(target), "clobbered")

        assert result.error is not None
        assert target.read_text() == "read only\n"
        assert self.records(store) == ()

    def test_the_authorized_root_recorded_is_the_one_that_admitted(self, tmp_path):
        """The record carries the floor's own answer, not a re-derivation."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "notes.md"
        target.write_text("before\n")

        store = self.store(tmp_path)
        middleware = self.middleware(root, self.journal(store))
        self.call(middleware, "write_file", file_path=str(target), content="after")

        (record,) = self.records(store)
        assert record.authorized_root == str(root)
        assert record.tool_call_id == "call-1"
        assert record.org_id == ORG and record.run_id == RUN

    def test_a_capture_failure_never_fails_the_write(self, tmp_path):
        """Losing an undo beats losing the user's task."""

        root = tmp_path / "Projects"
        root.mkdir()
        target = root / "notes.md"
        target.write_text("before\n")

        class ExplodingStore:
            def put_blob(self, data: bytes) -> str:
                raise OSError("disk full")

            def append(self, record: HostWriteRecord) -> None:
                raise OSError("disk full")

        middleware = self.middleware(
            root,
            HostWriteJournal(
                ExplodingStore(),  # type: ignore[arg-type]
                org_id=ORG,
                conversation_id=CONVERSATION,
                run_id=RUN,
            ),
        )
        self.call(middleware, "write_file", file_path=str(target), content="after")

        assert target.read_text() == "after"


class TestRetention(JournalStackMixin):
    """Bounded history: captures expire and their bytes go with them."""

    def _record(self, store, path: str, age_days: int, data: bytes) -> str:
        """Append one aged record and return the digest of its pre-image."""

        digest = store.put_blob(data)
        store.append(
            HostWriteRecord(
                entry_id=f"e{age_days}",
                org_id=ORG,
                conversation_id=CONVERSATION,
                run_id=RUN,
                sequence=age_days,
                path=path,
                authorized_root="/Users/ada/Projects",
                kind=HostWriteKind.MODIFIED,
                prior_sha256=digest,
                prior_size=len(data),
                captured_at=datetime.now(timezone.utc) - timedelta(days=age_days),
            )
        )
        return digest

    def test_prune_drops_expired_records_and_their_blobs(self, tmp_path):
        store = self.store(tmp_path)
        stale_digest = self._record(store, "/Users/ada/Projects/old.md", 30, b"old")
        fresh_digest = self._record(store, "/Users/ada/Projects/new.md", 1, b"new")

        dropped = store.prune(before=datetime.now(timezone.utc) - timedelta(days=7))

        assert dropped == 1
        assert [record.path for record in self.records(store)] == [
            "/Users/ada/Projects/new.md"
        ]
        # The bytes are gone too, or retention would bound the index and not
        # the disk it was meant to bound.
        assert store._objects.exists(stale_digest) is False
        assert store.get_blob(fresh_digest) == b"new"

    def test_prune_keeps_a_blob_a_surviving_record_still_references(self, tmp_path):
        """Content addressing means two records can share one blob."""

        store = self.store(tmp_path)
        shared = b"identical prior content"
        self._record(store, "/Users/ada/Projects/old.md", 30, shared)
        self._record(store, "/Users/ada/Projects/new.md", 1, shared)

        store.prune(before=datetime.now(timezone.utc) - timedelta(days=7))

        (survivor,) = self.records(store)
        assert survivor.prior_sha256 is not None
        assert store.get_blob(survivor.prior_sha256) == shared

    def test_prune_with_nothing_expired_is_a_no_op(self, tmp_path):
        store = self.store(tmp_path)
        self._record(store, "/Users/ada/Projects/new.md", 1, b"new bytes")

        assert store.prune(before=datetime.now(timezone.utc) - timedelta(days=7)) == 0
        assert len(self.records(store)) == 1


class TestJournalStoreScoping(JournalStackMixin):
    """One ledger, many runs — a caller sees only its own."""

    def test_records_are_scoped_by_org_and_run(self, tmp_path):
        store = self.store(tmp_path)
        for org, run in (
            ("org-acme", "run-1"),
            ("org-acme", "run-2"),
            ("org-b", "run-1"),
        ):
            store.append(
                HostWriteRecord(
                    entry_id=f"{org}-{run}",
                    org_id=org,
                    conversation_id=CONVERSATION,
                    run_id=run,
                    sequence=1,
                    path=f"/Users/ada/Projects/{org}-{run}.md",
                    authorized_root="/Users/ada/Projects",
                    kind=HostWriteKind.CREATED,
                    captured_at=datetime.now(timezone.utc),
                )
            )

        assert [record.entry_id for record in self.records(store)] == ["org-acme-run-1"]
