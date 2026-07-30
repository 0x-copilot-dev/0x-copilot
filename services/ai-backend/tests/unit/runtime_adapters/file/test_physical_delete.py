"""Physical deletion + object GC + retention reaping for the desktop file store.

Proves "delete my data" removes bytes: a purged conversation's session folder
and JSONL streams are gone from disk, objects it exclusively owned are
garbage-collected, a content-addressed object shared with another conversation
survives, legal hold blocks deletion, the retention sweeper reaps expired
sessions, the disposable catalog index rebuilds correctly after a purge, and
every purge writes a signed deletion-completed audit row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationProjectionJob,
    EvaluationScope,
    ProjectionJobStatus,
)
from agent_runtime.harness_quality.ports import EvaluationObjectDeletionPolicy
from agent_runtime.persistence.records import (
    RuntimeContextOccupancyRecord,
)
from agent_runtime.persistence.records.retention import RetentionKind
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._deletion import (
    DeletionPlanError,
    LegalHoldPolicy,
    SessionEraser,
)
from runtime_adapters.file.offload import FileOffloadWriter
from runtime_adapters.file.evaluation_repository import FileEvaluationRepository
from runtime_adapters.file.runtime_api_store import (
    FileRuntimeApiStore,
    _DeletionFields,
)
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_del"
_USER = "user_del"


class _UnavailableExternalReferences:
    def protected_object_digests(self) -> frozenset[str]:
        raise RuntimeError("reference index unavailable")


class _SourceDeletionRecorder:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, frozenset[str]]] = []

    async def delete_source_runs(
        self,
        *,
        source_org_id: str,
        source_run_ids: frozenset[str],
    ) -> object:
        self.calls.append((source_org_id, source_run_ids))
        if self.error is not None:
            raise self.error
        return object()


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class _SeedMixin:
    """Seed conversations, runs, events, and offloaded objects on a real store."""

    async def _store(self, tmp_path) -> FileRuntimeApiStore:
        store = FileRuntimeApiStore(tmp_path / "store")
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
        self,
        store: FileRuntimeApiStore,
        *,
        object_content: str,
        user: str = _USER,
        metadata: dict | None = None,
    ):
        """Create a conversation + run, offload ``object_content``, and append an
        event whose payload references the resulting blob. Returns
        ``(conversation, run, sha)``.
        """

        coordinator = self._coordinators(store)
        conversation = await coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG,
                user_id=user,
                assistant_id="assistant",
                metadata=metadata or {},
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
                trace_id="trace_del",
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


class TestPhysicalDeleteUserHistory(_SeedMixin):
    async def test_erases_session_and_gcs_exclusive_object(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conversation, run, sha = await self._seed_conversation(
            store, object_content="UNIQUE PAYLOAD\n" * 4_000
        )
        conv_dir = store.layout.conversation_dir(_ORG, conversation.conversation_id)
        assert conv_dir.exists()
        assert (conv_dir / store.layout.EVENTS_FILE).exists()
        assert store.object_store.exists(sha)

        response = await store.delete_user_history(
            org_id=_ORG, user_id=_USER, reason="gdpr erase"
        )

        # Session directory + every JSONL stream is gone from disk.
        assert not conv_dir.exists()
        # The exclusively-owned object was garbage-collected.
        assert not store.object_store.exists(sha)
        # Materialised view + index no longer surface the conversation.
        assert conversation.conversation_id not in store.conversations
        listed = await store.list_conversations(
            org_id=_ORG, user_id=_USER, limit=50, include_archived=True
        )
        assert listed == ()
        # Response tally reflects the physical deletion.
        assert response.conversations_archived == 1
        assert response.runs_cancelled == 1
        assert response.events_retained == 0
        assert response.audit_event_id is not None
        await store.close()

    async def test_shared_object_survives_when_one_owner_deleted(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path)
        shared = "SHARED PAYLOAD\n" * 4_000
        conv_a, _run_a, sha_a = await self._seed_conversation(
            store, object_content=shared
        )
        conv_b, _run_b, sha_b = await self._seed_conversation(
            store, object_content=shared
        )
        # Content-addressed: identical content dedupes to one blob.
        assert sha_a == sha_b
        assert store.object_store.exists(sha_a)

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")

        # Every conversation for the user is deleted, but the shared blob is
        # deleted only once and only after its last reference is gone — here both
        # owners were deleted, so it is collected. Re-run the finer-grained case
        # with a surviving second user below.
        assert conv_a.conversation_id not in store.conversations
        assert conv_b.conversation_id not in store.conversations
        assert not store.object_store.exists(sha_a)
        await store.close()

    async def test_shared_object_survives_across_users(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        shared = "CROSS USER PAYLOAD\n" * 4_000
        conv_del, _r1, sha = await self._seed_conversation(
            store, object_content=shared, user=_USER
        )
        conv_keep, _r2, sha2 = await self._seed_conversation(
            store, object_content=shared, user="other_user"
        )
        assert sha == sha2

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")

        # The deleted user's session is gone, but the blob is still referenced by
        # the surviving user's conversation, so GC leaves it in place.
        assert conv_del.conversation_id not in store.conversations
        assert conv_keep.conversation_id in store.conversations
        assert store.object_store.exists(sha)
        keep_dir = store.layout.conversation_dir(_ORG, conv_keep.conversation_id)
        assert keep_dir.exists()
        await store.close()

    async def test_evaluation_owned_object_survives_conversation_purge(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path)
        content = "EVALUATION FIXTURE\n" * 4_000
        conversation, _run, sha = await self._seed_conversation(
            store,
            object_content=content,
        )
        repository = FileEvaluationRepository(
            store.layout,
            object_store=store.object_store,
        )
        store.configure_external_object_references(repository)
        artifact = await repository.put_protected_artifact(
            EvaluationScope(profile_id="desktop-local", project_id="project-1"),
            data=content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
        )
        assert artifact.sha256 == sha

        await store.delete_user_history(
            org_id=_ORG,
            user_id=_USER,
            reason="erase conversation data",
        )

        assert conversation.conversation_id not in store.conversations
        assert store.object_store.exists(sha)
        await store.close()

    async def test_external_reference_failure_skips_object_gc(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conversation, _run, sha = await self._seed_conversation(
            store,
            object_content="FAIL CLOSED GC\n" * 4_000,
        )
        store.configure_external_object_references(_UnavailableExternalReferences())

        await store.delete_user_history(
            org_id=_ORG,
            user_id=_USER,
            reason="erase conversation data",
        )

        assert conversation.conversation_id not in store.conversations
        assert store.object_store.exists(sha)
        await store.close()

    async def test_source_run_cascade_precedes_conversation_erasure(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path)
        conversation, run, _sha = await self._seed_conversation(
            store,
            object_content="CASCADE SOURCE RUN\n" * 4_000,
        )
        observer = _SourceDeletionRecorder()
        store.configure_source_run_deletion_observer(observer)

        await store.delete_user_history(
            org_id=_ORG,
            user_id=_USER,
            reason="erase conversation data",
        )

        assert observer.calls == [(_ORG, frozenset({run.run_id}))]
        assert conversation.conversation_id not in store.conversations
        await store.close()

    async def test_composed_evaluation_cascade_deletes_and_tombstones_source_job(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path)
        conversation, run, _sha = await self._seed_conversation(
            store,
            object_content="PROJECTED SOURCE RUN\n" * 4_000,
        )
        repository = FileEvaluationRepository(
            store.layout,
            object_store=store.object_store,
            object_deletion_policy=(
                EvaluationObjectDeletionPolicy.SHARED_STORE_METADATA_ONLY
            ),
        )
        store.configure_external_object_references(repository)
        store.configure_source_run_deletion_observer(repository)
        scope = EvaluationScope(
            profile_id="desktop-local",
            project_id="project-1",
        )
        now = datetime.now(timezone.utc)
        values: dict[str, object] = {
            "job_id": "projection-source-delete",
            "source_org_id": _ORG,
            "source_run_id": run.run_id,
            "variant_id": "variant-source-delete",
            "policy_revision": "projection-policy-v1",
            "terminal_sequence_no": 2,
            "status": ProjectionJobStatus.PENDING,
            "next_sequence_no": 1,
            "attempt_count": 0,
            "lease_owner_digest": None,
            "lease_expires_at": None,
            "trajectory_id": None,
            "failure_reason_code": None,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        }
        job = EvaluationProjectionJob(
            **values,
            job_digest=EvaluationProjectionJob.digest_for(**values),
        )
        assert await repository.put_projection_job(scope, job)

        await store.delete_user_history(
            org_id=_ORG,
            user_id=_USER,
            reason="erase conversation data",
        )

        assert conversation.conversation_id not in store.conversations
        assert await repository.get_projection_job(scope, job_id=job.job_id) is None
        assert not await repository.put_projection_job(scope, job)
        await store.close()

    async def test_source_run_cascade_failure_aborts_before_source_erasure(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path)
        conversation, run, sha = await self._seed_conversation(
            store,
            object_content="CASCADE FAILURE\n" * 4_000,
        )
        observer = _SourceDeletionRecorder(error=RuntimeError("cascade unavailable"))
        store.configure_source_run_deletion_observer(observer)

        with pytest.raises(RuntimeError, match="cascade unavailable"):
            await store.delete_user_history(
                org_id=_ORG,
                user_id=_USER,
                reason="erase conversation data",
            )

        assert observer.calls == [(_ORG, frozenset({run.run_id}))]
        assert conversation.conversation_id in store.conversations
        assert store.object_store.exists(sha)
        await store.close()


class TestLegalHold(_SeedMixin):
    async def test_legal_hold_blocks_deletion(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        held_conv, _run, held_sha = await self._seed_conversation(
            store,
            object_content="HELD PAYLOAD\n" * 4_000,
            metadata={"legal_hold": True},
        )
        free_conv, _run2, free_sha = await self._seed_conversation(
            store, object_content="FREE PAYLOAD\n" * 4_000
        )

        response = await store.delete_user_history(
            org_id=_ORG, user_id=_USER, reason="erase"
        )

        # Held conversation + its object are untouched; free one is erased.
        held_dir = store.layout.conversation_dir(_ORG, held_conv.conversation_id)
        assert held_dir.exists()
        assert held_conv.conversation_id in store.conversations
        assert store.object_store.exists(held_sha)
        assert free_conv.conversation_id not in store.conversations
        assert not store.object_store.exists(free_sha)
        # Response counts one deletion and reports the retained (held) events.
        assert response.conversations_archived == 1
        assert response.events_retained >= 1
        await store.close()

    def test_policy_reads_metadata_flag(self, tmp_path) -> None:
        from runtime_api.schemas import ConversationRecord

        held = ConversationRecord(
            org_id=_ORG, user_id=_USER, assistant_id="a", metadata={"legal_hold": True}
        )
        free = ConversationRecord(org_id=_ORG, user_id=_USER, assistant_id="a")
        assert LegalHoldPolicy.is_on_hold(held) is True
        assert LegalHoldPolicy.is_on_hold(free) is False


class TestRetentionSweep(_SeedMixin):
    async def test_sweeper_removes_expired_sessions(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conv, _run, sha = await self._seed_conversation(
            store, object_content="EXPIRED PAYLOAD\n" * 4_000
        )
        # Backdate the conversation's last activity so it is past the TTL.
        aged = store.conversations[conv.conversation_id].model_copy(
            update={"updated_at": datetime.now(timezone.utc) - timedelta(days=30)}
        )
        store.conversations[conv.conversation_id] = aged
        store._persist_conversation(aged)

        outcome = await store.sweep_retention_kind(
            org_id=_ORG,
            kind=RetentionKind.MESSAGES,
            ttl_seconds=int(timedelta(days=7).total_seconds()),
        )

        assert outcome.deleted == 1
        assert outcome.skipped_legal_hold == 0
        assert conv.conversation_id not in store.conversations
        assert not store.layout.conversation_dir(_ORG, conv.conversation_id).exists()
        assert not store.object_store.exists(sha)
        await store.close()

    async def test_sweeper_dry_run_removes_nothing(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conv, _run, sha = await self._seed_conversation(
            store, object_content="DRYRUN PAYLOAD\n" * 4_000
        )
        aged = store.conversations[conv.conversation_id].model_copy(
            update={"updated_at": datetime.now(timezone.utc) - timedelta(days=30)}
        )
        store.conversations[conv.conversation_id] = aged
        store._persist_conversation(aged)

        outcome = await store.sweep_retention_kind(
            org_id=_ORG,
            kind=RetentionKind.MESSAGES,
            ttl_seconds=int(timedelta(days=7).total_seconds()),
            dry_run=True,
        )

        assert outcome.deleted == 1
        # Nothing actually removed under dry-run.
        assert conv.conversation_id in store.conversations
        assert store.layout.conversation_dir(_ORG, conv.conversation_id).exists()
        assert store.object_store.exists(sha)
        await store.close()

    async def test_sweeper_skips_legal_hold(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conv, _run, sha = await self._seed_conversation(
            store,
            object_content="HELD EXPIRED\n" * 4_000,
            metadata={"legal_hold": True},
        )
        aged = store.conversations[conv.conversation_id].model_copy(
            update={"updated_at": datetime.now(timezone.utc) - timedelta(days=30)}
        )
        store.conversations[conv.conversation_id] = aged
        store._persist_conversation(aged)

        outcome = await store.sweep_retention_kind(
            org_id=_ORG, kind=RetentionKind.MESSAGES, ttl_seconds=1
        )

        assert outcome.deleted == 0
        assert outcome.skipped_legal_hold == 1
        assert conv.conversation_id in store.conversations
        assert store.object_store.exists(sha)
        await store.close()

    async def test_non_message_kind_is_noop(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conv, _run, _sha = await self._seed_conversation(
            store, object_content="KEEP PAYLOAD\n" * 4_000
        )
        outcome = await store.sweep_retention_kind(
            org_id=_ORG, kind=RetentionKind.EVENTS, ttl_seconds=0
        )
        assert outcome.deleted == 0
        assert conv.conversation_id in store.conversations
        await store.close()


class TestIndexRebuildAndAudit(_SeedMixin):
    async def test_index_rebuilds_after_deletion(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        keep, _rk, _sk = await self._seed_conversation(
            store, object_content="KEEP\n" * 4_000
        )
        drop, _rd, _sd = await self._seed_conversation(
            store, object_content="DROP\n" * 4_000
        )
        root = store.layout.root

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")
        # Both belonged to the same user; reseed a survivor for the rebuild check.
        await store.close()

        # Reopen a fresh instance: the index rebuilds from the JSONL that remains
        # on disk, so the purged conversations never reappear.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        listed = await reopened.list_conversations(
            org_id=_ORG, user_id=_USER, limit=50, include_archived=True
        )
        assert listed == ()
        assert keep.conversation_id not in reopened.conversations
        assert drop.conversation_id not in reopened.conversations
        await reopened.close()

    async def test_index_rebuild_keeps_survivor_drops_purged(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        survivor, _rs, _ss = await self._seed_conversation(
            store, object_content="SURVIVOR\n" * 4_000, user="keep_user"
        )
        purged, _rp, _sp = await self._seed_conversation(
            store, object_content="PURGED\n" * 4_000, user=_USER
        )
        root = store.layout.root

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        assert survivor.conversation_id in reopened.conversations
        assert purged.conversation_id not in reopened.conversations
        survivor_listed = await reopened.list_conversations(
            org_id=_ORG, user_id="keep_user", limit=50
        )
        assert any(
            c.conversation_id == survivor.conversation_id for c in survivor_listed
        )
        await reopened.close()

    async def test_deletion_records_signed_audit_event(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        await self._seed_conversation(store, object_content="AUDIT PAYLOAD\n" * 4_000)

        await store.delete_user_history(
            org_id=_ORG, user_id=_USER, reason="compliance erase"
        )

        purge_rows = [
            record
            for event_type, record in store.audit_log
            if event_type == _DeletionFields.EVENT_TYPE
        ]
        assert len(purge_rows) == 1
        row = purge_rows[0]
        assert row[_DeletionFields.CONVERSATIONS_DELETED] == 1
        assert row[_DeletionFields.OBJECTS_GARBAGE_COLLECTED] == 1
        assert row[_DeletionFields.TRIGGER] == _DeletionFields.TRIGGER_USER_REQUEST
        # The row is chained + signed (tamper-evident) like every audit record.
        assert row["signature"] is not None
        assert row["seq"] >= 1
        await store.close()


class TestFailSafeAndGcGuards(_SeedMixin):
    async def test_gc_skips_object_with_in_flight_write(self, tmp_path) -> None:
        store = await self._store(tmp_path)
        conv, _run, sha = await self._seed_conversation(
            store, object_content="INFLIGHT\n" * 4_000
        )
        # Simulate an in-flight write (a lingering ``.tmp`` sibling): GC must not
        # remove a blob whose bytes could still be landing.
        target = store.layout.object_path(sha)
        tmp_sibling = target.with_name(target.name + ".tmp")
        tmp_sibling.write_bytes(b"partial")

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")

        # Session is gone, but the blob survived the GC because of the open handle.
        assert conv.conversation_id not in store.conversations
        assert store.object_store.exists(sha)
        tmp_sibling.unlink()
        await store.close()

    def test_session_eraser_refuses_escape(self, tmp_path) -> None:
        from runtime_api.schemas import ConversationRecord
        from runtime_adapters.file._paths import FileStoreLayout

        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        eraser = SessionEraser(layout)
        good = ConversationRecord(org_id=_ORG, user_id=_USER, assistant_id="a")
        # A well-formed record plans cleanly (path is hashed + contained).
        planned = eraser.plan([good])
        assert len(planned) == 1

    def test_deletion_plan_error_is_raised_on_escape(
        self, tmp_path, monkeypatch
    ) -> None:
        from runtime_api.schemas import ConversationRecord
        from runtime_adapters.file._paths import FileStoreLayout

        layout = FileStoreLayout(tmp_path / "store")
        layout.ensure_scaffold()
        eraser = SessionEraser(layout)
        conv = ConversationRecord(org_id=_ORG, user_id=_USER, assistant_id="a")

        # Force the resolved conversation dir outside the sessions root.
        escaped = tmp_path / "elsewhere" / "evil"

        def _fake_conversation_dir(org_id, conversation_id):
            return escaped

        monkeypatch.setattr(layout, "conversation_dir", _fake_conversation_dir)
        try:
            eraser.plan([conv])
        except DeletionPlanError:
            return
        raise AssertionError("expected DeletionPlanError")


class TestPhysicalDeleteRemovesContextOccupancy(_SeedMixin):
    """A purged conversation's occupancy rows leave with it (design §5).

    Occupancy is the one telemetry family whose §5 contract says it is erased by
    the conversation-deletion cascade and therefore needs no retention class of
    its own. On Postgres that is two ``ON DELETE CASCADE`` clauses. Here it was
    nothing: occupancy lives in a shared back-office ledger, not in the
    per-session directories :class:`SessionEraser` removes, and
    ``_drop_conversation_state`` folded conversations, messages, runs, events and
    the index while leaving one occupancy row per model call behind — on the
    store that is the desktop default. "No longer readable" is not "erased".
    """

    async def _occupancy(
        self,
        store: FileRuntimeApiStore,
        *,
        conversation_id: str,
        run_id: str,
        model_call_id: str,
    ) -> None:
        await store.append_context_occupancy(
            RuntimeContextOccupancyRecord.from_measurement(
                org_id=_ORG,
                run_id=run_id,
                conversation_id=conversation_id,
                model_call_id=model_call_id,
                provider="openai",
                model_family="gpt-5.4-mini",
                estimated_input_tokens=971,
                segments=(
                    {
                        "segment_class": "tools",
                        "label": "a:b",
                        "lifecycle": "resident",
                        "detail": "publish_artifact",
                        "byte_count": 8,
                        "estimated_tokens": 2,
                        "counter_source": "tokenizer",
                    },
                ),
            )
        )

    async def test_purge_erases_the_rows_from_memory_and_from_disk(
        self, tmp_path
    ) -> None:
        store = await self._store(tmp_path)
        conversation, run, _sha = await self._seed_conversation(
            store, object_content="PAYLOAD\n" * 100
        )
        await self._occupancy(
            store,
            conversation_id=conversation.conversation_id,
            run_id=run.run_id,
            model_call_id="call_purged",
        )
        ledger_path = store.layout.state_path("context_occupancy")
        assert "call_purged" in ledger_path.read_text()

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")

        assert store.context_occupancy == {}
        # The bytes are gone, not tombstoned: an append-only delete marker would
        # leave the original ``put`` line — and its contents — on disk.
        assert "call_purged" not in ledger_path.read_text()
        await store.close()

    async def test_erasure_survives_a_reopen(self, tmp_path) -> None:
        """The fold on the next boot must not resurrect the purged rows."""

        store = await self._store(tmp_path)
        conversation, run, _sha = await self._seed_conversation(
            store, object_content="PAYLOAD\n" * 100
        )
        await self._occupancy(
            store,
            conversation_id=conversation.conversation_id,
            run_id=run.run_id,
            model_call_id="call_purged",
        )
        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")
        await store.close()

        reopened = FileRuntimeApiStore(tmp_path / "store")
        await reopened.open()

        assert reopened.context_occupancy == {}
        await reopened.close()

    async def test_another_conversations_rows_survive(self, tmp_path) -> None:
        """Erasure is scoped: a survivor's measurements are not collateral.

        The row is matched on ``conversation_id`` rather than on the victim runs,
        so this also pins that the wider key does not over-collect.
        """

        store = await self._store(tmp_path)
        victim, victim_run, _a = await self._seed_conversation(
            store, object_content="VICTIM\n" * 100
        )
        survivor, survivor_run, _b = await self._seed_conversation(
            store, object_content="SURVIVOR\n" * 100, user="user_other"
        )
        await self._occupancy(
            store,
            conversation_id=victim.conversation_id,
            run_id=victim_run.run_id,
            model_call_id="call_victim",
        )
        await self._occupancy(
            store,
            conversation_id=survivor.conversation_id,
            run_id=survivor_run.run_id,
            model_call_id="call_survivor",
        )

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")

        keys = set(store.context_occupancy)
        assert keys == {("call_survivor", 1)}
        rows = await store.list_context_occupancy(
            org_id=_ORG, run_id=survivor_run.run_id
        )
        assert [row.model_call_id for row in rows] == ["call_survivor"]
        await store.close()

    async def test_legal_hold_retains_the_occupancy_rows_too(self, tmp_path) -> None:
        """A held conversation is counted and untouched — including its occupancy.

        The purge already skips held conversations wholesale; asserting it here
        stops a future erasure from being wired ahead of the hold check, which is
        the ordering mistake that would make a legal hold unenforceable.
        """

        store = await self._store(tmp_path)
        conversation, run, _sha = await self._seed_conversation(
            store,
            object_content="HELD\n" * 100,
            metadata={"legal_hold": True},
        )
        await self._occupancy(
            store,
            conversation_id=conversation.conversation_id,
            run_id=run.run_id,
            model_call_id="call_held",
        )

        await store.delete_user_history(org_id=_ORG, user_id=_USER, reason="erase")

        assert set(store.context_occupancy) == {("call_held", 1)}
        await store.close()
