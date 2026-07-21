"""File-store BACKUP→RESTORE integrity DRILL — the backup-integrity safety net.

Postgres has an automated *restore* drill (``postgres-restore-drill.yml`` proves
the documented restore procedure actually works: "we have backups" is not a
control until a passing drill exercises it — CLAUDE.md §Compliance reviews). The
file store — the desktop **default** store — already had a *corruption-recovery*
drill (:mod:`test_corruption_recovery_drill`, #160): seed a real store, corrupt
it, run the repair tool, prove it reopens. This module is the **complementary**
half: prove the operator backup→restore loop end to end.

A file-store backup is a plain copy of the store root:

* ``workspaces/<ws>/sessions/<conv>/`` — the canonical JSONL folders
  (``events.jsonl`` / ``messages.jsonl`` / ``runs.jsonl`` / ``conversation.json``),
* ``state/*.jsonl`` — the append-with-fold back-office + queue ledgers
  (durable state: the enqueued run command lives here),
* ``index/checkpoints.sqlite3`` — the durable LangGraph ``AsyncSqliteSaver``.

Deliberately **excluded** from the backup: ``index/catalog.sqlite3`` (+ its
``-wal`` / ``-shm`` sidecars). The catalog is a disposable read index — every
row is derivable from the canonical JSONL, so a real backup does not carry it,
and reopening a restored store must **rebuild it from the JSONL**. That rebuild
is the crux of the drill: if it regressed, a restored store would come back with
an empty or stale listing index while the canonical data was intact — a silent
data-availability bug a naive "does the folder exist" check would miss.

Everything is driven through the **real** :class:`FileRuntimeApiStore` and its
coordinators — no hand-written JSONL for the golden data — so a regression in
the open/replay path, the JSONL durability contract, the state-ledger replay, or
the catalog rebuild all fail this one test. Fully hermetic: no live services.

Two scenarios:

* **Full backup → total loss → restore** proves fidelity: after the live root is
  destroyed and replaced by the (catalog-less) backup, the reopened store serves
  every conversation, message, and event — sequence numbers monotonic and
  gap-free — the durable queue command is still claimable, the durable
  checkpointer survives byte-for-byte, and the disposable catalog rebuilt from
  the JSONL (``store_health()`` is clean; the file exists again).
* **Point-in-time snapshot** pins that a restore is exactly the backed-up moment:
  events appended *after* the backup are absent from the restored store, the
  rebuilt catalog reflects the restored JSONL (not the lost live state), and the
  sequence space stays monotonic so stream-resume still works post-restore.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_backup_drill"
_USER = "user_backup_drill"

# The disposable read index. A real backup omits these — they rebuild from JSONL.
_DISPOSABLE_CATALOG_NAMES = frozenset(
    {"catalog.sqlite3", "catalog.sqlite3-wal", "catalog.sqlite3-shm"}
)
# A sentinel standing in for the durable LangGraph checkpointer. The store itself
# does not create it (the deep-agent builder does), so the drill plants one to
# prove the backup preserves durable index state that is NOT the catalog.
_CHECKPOINTER_NAME = "checkpoints.sqlite3"
_CHECKPOINTER_BYTES = b"SQLite format 3\x00durable-checkpointer-sentinel"


class BackupDrillMixin:
    """Seed a real file store, then back it up / restore it as an operator would."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    @classmethod
    async def _seed_conversation(
        cls, store: FileRuntimeApiStore, *, event_count: int
    ) -> tuple[str, str]:
        """Create a conversation + run and append ``event_count`` main events.

        Returns ``(conversation_id, run_id)``. Everything flows through the real
        coordinators + ``append_event`` so the JSONL folders and the ``state``
        queue ledger hold genuine store output, not a hand-built fixture.
        """

        settings = cls._settings()
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
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
        for i in range(event_count):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=_ORG,
                    run_id=run.run_id,
                    conversation_id=conversation.conversation_id,
                    trace_id="trace_backup_drill",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    summary=f"chunk-{i}",
                )
            )
        return conversation.conversation_id, run.run_id

    @staticmethod
    def _plant_durable_checkpointer(root: Path) -> Path:
        """Write a sentinel durable checkpointer under ``index/``.

        Stands in for the real ``AsyncSqliteSaver`` file so the drill can prove
        the backup preserves durable (non-disposable) index state byte-for-byte.
        """

        path = root / "index" / _CHECKPOINTER_NAME
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(_CHECKPOINTER_BYTES)
        return path

    @staticmethod
    def _backup(root: Path, dest: Path) -> None:
        """Copy the store root to ``dest`` as an operator backup would.

        Mirrors a real file-store backup: copy everything **except** the
        disposable catalog index (it rebuilds from the canonical JSONL on the
        next open, so a backup neither needs nor should trust a stale copy).
        """

        def _ignore(_directory: str, names: list[str]) -> set[str]:
            return {n for n in names if n in _DISPOSABLE_CATALOG_NAMES}

        shutil.copytree(root, dest, ignore=_ignore)

    @staticmethod
    def _catalog_files(root: Path) -> list[Path]:
        index_dir = root / "index"
        if not index_dir.exists():
            return []
        return [p for p in index_dir.iterdir() if p.name in _DISPOSABLE_CATALOG_NAMES]

    @classmethod
    async def _restore_and_open(cls, backup: Path, root: Path) -> FileRuntimeApiStore:
        """Destroy the live root, restore it from ``backup``, and reopen."""

        shutil.rmtree(root)
        shutil.copytree(backup, root)
        restored = FileRuntimeApiStore(root)
        await restored.open()
        return restored


