"""Postgres/in-memory -> file-store migration (AC2 §702).

The source is read *only* through the shared runtime store port, so an
in-memory source drives byte-identically the same migration code path a
Postgres source would (``InMemoryRuntimeApiStore`` and
``PostgresRuntimeApiStore`` implement the same port surface). These tests
therefore use in-memory as the source and a real file store as the
destination — postgres->file is not a different code path, just a different
port implementation on the read side, and the Postgres suite is skipped without
a live database anyway.

Coverage:

* full round-trip equality — conversations, messages, runs, events (with exact
  ``sequence_no``), and main vs subagent event streams;
* idempotent re-run — a second migration is an all-skip no-op with no duplicate
  records on disk;
* dry-run — reports what would migrate and writes nothing;
* verify — the equality pass catches an injected destination mismatch and fails
  loudly;
* objects — a file source's ``/large_tool_results/<sha256>`` blobs are copied
  byte-for-byte with their content-address preserved (the only case where
  separate object blobs exist, since the offload seam is file-store-only).
"""

from __future__ import annotations

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.migration import (
    MIGRATED,
    SKIPPED,
    MigrationScope,
    MigrationVerificationError,
    StoreMigrator,
)
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_mig"
_USER = "user_mig"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class _SeedMixin:
    """Seed a store (any port-compatible store) with conversations + records."""

    def _coordinators(self, store) -> ConversationCoordinator:
        settings = _settings()
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
        return ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )

    async def _seed_conversation(
        self,
        store,
        *,
        title: str,
        with_subagent: bool = True,
        object_content: str | None = None,
    ):
        """Create a conversation + run + a few main/subagent events."""

        coordinator = self._coordinators(store)
        conversation = await coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant", metadata={}
            )
        )
        cid = conversation.conversation_id
        run = await coordinator._run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=cid,
                org_id=_ORG,
                user_id=_USER,
                user_input=f"Hello from {title}",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )

        # A couple of main-stream events with monotonic sequence numbers.
        payload_ref = None
        if object_content is not None:
            payload_ref = FileOffloadWriter(store.object_store)(object_content)
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=run.run_id,
                conversation_id=cid,
                trace_id="trace_mig",
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                payload={"text": "thinking"},
            )
        )
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=run.run_id,
                conversation_id=cid,
                trace_id="trace_mig",
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload={
                    "tool_name": "web_search",
                    "call_id": "c1",
                    "status": "completed",
                    **({"output_ref": payload_ref} if payload_ref else {}),
                },
            )
        )

        if with_subagent:
            await store.append_event(
                RuntimeEventDraft(
                    org_id=_ORG,
                    run_id=run.run_id,
                    conversation_id=cid,
                    trace_id="trace_mig",
                    source=StreamEventSource.MODEL,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    task_id="task_research",
                    subagent_id="researcher",
                    payload={"text": "subagent step"},
                )
            )

        # An assistant message appended out-of-band, to exercise multi-message
        # conversations (messages carry the run_id the migrator derives runs from).
        assistant = await store.append_message(
            _assistant_message(cid=cid, run_id=run.run_id)
        )
        return conversation, run, assistant


def _assistant_message(*, cid: str, run_id: str):
    from runtime_api.schemas import MessageRecord

    return MessageRecord(
        conversation_id=cid,
        org_id=_ORG,
        run_id=run_id,
        role=MessageRole.ASSISTANT,
        content_text="Here is the answer.",
    )


async def _open_file_store(root) -> FileRuntimeApiStore:
    store = FileRuntimeApiStore(root)
    await store.open()
    return store


async def _open_memory_store() -> InMemoryRuntimeApiStore:
    store = InMemoryRuntimeApiStore()
    await store.open()
    return store


def _scope() -> MigrationScope:
    return MigrationScope(org_id=_ORG, user_id=_USER)


class TestRoundTrip(_SeedMixin):
    async def test_in_memory_to_file_round_trip_preserves_everything(
        self, tmp_path
    ) -> None:
        source = await _open_memory_store()
        conv_a, run_a, _ = await self._seed_conversation(source, title="A")
        conv_b, run_b, _ = await self._seed_conversation(source, title="B")
        dest = await _open_file_store(tmp_path / "dst")

        report = await StoreMigrator(source=source, dest=dest).migrate(verify=True)

        assert report.verified is True
        assert report.mismatches == ()
        assert report.conversations_total == 2
        assert report.conversations_migrated == 2
        assert report.conversations_skipped == 0
        assert report.events > 0

        # Both conversations are readable in the destination through the port.
        for conv, run in ((conv_a, run_a), (conv_b, run_b)):
            cid = conv.conversation_id
            dest_conv = await dest.get_conversation(
                org_id=_ORG, user_id=_USER, conversation_id=cid
            )
            # Compare against the source's CURRENT state (run creation mutates
            # updated_at / latest_run_* on the source conversation).
            src_conv = await source.get_conversation(
                org_id=_ORG, user_id=_USER, conversation_id=cid
            )
            assert dest_conv is not None and src_conv is not None
            assert dest_conv.model_dump_json() == src_conv.model_dump_json()

            src_events = list(source.events_by_run[run.run_id])
            dst_events = await dest.list_events_after(
                org_id=_ORG, run_id=run.run_id, after_sequence=0
            )
            assert [e.sequence_no for e in dst_events] == [
                e.sequence_no for e in src_events
            ]
            assert [e.model_dump_json() for e in dst_events] == [
                e.model_dump_json() for e in src_events
            ]
            # The subagent event (task_id set) survived and lands in its stream.
            assert any(e.task_id == "task_research" for e in dst_events)

        await source.close()
        await dest.close()

    async def test_subagent_stream_written_to_subagents_dir(self, tmp_path) -> None:
        source = await _open_memory_store()
        conv, run, _ = await self._seed_conversation(source, title="sub")
        dest = await _open_file_store(tmp_path / "dst")

        await StoreMigrator(source=source, dest=dest).migrate()

        sub_path = dest.layout.subagent_path(
            _ORG, conv.conversation_id, "task_research"
        )
        assert sub_path.exists(), "subagent events must route to subagents/<task>.jsonl"
        await source.close()
        await dest.close()


