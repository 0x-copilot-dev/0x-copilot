"""Corruption diagnosis, salvage-export, and guarded repair (``repair.py``).

The live file store fails closed on interior JSONL corruption — a corrupt
conversation becomes read-only and the store refuses to open. These tests pin
the offline recovery tool that turns that stuck state back into readable data:

* **diagnose** identifies a truncated final line, an interior malformed line, a
  dangling ``objects/sha256/`` reference, and an orphan blob — without ever
  raising on the corrupt input;
* **salvage_export** recovers the good prefix verbatim (never fabricating a
  record) and reports exactly what it dropped and why;
* **rebuild_catalog** restores queryability of the disposable SQLite index from
  the recoverable JSONL truth;
* **quarantine_corrupt_tail** *moves* an unrecoverable tail aside (never
  hard-deletes) so the conversation reads cleanly again.

Fixtures write JSONL directly through :class:`FileStoreLayout` so the exact
corruption shapes can be constructed byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

from runtime_adapters.file._catalog_index import CatalogIndex
from runtime_adapters.file._jsonl import JsonlCorruptionError, JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore
from runtime_adapters.file.repair import (
    JsonlLineKind,
    StoreRepair,
)

_ORG = "org-repair"
_USER = "user-repair"


class StoreFixtureMixin:
    """Build on-disk conversation folders with precise corruption shapes."""

    _RUN_ID = "run-1"

    def _layout(self, root: Path) -> FileStoreLayout:
        layout = FileStoreLayout(root)
        layout.ensure_scaffold()
        return layout

    def _meta(self, conversation_id: str) -> dict:
        return {
            "conversation_id": conversation_id,
            "org_id": _ORG,
            "user_id": _USER,
            "status": "active",
            "updated_at": "2026-01-01T00:00:00Z",
            "title": "recovery fixture",
        }

    def _event(self, seq: int, conversation_id: str, *, ref: str | None = None) -> dict:
        doc: dict = {
            "run_id": self._RUN_ID,
            "conversation_id": conversation_id,
            "sequence_no": seq,
            "summary": f"chunk-{seq}",
        }
        if ref is not None:
            doc["payload"] = {"reference": f"/large_tool_results/{ref}"}
        return doc

    def _message(self, idx: int, conversation_id: str) -> dict:
        return {
            "message_id": f"msg-{idx}",
            "org_id": _ORG,
            "conversation_id": conversation_id,
            "created_at": f"2026-01-01T00:00:0{idx}Z",
            "role": "user",
            "content_text": f"hello {idx}",
        }

    def _run(self, conversation_id: str) -> dict:
        return {
            "run_id": self._RUN_ID,
            "org_id": _ORG,
            "conversation_id": conversation_id,
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
        }

    def _write_conversation(
        self,
        layout: FileStoreLayout,
        conversation_id: str,
        *,
        events_text: str,
        messages: list[dict] | None = None,
    ) -> Path:
        conv_dir = layout.conversation_dir(_ORG, conversation_id)
        FileStoreLayout.ensure_dir(conv_dir)
        JsonlIo.rewrite_json(
            conv_dir / FileStoreLayout.CONVERSATION_META, self._meta(conversation_id)
        )
        (conv_dir / FileStoreLayout.EVENTS_FILE).write_text(
            events_text, encoding="utf-8"
        )
        JsonlIo.append_lines(
            conv_dir / FileStoreLayout.RUNS_FILE, [self._run(conversation_id)]
        )
        if messages:
            JsonlIo.append_lines(conv_dir / FileStoreLayout.MESSAGES_FILE, messages)
        return conv_dir

    def _lines(self, docs: list[dict]) -> str:
        return "".join(JsonlIo.dumps(doc) + "\n" for doc in docs)

    def _healthy_store(self, root: Path, conversation_id: str = "conv-ok") -> Path:
        layout = self._layout(root)
        events = self._lines([self._event(seq, conversation_id) for seq in range(1, 4)])
        return self._write_conversation(
            layout,
            conversation_id,
            events_text=events,
            messages=[self._message(i, conversation_id) for i in (1, 2)],
        )


class TestDiagnose(StoreFixtureMixin):
    def test_healthy_store_is_healthy(self, tmp_path) -> None:
        self._healthy_store(tmp_path)
        diagnosis = StoreRepair(tmp_path).diagnose()
        assert diagnosis.healthy
        assert diagnosis.total_dangling_refs == 0
        assert diagnosis.orphan_objects == ()
        (conv,) = diagnosis.conversations
        assert conv.healthy
        assert all(stream.malformed_kind is None for stream in conv.streams)

    def test_truncated_final_line_is_torn_tail(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        good = self._lines([self._event(1, "conv-torn"), self._event(2, "conv-torn")])
        # Crash mid-append: a partial trailing line with no newline.
        self._write_conversation(
            layout, "conv-torn", events_text=good + '{"sequence_no": 3, "sum'
        )
        diagnosis = StoreRepair(tmp_path).diagnose()
        (conv,) = diagnosis.conversations
        events_stream = self._events_stream(conv)
        assert events_stream.malformed_kind is JsonlLineKind.TORN_TAIL
        assert events_stream.recovered_records == 2
        # A torn tail is benign — the conversation is still healthy.
        assert conv.healthy

    def test_interior_corruption_is_flagged(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        events = (
            self._lines([self._event(1, "conv-bad")])
            + "{ this is not json\n"
            + self._lines([self._event(3, "conv-bad")])
        )
        self._write_conversation(layout, "conv-bad", events_text=events)
        diagnosis = StoreRepair(tmp_path).diagnose()
        (conv,) = diagnosis.conversations
        events_stream = self._events_stream(conv)
        assert events_stream.malformed_kind is JsonlLineKind.INTERIOR_CORRUPT
        assert events_stream.malformed_line == 2
        assert events_stream.recovered_records == 1  # only the good prefix
        assert events_stream.salvageable_tail_lines == 1  # the later valid line
        assert conv.needs_repair
        assert not conv.healthy

    def test_dangling_object_ref_is_reported(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        missing = "a" * 64  # referenced but never stored
        events = self._lines([self._event(1, "conv-ref", ref=missing)])
        self._write_conversation(layout, "conv-ref", events_text=events)
        diagnosis = StoreRepair(tmp_path).diagnose()
        (conv,) = diagnosis.conversations
        assert missing in conv.dangling_object_refs
        assert diagnosis.total_dangling_refs == 1
        assert not conv.healthy

    def test_present_object_ref_is_not_dangling(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        ref = FileObjectStore(layout).put(b"real blob").sha256
        events = self._lines([self._event(1, "conv-ref-ok", ref=ref)])
        self._write_conversation(layout, "conv-ref-ok", events_text=events)
        diagnosis = StoreRepair(tmp_path).diagnose()
        (conv,) = diagnosis.conversations
        assert conv.dangling_object_refs == ()
        # The referenced blob is not an orphan either.
        assert ref not in diagnosis.orphan_objects
        assert diagnosis.healthy

    def test_orphan_object_is_reported(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        self._write_conversation(
            layout,
            "conv-plain",
            events_text=self._lines([self._event(1, "conv-plain")]),
        )
        orphan = FileObjectStore(layout).put(b"nobody references me").sha256
        diagnosis = StoreRepair(tmp_path).diagnose()
        assert orphan in diagnosis.orphan_objects
        assert not diagnosis.healthy

    @staticmethod
    def _events_stream(conv):
        return next(
            stream
            for stream in conv.streams
            if stream.relative_path.endswith(FileStoreLayout.EVENTS_FILE)
        )


class TestSalvageExport(StoreFixtureMixin):
    def test_recovers_good_prefix_and_reports_interior_drop(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        good_docs = [self._event(1, "conv-bad"), self._event(2, "conv-bad")]
        events = (
            self._lines(good_docs)
            + "{ not json at all\n"
            + self._lines([self._event(9, "conv-bad")])  # valid but post-corruption
        )
        conv_dir = self._write_conversation(layout, "conv-bad", events_text=events)

        dest = tmp_path / "bundle"
        report = StoreRepair(tmp_path).salvage_export(dest)

        # 2 good events + the 1 run record (messages stream absent here).
        assert report.records_recovered == 3
        assert report.salvageable_tail_records == 1
        (drop,) = report.dropped
        assert drop.reason is JsonlLineKind.INTERIOR_CORRUPT
        assert drop.from_line == 3

        # The recovered stream is the good prefix, byte-for-byte — never rebuilt.
        recovered = (
            dest / "conversations" / conv_dir.name / FileStoreLayout.EVENTS_FILE
        ).read_text(encoding="utf-8")
        assert list(JsonlIo.iter_lines(_as_path(recovered, tmp_path, "recovered"))) == (
            good_docs
        )
        # Post-corruption valid line is preserved in a labelled sidecar only.
        tail = (
            dest
            / "conversations"
            / conv_dir.name
            / (FileStoreLayout.EVENTS_FILE + ".recovered-tail")
        )
        assert tail.exists()
        # The bundle carries its own machine-readable report.
        assert (dest / "SALVAGE_REPORT.json").exists()

    def test_torn_tail_recovers_prefix(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        good_docs = [self._event(1, "conv-torn"), self._event(2, "conv-torn")]
        events = self._lines(good_docs) + '{"sequence_no": 3, "trunc'
        self._write_conversation(layout, "conv-torn", events_text=events)

        report = StoreRepair(tmp_path).salvage_export(tmp_path / "bundle")
        # 2 good events + the 1 run record; the torn final event line is dropped.
        assert report.records_recovered == 3
        (drop,) = report.dropped
        assert drop.reason is JsonlLineKind.TORN_TAIL
        assert drop.count == 1

    def test_copies_referenced_objects_and_reports_missing(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        store = FileObjectStore(layout)
        present = store.put(b"kept blob").sha256
        missing = "b" * 64
        events = self._lines(
            [
                self._event(1, "conv-obj", ref=present),
                self._event(2, "conv-obj", ref=missing),
            ]
        )
        self._write_conversation(layout, "conv-obj", events_text=events)

        dest = tmp_path / "bundle"
        report = StoreRepair(tmp_path).salvage_export(dest)
        assert report.objects_copied == 1
        assert missing in report.missing_objects
        copied = dest / "objects" / "sha256" / present[:2] / present
        assert copied.read_bytes() == b"kept blob"


class TestRebuildCatalog(StoreFixtureMixin):
    def test_rebuild_restores_queryability_over_recoverable_prefix(
        self, tmp_path
    ) -> None:
        layout = self._layout(tmp_path)
        # Interior corruption after two good events: the live store would refuse
        # to open, but the catalog is rebuilt from the recoverable prefix.
        events = (
            self._lines([self._event(1, "conv-idx"), self._event(2, "conv-idx")])
            + "{ corrupt interior\n"
            + self._lines([self._event(3, "conv-idx")])
        )
        self._write_conversation(
            layout,
            "conv-idx",
            events_text=events,
            messages=[self._message(1, "conv-idx"), self._message(2, "conv-idx")],
        )

        StoreRepair(tmp_path).rebuild_catalog()

        index = CatalogIndex(layout.index_db_path)
        index.connect()
        try:
            conversations = index.list_conversations(
                org_id=_ORG,
                user_id=_USER,
                limit=10,
                include_archived=True,
                include_deleted=True,
            )
            messages = index.list_messages(
                org_id=_ORG, conversation_id="conv-idx", limit=10, include_deleted=True
            )
            events_indexed = index.list_events_after(
                run_id=self._RUN_ID, after_sequence=0
            )
        finally:
            index.close()

        assert len(conversations) == 1
        assert len(messages) == 2
        # Only the good-prefix events are indexed; the corrupt tail is excluded.
        assert len(events_indexed) == 2

    def test_rebuild_on_healthy_store_indexes_everything(self, tmp_path) -> None:
        self._healthy_store(tmp_path, "conv-ok")
        repair = StoreRepair(tmp_path)
        repair.rebuild_catalog()
        index = CatalogIndex(repair.layout.index_db_path)
        index.connect()
        try:
            events_indexed = index.list_events_after(
                run_id=self._RUN_ID, after_sequence=0
            )
        finally:
            index.close()
        assert len(events_indexed) == 3


class TestQuarantine(StoreFixtureMixin):
    def test_quarantine_moves_tail_and_makes_stream_readable(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        good_docs = [self._event(1, "conv-q"), self._event(2, "conv-q")]
        corrupt_line = "{ interior corruption here\n"
        events = (
            self._lines(good_docs)
            + corrupt_line
            + self._lines([self._event(3, "conv-q")])
        )
        conv_dir = self._write_conversation(layout, "conv-q", events_text=events)
        events_path = conv_dir / FileStoreLayout.EVENTS_FILE

        # Precondition: the live read path fails closed on this stream.
        try:
            list(JsonlIo.iter_lines(events_path))
            raised = False
        except JsonlCorruptionError:
            raised = True
        assert raised

        result = StoreRepair(tmp_path).quarantine_corrupt_tail(conv_dir)
        assert result.changed
        (moved,) = result.streams
        assert moved.good_prefix_records == 2

        # The canonical stream now reads cleanly as exactly the good prefix.
        assert list(JsonlIo.iter_lines(events_path)) == good_docs

    def test_quarantine_never_hard_deletes_the_corrupt_tail(self, tmp_path) -> None:
        layout = self._layout(tmp_path)
        corrupt_line = "{ interior corruption here"
        events = (
            self._lines([self._event(1, "conv-q2")])
            + corrupt_line
            + "\n"
            + self._lines([self._event(2, "conv-q2")])
        )
        conv_dir = self._write_conversation(layout, "conv-q2", events_text=events)

        StoreRepair(tmp_path).quarantine_corrupt_tail(conv_dir)

        corrupt_dir = conv_dir / ".corrupt"
        assert corrupt_dir.is_dir()
        sidecars = list(corrupt_dir.iterdir())
        assert len(sidecars) == 1
        # The moved-aside bytes still contain the original corrupt line verbatim —
        # nothing was destroyed, only relocated.
        sidecar_text = sidecars[0].read_text(encoding="utf-8")
        assert corrupt_line in sidecar_text
        assert '"sequence_no":2' in sidecar_text.replace(" ", "")

    def test_quarantine_is_noop_on_healthy_conversation(self, tmp_path) -> None:
        conv_dir = self._healthy_store(tmp_path, "conv-ok")
        result = StoreRepair(tmp_path).quarantine_corrupt_tail(conv_dir)
        assert not result.changed
        assert not (conv_dir / ".corrupt").exists()


def _as_path(text: str, tmp_path: Path, name: str) -> Path:
    """Materialize salvage-bundle text back to a file for JSONL re-parsing."""

    scratch = tmp_path / f"{name}.jsonl"
    scratch.write_text(text, encoding="utf-8")
    return scratch