class TestFullBackupRestoreFidelity(BackupDrillMixin):
    async def test_total_loss_then_restore_reopens_with_full_fidelity(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"

        # --- 1. seed a real store; capture the golden committed truth ----------
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation_id, run_id = await self._seed_conversation(store, event_count=5)
        golden_events = await store.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=0
        )
        golden_seqs = [e.sequence_no for e in golden_events]
        assert golden_seqs, "seed should have written events"
        golden_messages = await store.list_messages(
            org_id=_ORG, conversation_id=conversation_id, limit=50
        )
        assert golden_messages, "seed should have written the user message"
        golden_conv = await store.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conversation_id
        )
        golden_run = await store.get_run(org_id=_ORG, run_id=run_id)
        assert golden_conv is not None and golden_run is not None
        await store.close()

        # A durable checkpointer exists on disk alongside the canonical data.
        checkpointer = self._plant_durable_checkpointer(root)
        # The live store built a disposable catalog; a real backup won't carry it.
        assert self._catalog_files(root), "live store should have a catalog index"

        # --- 2. back up the root (catalog-less), then LOSE the live store ------
        backup = tmp_path / "backup"
        self._backup(root, backup)
        assert not self._catalog_files(backup), "backup must exclude the catalog"
        assert (backup / "state" / "queue.jsonl").exists(), "durable queue backed up"
        assert (
            backup / "index" / _CHECKPOINTER_NAME
        ).read_bytes() == _CHECKPOINTER_BYTES, "durable checkpointer backed up"

        # --- 3. RESTORE from backup into a fresh root and reopen ---------------
        restored = await self._restore_and_open(backup, root)
        try:
            # (a) canonical event history is intact: monotonic + gap-free.
            events_after = await restored.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=0
            )
            seqs_after = [e.sequence_no for e in events_after]
            assert seqs_after == golden_seqs
            assert seqs_after == sorted(seqs_after) == sorted(set(seqs_after))

            # (b) conversation, run, and messages survived byte-equivalent.
            conv_after = await restored.get_conversation(
                org_id=_ORG, user_id=_USER, conversation_id=conversation_id
            )
            assert conv_after is not None
            assert conv_after.model_dump(mode="json") == golden_conv.model_dump(
                mode="json"
            )
            run_after = await restored.get_run(org_id=_ORG, run_id=run_id)
            assert run_after is not None
            assert run_after.model_dump(mode="json") == golden_run.model_dump(
                mode="json"
            )
            messages_after = await restored.list_messages(
                org_id=_ORG, conversation_id=conversation_id, limit=50
            )
            assert [m.model_dump(mode="json") for m in messages_after] == [
                m.model_dump(mode="json") for m in golden_messages
            ]

            # (c) DISPOSABLE catalog rebuilt from JSONL: the file is back, and the
            #     store reports healthy (a *missing* catalog is a clean create, not
            #     a torn discard — so catalog_rebuilt stays False).
            assert self._catalog_files(root), "catalog must be rebuilt on reopen"
            health = await restored.store_health()
            assert health.healthy
            assert not health.catalog_rebuilt

            # (d) DURABLE state: the enqueued run command survived and is claimable.
            claim = await restored.claim_next(
                worker_id="drill-worker",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            assert claim is not None, "durable queue command must survive restore"

            # (e) DURABLE checkpointer survived byte-for-byte.
            assert checkpointer.read_bytes() == _CHECKPOINTER_BYTES
        finally:
            await restored.close()


class TestPointInTimeSnapshot(BackupDrillMixin):
    async def test_restore_reflects_backup_moment_not_later_writes(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"

        store = FileRuntimeApiStore(root)
        await store.open()
        conversation_id, run_id = await self._seed_conversation(store, event_count=3)
        at_backup = await store.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=0
        )
        backup_seqs = [e.sequence_no for e in at_backup]
        backup_message_count = len(
            await store.list_messages(
                org_id=_ORG, conversation_id=conversation_id, limit=50
            )
        )
        await store.close()

        # Snapshot the store at this point in time (catalog excluded).
        backup = tmp_path / "backup"
        self._backup(root, backup)

        # --- writes AFTER the backup that the snapshot must NOT contain --------
        store = FileRuntimeApiStore(root)
        await store.open()
        for i in range(4):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=_ORG,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    trace_id="trace_backup_drill",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    summary=f"post-backup-{i}",
                )
            )
        post = await store.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=0
        )
        assert len(post) > len(backup_seqs), (
            "post-backup writes landed on the live root"
        )
        await store.close()

        # --- lose the live store and restore the earlier snapshot --------------
        restored = await self._restore_and_open(backup, root)
        try:
            restored_events = await restored.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=0
            )
            restored_seqs = [e.sequence_no for e in restored_events]
            # Exactly the backed-up moment: none of the later appends are present.
            assert restored_seqs == backup_seqs
            assert restored_seqs == sorted(restored_seqs) == sorted(set(restored_seqs))

            # The rebuilt catalog reflects the RESTORED JSONL, not the lost live
            # state — a stream resume after ``restored_seqs[-1]`` yields nothing.
            tail = await restored.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=restored_seqs[-1]
            )
            assert list(tail) == []

            restored_message_count = len(
                await restored.list_messages(
                    org_id=_ORG, conversation_id=conversation_id, limit=50
                )
            )
            assert restored_message_count == backup_message_count

            health = await restored.store_health()
            assert health.healthy
        finally:
            await restored.close()