class TestIdempotency(_SeedMixin):
    async def test_rerun_skips_and_does_not_duplicate(self, tmp_path) -> None:
        source = await _open_memory_store()
        conv, run, _ = await self._seed_conversation(source, title="idem")
        dest = await _open_file_store(tmp_path / "dst")

        first = await StoreMigrator(source=source, dest=dest).migrate()
        assert first.conversations_migrated == 1
        assert all(o.status == MIGRATED for o in first.outcomes)

        events_after_first = await dest.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )

        second = await StoreMigrator(source=source, dest=dest).migrate(verify=True)
        assert second.conversations_migrated == 0
        assert second.conversations_skipped == 1
        assert all(o.status == SKIPPED for o in second.outcomes)
        assert second.verified is True

        # No duplicate records appended on the second pass.
        events_after_second = await dest.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        assert len(events_after_second) == len(events_after_first)
        messages = await dest.list_messages(
            org_id=_ORG, conversation_id=conv.conversation_id, limit=100
        )
        # user message + assistant message, exactly once each.
        assert len(messages) == 2

        # The on-disk events.jsonl holds exactly the main-stream events once —
        # subagent events (task_id set) live in their own subagents/ file, and
        # nothing is duplicated by the re-run.
        source_events = list(source.events_by_run[run.run_id])
        expected_main = sum(1 for e in source_events if not e.task_id)
        events_path = dest.layout.events_path(_ORG, conv.conversation_id)
        line_count = sum(
            1 for line in events_path.read_text().splitlines() if line.strip()
        )
        assert line_count == expected_main

        await source.close()
        await dest.close()


class TestDryRun(_SeedMixin):
    async def test_dry_run_writes_nothing(self, tmp_path) -> None:
        source = await _open_memory_store()
        conv, _, _ = await self._seed_conversation(source, title="dry")
        dest = await _open_file_store(tmp_path / "dst")

        report = await StoreMigrator(source=source, dest=dest).migrate(dry_run=True)

        assert report.dry_run is True
        assert report.conversations_total == 1
        assert report.conversations_migrated == 1  # "would migrate"
        assert report.verified is None

        # Nothing landed on disk and nothing is queryable in the destination.
        meta_path = dest.layout.conversation_meta_path(_ORG, conv.conversation_id)
        assert not meta_path.exists()
        listed = await dest.list_conversations(
            org_id=_ORG, user_id=_USER, limit=50, include_deleted=True
        )
        assert listed == ()

        await source.close()
        await dest.close()


class TestVerifyCatchesMismatch(_SeedMixin):
    async def test_injected_mismatch_is_reported_and_raised(self, tmp_path) -> None:
        source = await _open_memory_store()
        conv, run, _ = await self._seed_conversation(source, title="verify")
        dest = await _open_file_store(tmp_path / "dst")
        await StoreMigrator(source=source, dest=dest).migrate()

        # Corrupt the destination out-of-band: drop the last event from disk and
        # reload the store so its port reads reflect the tampered stream.
        events_path = dest.layout.events_path(_ORG, conv.conversation_id)
        lines = [ln for ln in events_path.read_text().splitlines() if ln.strip()]
        events_path.write_text("\n".join(lines[:-1]) + "\n")
        await dest.close()
        dest = await _open_file_store(tmp_path / "dst")

        migrator = StoreMigrator(source=source, dest=dest)
        with pytest.raises(MigrationVerificationError) as excinfo:
            await migrator.migrate(scopes=[_scope()], verify=True)

        assert excinfo.value.mismatches
        assert any("event" in m for m in excinfo.value.mismatches)

        # A standalone verify() surfaces the same failure.
        with pytest.raises(MigrationVerificationError):
            await migrator.verify(scopes=[_scope()])

        await source.close()
        await dest.close()


class TestObjectCopy(_SeedMixin):
    async def test_file_source_objects_copied_by_content_address(
        self, tmp_path
    ) -> None:
        # File->file exercises the object-copy path: offload is file-store-only,
        # so this is the sole case where separate object blobs exist to migrate.
        source = await _open_file_store(tmp_path / "src")
        content = "LARGE TOOL RESULT\n" * 5_000
        conv, run, _ = await self._seed_conversation(
            source, title="obj", object_content=content
        )
        # Discover the referenced object digest from the source.
        digests = source.object_store.iter_digests()
        assert len(digests) == 1
        sha = digests[0]

        dest = await _open_file_store(tmp_path / "dst")
        report = await StoreMigrator(source=source, dest=dest).migrate(verify=True)

        assert report.verified is True
        assert report.objects >= 1
        # The blob travelled and its content-address is identical.
        assert dest.object_store.exists(sha)
        assert dest.object_store.get(sha) == source.object_store.get(sha)

        await source.close()
        await dest.close()
