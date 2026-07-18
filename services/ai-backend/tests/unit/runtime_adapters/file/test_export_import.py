"""Export / backup / import of one conversation for the desktop file store.

Proves the portable-archive capability (PRD §698 *Export*):

* a conversation round-trips into a **fresh, empty** store — self-contained
  archive carries the canonical session files verbatim and every referenced
  object blob, reads work, and the exported ``events.jsonl`` is byte-identical
  to what was on disk;
* a tampered archive (one flipped byte in a part) is rejected on import and
  nothing is written;
* import assigns a fresh conversation id and leaves the original untouched, so
  re-importing into the same store never clobbers.
"""

from __future__ import annotations

import io
import tarfile

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.export_import import (
    FORMAT_VERSION,
    ArchiveIntegrityError,
    ConversationNotFoundError,
)
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_del"
_USER = "user_del"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class _SeedMixin:
    """Seed a conversation + run + event referencing an offloaded object."""

    async def _store(self, root) -> FileRuntimeApiStore:
        store = FileRuntimeApiStore(root)
        await store.open()
        return store

    def _coordinators(self, store: FileRuntimeApiStore) -> ConversationCoordinator:
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
        self, store: FileRuntimeApiStore, *, object_content: str
    ):
        coordinator = self._coordinators(store)
        conversation = await coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant", metadata={}
            )
        )
        run = await coordinator._run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="Hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        reference = FileOffloadWriter(store.object_store)(object_content)
        sha = reference.removeprefix("/large_tool_results/")
        await store.append_event(
            RuntimeEventDraft(
                org_id=_ORG,
                run_id=run.run_id,
                conversation_id=conversation.conversation_id,
                trace_id="trace_exp",
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload={
                    "tool_name": "web_search",
                    "call_id": "c1",
                    "status": "completed",
                    "output_ref": reference,
                    "preview": object_content[:50],
                },
            )
        )
        return conversation, run, sha


class TestRoundTrip(_SeedMixin):
    async def test_export_import_into_fresh_store_preserves_events_and_object(
        self, tmp_path
    ) -> None:
        source_store = await self._store(tmp_path / "src")
        conversation, run, sha = await self._seed_conversation(
            source_store, object_content="ROUNDTRIP PAYLOAD\n" * 4_000
        )
        conv_id = conversation.conversation_id
        original_events = list(source_store.events_by_run[run.run_id])

        # Snapshot the canonical events.jsonl bytes for the byte-equality check.
        events_path = source_store.layout.events_path(_ORG, conv_id)
        on_disk_events = events_path.read_bytes()

        archive = tmp_path / "backup.tar.gz"
        manifest = await source_store.export_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conv_id, destination=archive
        )
        await source_store.close()

        assert manifest.format_version == FORMAT_VERSION
        assert manifest.counts.events == len(original_events)
        assert manifest.counts.objects == 1

        # The archive's events.jsonl is byte-identical to what was on disk.
        with tarfile.open(archive, "r:gz") as tar:
            archived_events = tar.extractfile("conversation/events.jsonl").read()  # type: ignore[union-attr]
        assert archived_events == on_disk_events

        # Import into a brand-new, empty store proves self-containment.
        dest_store = await self._store(tmp_path / "dst")
        outcome = await dest_store.import_conversation(
            org_id=_ORG, user_id=_USER, source=archive
        )
        new_id = outcome.conversation_id
        assert new_id != conv_id
        assert outcome.source_conversation_id == conv_id

        # The referenced blob travelled inside the archive and re-registered.
        assert dest_store.object_store.exists(sha)

        # Reads work: the conversation, its messages and its events are all
        # visible through the rebuilt catalog.
        imported = await dest_store.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=new_id
        )
        assert imported is not None
        messages = await dest_store.list_messages(
            org_id=_ORG, conversation_id=new_id, limit=50
        )
        assert len(messages) == manifest.counts.messages

        new_run = next(
            r for r in dest_store.runs.values() if r.conversation_id == new_id
        )
        assert new_run.run_id != run.run_id  # runs are re-keyed too
        events = await dest_store.list_events_after(
            org_id=_ORG, run_id=new_run.run_id, after_sequence=0
        )
        # Event content survived intact; only the ids were deliberately re-keyed.
        assert len(events) == len(original_events)
        assert [(e.sequence_no, e.event_type, e.payload) for e in events] == [
            (e.sequence_no, e.event_type, e.payload) for e in original_events
        ]
        assert all(e.conversation_id == new_id for e in events)
        assert all(e.run_id == new_run.run_id for e in events)
        await dest_store.close()

    async def test_export_missing_conversation_raises(self, tmp_path) -> None:
        store = await self._store(tmp_path / "src")
        with pytest.raises(ConversationNotFoundError):
            await store.export_conversation(
                org_id=_ORG,
                user_id=_USER,
                conversation_id="does_not_exist",
                destination=tmp_path / "x.tar.gz",
            )
        await store.close()


