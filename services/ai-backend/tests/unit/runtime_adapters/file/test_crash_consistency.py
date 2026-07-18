"""Crash-consistency matrix: kill the writer at every append step.

The file store is a **single-writer** desktop adapter whose durability contract
is Claude-Code-shaped: append a validated JSON line, ``fsync`` when the write
must survive a crash, ignore a torn trailing line on load. This suite exercises
each residue state a process-kill can leave on disk (AC2 "Crash-injection
tests") and pins the load-bearing invariant: **the store either recovers the
correct pre-crash state or fails closed with a typed error — it never silently
truncates history.**

Covered kill points:

* before the line write (nothing acknowledged, prior state intact);
* after a partial (torn) trailing line (dropped, prior records survive);
* after the line + ``fsync`` but before the SQLite projection (JSONL is
  canonical → reopen rebuilds the projection and the record is visible);
* during / after a SQLite commit that leaves a **torn index file** (the
  disposable catalog is discarded and rebuilt from JSONL — regression test for
  the fix that stops a corrupt index from bricking ``open()``);
* during an object ``put`` (temp → rename), leaving a ``.tmp`` and no blob under
  the digest path; and
* an interrupted ``conversation.json`` / state-ledger rewrite (leftover ``.tmp``
  ignored, the committed file wins).

After each simulated crash the store is reopened from a *fresh* instance against
the same root, and restart + catalog-rebuild is asserted to yield the correct
pre-crash state.
"""

from __future__ import annotations

import shutil

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._jsonl import JsonlCorruptionError
from runtime_adapters.file.object_store import FileObjectStore, ObjectStoreError
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_crash"
_USER = "user_crash"


