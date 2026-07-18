"""Disk-quota admission + age-based cleanup for the desktop file store.

Two capacity controls, both OFF by default (unlimited / keep forever):

* :class:`QuotaGuard` rejects an object-store write that would push the store
  root past its byte ceiling — with the typed, catchable
  :class:`FileStoreQuotaError`, *before* any bytes land, so a rejected write
  never leaves a partial or corrupt object behind. Under-quota writes succeed.
* ``FileRuntimeApiStore.sweep_expired_conversations`` reaps conversations whose
  last activity predates the retention window through the existing
  physical-delete + object-GC path: aged sessions and their now-orphan objects
  are removed while in-window conversations and content-addressed objects still
  referenced by a survivor are untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._capacity import (
    FileStoreCleanupReport,
    FileStoreQuotaError,
    QuotaGuard,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_cap"
_USER = "user_cap"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class TestQuotaGuardObjectStore:
    """Byte-ceiling admission enforced at the object-store write hook."""

    def _store(self, tmp_path, *, max_bytes: int) -> FileObjectStore:
        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        return FileObjectStore(layout, quota=QuotaGuard(layout, max_bytes=max_bytes))

    def test_under_quota_writes_succeed(self, tmp_path) -> None:
        store = self._store(tmp_path, max_bytes=5_000)
        ref_a = store.put(b"a" * 1_000)
        ref_b = store.put(b"b" * 3_000)
        # Both blobs landed and read back with a verified digest.
        assert store.exists(ref_a)
        assert store.exists(ref_b)
        assert store.get(ref_a) == b"a" * 1_000
        assert store.get(ref_b) == b"b" * 3_000

    def test_over_quota_write_rejected_and_store_consistent(self, tmp_path) -> None:
        store = self._store(tmp_path, max_bytes=5_000)
        ref_a = store.put(b"a" * 1_000)
        ref_b = store.put(b"b" * 3_000)  # used ~4000, still under ceiling
        digests_before = set(store.iter_digests())

        # This write (4000 + 3000 > 5000) must be refused with the typed error.
        with pytest.raises(FileStoreQuotaError) as excinfo:
            store.put(b"c" * 3_000)

        err = excinfo.value
        assert err.code == "file_store_quota_exceeded"
        assert err.retryable is False
        assert err.max_bytes == 5_000
        assert err.incoming_bytes == 3_000

        # Store is consistent: the rejected blob was never written, no partial
        # ``.tmp`` sibling leaked, and the earlier blobs are intact.
        import hashlib

        rejected_digest = hashlib.sha256(b"c" * 3_000).hexdigest()
        assert not store.exists(rejected_digest)
        assert set(store.iter_digests()) == digests_before
        tmp_sibling = store._layout.object_path(rejected_digest).with_name(
            rejected_digest + ".tmp"
        )
        assert not tmp_sibling.exists()
        assert store.get(ref_a) == b"a" * 1_000
        assert store.get(ref_b) == b"b" * 3_000

    def test_unlimited_by_default(self, tmp_path) -> None:
        # max_bytes=0 (unlimited): the guard never rejects and never walks.
        store = self._store(tmp_path, max_bytes=0)
        assert store._quota.enabled is False
        ref = store.put(b"z" * 1_000_000)
        assert store.exists(ref)

    def test_idempotent_reput_exempt_from_quota(self, tmp_path) -> None:
        # A re-put of an already-stored digest adds no bytes, so it is admitted
        # even when the store is exactly at its ceiling.
        store = self._store(tmp_path, max_bytes=1_000)
        ref = store.put(b"x" * 1_000)  # store now exactly full
        again = store.put(b"x" * 1_000)  # same bytes → no growth → allowed
        assert again.sha256 == ref.sha256


class _SeedMixin:
    """Seed real conversations + offloaded objects for the cleanup sweeper."""

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

    async def _seed(
        self, store: FileRuntimeApiStore, *, object_content: str, user: str = _USER
    ):
        """Create a conversation + run, offload ``object_content``, reference it
        from a tool-result event. Returns ``(conversation, sha)``.
        """

        coordinator = self._coordinators(store)
        conversation = await coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=user, assistant_id="assistant", metadata={}
            )
        )
        run = await coordinator._run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=user,
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
                trace_id="trace_cap",
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
        return conversation, sha

    def _backdate(self, store: FileRuntimeApiStore, conversation, *, days: int) -> None:
        aged = store.conversations[conversation.conversation_id].model_copy(
            update={"updated_at": datetime.now(timezone.utc) - timedelta(days=days)}
        )
        store.conversations[conversation.conversation_id] = aged
        store._persist_conversation(aged)


class TestAgeBasedCleanupSweeper(_SeedMixin):
    async def test_reaps_aged_gcs_orphans_keeps_inwindow_and_shared(
        self, tmp_path
    ) -> None:
        store = FileRuntimeApiStore(tmp_path / "store", retention_days=7)
        await store.open()

        # In-window conversation sharing a blob with an aged one.
        fresh, sha_shared = await self._seed(
            store, object_content="SHARED PAYLOAD\n" * 4_000
        )
        aged_shared, sha_shared_2 = await self._seed(
            store, object_content="SHARED PAYLOAD\n" * 4_000
        )
        assert sha_shared == sha_shared_2  # content-addressed dedupe
        # Aged conversation owning an exclusive blob.
        aged_excl, sha_excl = await self._seed(
            store, object_content="EXCLUSIVE AGED\n" * 4_000
        )
        self._backdate(store, aged_shared, days=30)
        self._backdate(store, aged_excl, days=30)

        report = await store.sweep_expired_conversations()

        assert isinstance(report, FileStoreCleanupReport)
        assert report.conversations_deleted == 2
        assert report.objects_collected == 1  # only the exclusive orphan
        assert report.skipped_legal_hold == 0
        assert report.dry_run is False

        # Aged sessions are gone from disk + view.
        assert aged_shared.conversation_id not in store.conversations
        assert aged_excl.conversation_id not in store.conversations
        assert not store.layout.conversation_dir(
            _ORG, aged_excl.conversation_id
        ).exists()
        # Exclusive orphan GC'd; shared blob survives (fresh still references it).
        assert not store.object_store.exists(sha_excl)
        assert store.object_store.exists(sha_shared)
        # In-window conversation + its dir are untouched.
        assert fresh.conversation_id in store.conversations
        assert store.layout.conversation_dir(_ORG, fresh.conversation_id).exists()
        await store.close()

    async def test_dry_run_reports_without_removing(self, tmp_path) -> None:
        store = FileRuntimeApiStore(tmp_path / "store", retention_days=7)
        await store.open()
        aged, sha = await self._seed(store, object_content="DRYRUN\n" * 4_000)
        self._backdate(store, aged, days=30)

        report = await store.sweep_expired_conversations(dry_run=True)

        assert report.conversations_deleted == 1
        assert report.dry_run is True
        # Nothing actually removed.
        assert aged.conversation_id in store.conversations
        assert store.layout.conversation_dir(_ORG, aged.conversation_id).exists()
        assert store.object_store.exists(sha)
        await store.close()

    async def test_retention_disabled_is_noop(self, tmp_path) -> None:
        # retention_days=0 (default): keep forever, even for ancient sessions.
        store = FileRuntimeApiStore(tmp_path / "store")
        await store.open()
        aged, sha = await self._seed(store, object_content="ANCIENT\n" * 4_000)
        self._backdate(store, aged, days=3_650)

        report = await store.sweep_expired_conversations()

        assert report == FileStoreCleanupReport()
        assert aged.conversation_id in store.conversations
        assert store.object_store.exists(sha)
        await store.close()

    async def test_startup_sweep_reaps_expired_on_open(self, tmp_path) -> None:
        # Seed + age a conversation, then reopen a store WITH a retention window:
        # the boot-time maintenance hook reaps it before any read is served.
        root = tmp_path / "store"
        seed_store = FileRuntimeApiStore(root)
        await seed_store.open()
        aged, sha = await self._seed(seed_store, object_content="BOOT REAP\n" * 4_000)
        self._backdate(seed_store, aged, days=30)
        await seed_store.close()

        reopened = FileRuntimeApiStore(root, retention_days=7)
        await reopened.open()

        assert aged.conversation_id not in reopened.conversations
        assert not reopened.layout.conversation_dir(_ORG, aged.conversation_id).exists()
        assert not reopened.object_store.exists(sha)
        await reopened.close()