class TestTamperRejected(_SeedMixin):
    async def test_flipped_byte_in_part_is_rejected_and_nothing_written(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path / "src")
        conversation, _run, _sha = await self._seed_conversation(
            store, object_content="TAMPER PAYLOAD\n" * 4_000
        )
        archive = tmp_path / "backup.tar.gz"
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=archive,
        )

        tampered = tmp_path / "tampered.tar.gz"
        _rewrite_with_flipped_part(archive, tampered, part="conversation/events.jsonl")

        before = set(store.conversations)
        with pytest.raises(ArchiveIntegrityError):
            await store.import_conversation(org_id=_ORG, user_id=_USER, source=tampered)
        # Fail-closed: no new conversation materialised.
        assert set(store.conversations) == before
        await store.close()

    async def test_tampered_object_blob_is_rejected(self, tmp_path) -> None:
        store = await self._store(tmp_path / "src")
        conversation, _run, sha = await self._seed_conversation(
            store, object_content="BLOB TAMPER\n" * 4_000
        )
        archive = tmp_path / "backup.tar.gz"
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=archive,
        )
        tampered = tmp_path / "tampered.tar.gz"
        _rewrite_with_flipped_part(archive, tampered, part=f"objects/{sha}")

        with pytest.raises(ArchiveIntegrityError):
            await store.import_conversation(org_id=_ORG, user_id=_USER, source=tampered)
        await store.close()

    async def test_scope_mismatch_is_rejected(self, tmp_path) -> None:
        store = await self._store(tmp_path / "src")
        conversation, _run, _sha = await self._seed_conversation(
            store, object_content="SCOPE PAYLOAD\n" * 4_000
        )
        archive = tmp_path / "backup.tar.gz"
        await store.export_conversation(
            org_id=_ORG,
            user_id=_USER,
            conversation_id=conversation.conversation_id,
            destination=archive,
        )
        with pytest.raises(ArchiveIntegrityError):
            await store.import_conversation(
                org_id=_ORG, user_id="someone_else", source=archive
            )
        await store.close()


class TestFreshIdNoClobber(_SeedMixin):
    async def test_import_assigns_new_id_and_leaves_original_untouched(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path / "src")
        conversation, run, sha = await self._seed_conversation(
            store, object_content="CLOBBER PAYLOAD\n" * 4_000
        )
        conv_id = conversation.conversation_id
        original_dir = store.layout.conversation_dir(_ORG, conv_id)
        original_events = store.layout.events_path(_ORG, conv_id).read_bytes()

        archive = tmp_path / "backup.tar.gz"
        await store.export_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conv_id, destination=archive
        )

        # Import back into the SAME store: must not clobber the original.
        outcome = await store.import_conversation(
            org_id=_ORG, user_id=_USER, source=archive
        )
        new_id = outcome.conversation_id
        assert new_id != conv_id

        # Original conversation, its directory, and its events are all intact.
        assert conv_id in store.conversations
        assert original_dir.exists()
        assert store.layout.events_path(_ORG, conv_id).read_bytes() == original_events
        assert store.runs[run.run_id].conversation_id == conv_id

        # Both conversations coexist and both are listable.
        listed = {
            c.conversation_id
            for c in await store.list_conversations(
                org_id=_ORG, user_id=_USER, limit=50
            )
        }
        assert conv_id in listed
        assert new_id in listed

        # The shared object stays present for both.
        assert store.object_store.exists(sha)
        await store.close()


def _rewrite_with_flipped_part(source, destination, *, part: str) -> None:
    """Copy a tar.gz, flipping one byte in ``part`` (manifest hash won't match)."""

    with tarfile.open(source, "r:gz") as tar:
        members = {
            m.name: tar.extractfile(m).read()  # type: ignore[union-attr]
            for m in tar.getmembers()
            if m.isfile()
        }
    data = bytearray(members[part])
    data[0] ^= 0x01
    members[part] = bytes(data)
    with tarfile.open(destination, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