class CrashHarnessMixin:
    """Store + coordinator setup and event seeding shared across crash tests."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    async def _seed_run(self, store: FileRuntimeApiStore):
        settings = self._settings()
        resolver = ModelConfigResolver(settings)
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=resolver,
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant"
            )
        )
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="Hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return conversation, run

    async def _append_events(self, store, *, run, conversation_id, count) -> None:
        for i in range(count):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=_ORG,
                    run_id=run.run_id,
                    conversation_id=conversation_id,
                    trace_id="trace_crash",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    summary=f"chunk-{i}",
                )
            )


class TestTornTrailingLineNeverTruncatesHistory(CrashHarnessMixin):
    """A crash mid-append leaves a torn tail; all committed records survive."""

    async def test_torn_tail_on_events_drops_only_the_tail(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await self._seed_run(store)
        await self._append_events(
            store, run=run, conversation_id=conversation.conversation_id, count=4
        )
        committed = len(
            await store.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        )
        await store.close()

        # Kill point: after fsync of N events, a partial N+1 line begins.
        events_path = store.layout.events_path(_ORG, conversation.conversation_id)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write('{"partial":"torn crash mid-append')  # no close, no newline

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        # Every acknowledged event is intact and sequence is contiguous.
        assert len(events) == committed
        assert [e.sequence_no for e in events] == list(range(1, committed + 1))
        await reopened.close()

    async def test_torn_tail_on_messages_and_runs_is_tolerated(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await self._seed_run(store)
        # A status update appends a second runs.jsonl line so a torn tail after
        # it still leaves a durable committed run row.
        await store.update_run_status(run_id=run.run_id, status=AgentRunStatus.RUNNING)
        messages_before = await store.list_messages(
            org_id=_ORG, conversation_id=conversation.conversation_id, limit=50
        )
        await store.close()

        for path in (
            store.layout.messages_path(_ORG, conversation.conversation_id),
            store.layout.runs_path(_ORG, conversation.conversation_id),
        ):
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"torn":true')  # torn tail

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        messages_after = await reopened.list_messages(
            org_id=_ORG, conversation_id=conversation.conversation_id, limit=50
        )
        assert [m.message_id for m in messages_after] == [
            m.message_id for m in messages_before
        ]
        got_run = await reopened.get_run(org_id=_ORG, run_id=run.run_id)
        assert got_run is not None
        assert got_run.status == AgentRunStatus.RUNNING
        await reopened.close()


class TestInteriorCorruptionFailsClosed(CrashHarnessMixin):
    """Interior corruption (committed data after a bad line) never truncates."""

    async def test_interior_corruption_raises_not_truncates(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await self._seed_run(store)
        await self._append_events(
            store, run=run, conversation_id=conversation.conversation_id, count=3
        )
        await store.close()

        # Corrupt an interior line: a malformed line with valid records after it.
        events_path = store.layout.events_path(_ORG, conversation.conversation_id)
        lines = events_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 3
        lines[0] = "{ this is not valid json"
        events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        reopened = FileRuntimeApiStore(root)
        # Fails closed with a typed error rather than returning a truncated
        # prefix — the store refuses to silently drop the committed suffix.
        with pytest.raises(JsonlCorruptionError):
            await reopened.open()


class TestProjectionCrashRebuildsFromJsonl(CrashHarnessMixin):
    """JSONL is canonical: a lost or torn SQLite projection is rebuilt."""

    async def test_line_committed_before_projection_is_recovered(
        self, tmp_path
    ) -> None:
        # Kill point: after the JSONL line + fsync, before the SQLite insert.
        # Simulate by appending a durable event line directly to events.jsonl
        # (bypassing the index), then reopening: the rebuild must surface it.
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await self._seed_run(store)
        await self._append_events(
            store, run=run, conversation_id=conversation.conversation_id, count=2
        )
        before = await store.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        # Craft the next envelope as it would land on disk, append only to JSONL.
        next_seq = before[-1].sequence_no + 1
        orphan = before[-1].model_copy(
            update={"sequence_no": next_seq, "summary": "committed-but-unindexed"}
        )
        events_path = store.layout.events_path(_ORG, conversation.conversation_id)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(orphan.model_dump_json() + "\n")
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        assert [e.sequence_no for e in events] == list(range(1, next_seq + 1))
        assert events[-1].summary == "committed-but-unindexed"
        await reopened.close()

    async def test_torn_index_file_is_discarded_and_rebuilt(self, tmp_path) -> None:
        # Kill point: a crash *during* the SQLite commit leaves a torn index
        # file. The catalog is disposable, so a fresh open must discard it and
        # rebuild from JSONL rather than fail — regression for the bricking bug.
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await self._seed_run(store)
        await self._append_events(
            store, run=run, conversation_id=conversation.conversation_id, count=3
        )
        golden = [
            e.model_dump(mode="json")
            for e in await store.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        ]
        await store.close()

        # Torn/garbage index (not a valid SQLite database) + orphaned sidecars.
        index_path = store.layout.index_db_path
        index_path.write_bytes(b"torn sqlite commit \x00\x01\x02 not a database" * 32)
        for suffix in ("-wal", "-shm"):
            index_path.with_name(index_path.name + suffix).write_bytes(b"garbage")

        reopened = FileRuntimeApiStore(root)
        await reopened.open()  # must NOT raise — corrupt index is disposable
        rebuilt = [
            e.model_dump(mode="json")
            for e in await reopened.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
        ]
        assert rebuilt == golden
        got_conv = await reopened.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conversation.conversation_id
        )
        assert got_conv is not None
        await reopened.close()

    async def test_deleted_index_dir_rebuilds(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, run = await self._seed_run(store)
        await self._append_events(
            store, run=run, conversation_id=conversation.conversation_id, count=2
        )
        latest = await store.get_latest_sequence(run_id=run.run_id)
        await store.close()

        shutil.rmtree(root / "index")
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        assert await reopened.get_latest_sequence(run_id=run.run_id) == latest
        await reopened.close()


class TestObjectPutCrash:
    """A half-written object leaves a .tmp and no blob under the digest path."""

    def _object_store(self, tmp_path) -> FileObjectStore:
        from runtime_adapters.file._paths import FileStoreLayout

        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        return FileObjectStore(layout)

    def test_interrupted_put_never_exposes_a_half_written_blob(self, tmp_path) -> None:
        import hashlib

        from runtime_adapters.file._paths import FileStoreLayout

        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        store = FileObjectStore(layout)

        data = b"a large tool result that was mid-write when the process died"
        digest = hashlib.sha256(data).hexdigest()
        target = layout.object_path(digest)
        # Simulate a crash during put(): only the .tmp (partial bytes) exists.
        FileStoreLayout.ensure_dir(target.parent)
        target.with_name(target.name + ".tmp").write_bytes(data[:10])

        # No blob is readable under the digest path — fails closed, not partial.
        assert not store.exists(digest)
        assert store.write_in_flight(digest) is True
        with pytest.raises(ObjectStoreError):
            store.get(digest)
        # The digest is not advertised as present.
        assert digest not in store.iter_digests()
        # Deletion refuses to touch an in-flight write.
        assert store.delete(digest) is False

        # A completing put of the same bytes overwrites the stale .tmp and yields
        # a verified blob; no duplicate and no corruption.
        ref = store.put(data)
        assert ref.sha256 == digest
        assert store.get(ref) == data
        assert store.write_in_flight(digest) is False


class TestInterruptedRewriteIgnoresLeftoverTmp(CrashHarnessMixin):
    """conversation.json / state rewrites are temp+rename; a torn .tmp is inert."""

    async def test_leftover_conversation_json_tmp_is_ignored(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation, _run = await self._seed_run(store)
        await store.close()

        meta_path = store.layout.conversation_meta_path(
            _ORG, conversation.conversation_id
        )
        # Crash before os.replace: a torn .tmp sits next to the committed file.
        meta_path.with_name(meta_path.name + ".tmp").write_text(
            '{"conversation_id": "TORN', encoding="utf-8"
        )

        reopened = FileRuntimeApiStore(root)
        await reopened.open()  # reads conversation.json by name; .tmp is inert
        got = await reopened.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conversation.conversation_id
        )
        assert got is not None
        assert got.conversation_id == conversation.conversation_id
        await reopened.close()
