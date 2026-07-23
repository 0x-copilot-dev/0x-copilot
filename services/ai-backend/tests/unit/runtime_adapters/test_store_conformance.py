"""Runtime-store port-conformance suite, parametrized across backends.

The same behaviors run against every non-Postgres runtime store backend
(``in_memory`` and ``file``). The Postgres adapter has its own DB-gated suite
under ``postgres/``; these are the backends that need no external service.

Covers the queue claim/retry/dead-letter lifecycle and message-regeneration
parent reuse — the behaviors the in-memory suite historically owned — now
exercised through the shared port surface so a new backend cannot silently
diverge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.persistence.records import RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeRunCommand,
)


@pytest.fixture(
    params=[
        "in_memory",
        "file",
        pytest.param("postgres", marks=pytest.mark.postgres),
    ]
)
async def store(request, tmp_path):
    """Yield an opened runtime store for each conformance backend.

    ``in_memory`` and ``file`` are the CI backends (no external service). The
    ``postgres`` param is present so the shared contract *names* every backend,
    but it skips unless a live database is wired up — the real Postgres
    behaviours are exercised by the DB-gated suite under ``postgres/`` (the
    PRD-08 tool-invocation ledger specifically by
    ``postgres/test_tool_invocation_ledger.py``). It never requires a database
    in CI.
    """

    if request.param == "in_memory":
        instance = InMemoryRuntimeApiStore()
    elif request.param == "file":
        instance = FileRuntimeApiStore(tmp_path / "store")
    else:
        pytest.skip(
            "postgres conformance needs a live database; covered by the "
            "DB-gated suite under tests/unit/runtime_adapters/postgres/"
        )
    await instance.open()
    try:
        yield instance
    finally:
        await instance.close()


class _CrudSeedMixin:
    """Shared conversation/run seeding over the port surface (backend-agnostic)."""

    _ORG = "org_conf"
    _USER = "user_conf"

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    def _run_coordinator(self, store) -> RunCoordinator:
        settings = self._settings()
        return RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )

    async def _new_conversation(self, store, *, user_id=None, title="conf"):
        return await store.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG,
                user_id=user_id or self._USER,
                assistant_id="assistant",
                title=title,
            )
        )

    async def _new_run(self, store):
        run_coordinator = self._run_coordinator(store)
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=self._settings(),
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG, user_id=self._USER, assistant_id="assistant"
            )
        )
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=self._ORG,
                user_id=self._USER,
                user_input="hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return conversation, run

    async def _append_event(self, store, *, run, conversation_id, summary):
        return await store.append_event(
            RuntimeEventDraft(
                org_id=self._ORG,
                run_id=run.run_id,
                conversation_id=conversation_id,
                trace_id="trace_conf",
                source=StreamEventSource.MAIN_AGENT,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                summary=summary,
            )
        )


class TestConversationCrudConformance(_CrudSeedMixin):
    """create / get / list behave identically across backends."""

    async def test_create_get_and_list_round_trip(self, store) -> None:
        created = await self._new_conversation(store, title="planning")
        got = await store.get_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
        )
        assert got is not None
        assert got.conversation_id == created.conversation_id
        listed = await store.list_conversations(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert any(c.conversation_id == created.conversation_id for c in listed)

    async def test_get_is_scoped_by_user(self, store) -> None:
        created = await self._new_conversation(store)
        # Wrong user cannot read another user's conversation.
        assert (
            await store.get_conversation(
                org_id=self._ORG,
                user_id="intruder",
                conversation_id=created.conversation_id,
            )
            is None
        )


class TestConversationProjectConformance(_CrudSeedMixin):
    """PRD-07 — project_id round-trip, filter, and grouped count (all backends)."""

    async def _project_conversation(self, store, *, project_id, title="p"):
        return await store.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG,
                user_id=self._USER,
                assistant_id="assistant",
                title=title,
                project_id=project_id,
            )
        )

    async def test_project_id_round_trips_through_create_and_get(self, store) -> None:
        created = await self._project_conversation(store, project_id="p1")
        assert created.project_id == "p1"
        got = await store.get_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
        )
        assert got is not None
        assert got.project_id == "p1"

    async def test_list_conversations_filters_by_project(self, store) -> None:
        on_p1 = await self._project_conversation(store, project_id="p1", title="a")
        on_p2 = await self._project_conversation(store, project_id="p2", title="b")
        no_project = await self._new_conversation(store, title="c")

        p1_ids = {
            c.conversation_id
            for c in await store.list_conversations(
                org_id=self._ORG, user_id=self._USER, limit=50, project_id="p1"
            )
        }
        assert on_p1.conversation_id in p1_ids
        assert on_p2.conversation_id not in p1_ids
        assert no_project.conversation_id not in p1_ids

        # p2 sees only its own; an unknown project sees none.
        p2_ids = {
            c.conversation_id
            for c in await store.list_conversations(
                org_id=self._ORG, user_id=self._USER, limit=50, project_id="p2"
            )
        }
        assert p2_ids == {on_p2.conversation_id}
        empty = await store.list_conversations(
            org_id=self._ORG, user_id=self._USER, limit=50, project_id="p-unknown"
        )
        assert list(empty) == []

    async def test_count_conversations_by_project_is_grouped(self, store) -> None:
        for _ in range(3):
            await self._project_conversation(store, project_id="p1")
        await self._project_conversation(store, project_id="p2")

        counts = await store.count_conversations_by_project(
            org_id=self._ORG,
            user_id=self._USER,
            project_ids=("p1", "p2", "p3"),
        )
        assert counts.get("p1") == 3
        assert counts.get("p2") == 1
        # p3 has no chats → absent from the grouped map (caller renders 0).
        assert "p3" not in counts

    async def test_count_conversations_by_project_is_identity_scoped(
        self, store
    ) -> None:
        # Two chats on p1 for _USER; one for another user in the same org.
        await self._project_conversation(store, project_id="p1")
        await self._project_conversation(store, project_id="p1")
        await store.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG,
                user_id="other_user",
                assistant_id="assistant",
                title="theirs",
                project_id="p1",
            )
        )
        mine = await store.count_conversations_by_project(
            org_id=self._ORG, user_id=self._USER, project_ids=("p1",)
        )
        assert mine.get("p1") == 2
        theirs = await store.count_conversations_by_project(
            org_id=self._ORG, user_id="other_user", project_ids=("p1",)
        )
        assert theirs.get("p1") == 1

    async def test_patch_files_and_unfiles_a_conversation(self, store) -> None:
        created = await self._new_conversation(store, title="loose")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # File it into p1.
        filed = await store.update_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
            title=None,
            title_changed=False,
            folder=None,
            folder_changed=False,
            archived=None,
            archived_changed=False,
            project_id="p1",
            project_id_changed=True,
            now=now,
        )
        assert filed is not None and filed.project_id == "p1"
        # An omitted project_id (project_id_changed=False) leaves it untouched.
        untouched = await store.update_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
            title="renamed",
            title_changed=True,
            folder=None,
            folder_changed=False,
            archived=None,
            archived_changed=False,
            project_id=None,
            project_id_changed=False,
            now=now,
        )
        assert untouched is not None and untouched.project_id == "p1"
        # Explicit null unfiles it.
        unfiled = await store.update_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
            title=None,
            title_changed=False,
            folder=None,
            folder_changed=False,
            archived=None,
            archived_changed=False,
            project_id=None,
            project_id_changed=True,
            now=now,
        )
        assert unfiled is not None and unfiled.project_id is None


class TestMessageOrderingConformance(_CrudSeedMixin):
    """append_message + list_messages preserve creation order per backend."""

    async def test_messages_are_ordered_by_created_at(self, store) -> None:
        conversation = await self._new_conversation(store)
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Append out of chronological order; the store must sort ascending.
        for offset in (2, 0, 1):
            await store.append_message(
                MessageRecord(
                    conversation_id=conversation.conversation_id,
                    org_id=self._ORG,
                    role=MessageRole.USER,
                    content_text=f"msg-{offset}",
                    created_at=base + timedelta(seconds=offset),
                )
            )
        messages = await store.list_messages(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
            limit=50,
        )
        texts = [m.content_text for m in messages]
        assert texts == ["msg-0", "msg-1", "msg-2"]


class TestMessageKeysetWindowConformance(_CrudSeedMixin):
    """list_messages returns the most-recent window (ASC) with keyset paging.

    AD-12 / NFR-7: a long conversation's newest turns must be reachable. The
    port returns the TAIL (newest ``limit``), reversed to ascending, and pages
    backwards through the ``(before_created_at, before_message_id)`` keyset.
    """

    async def _seed(self, store, conversation, *, count):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for offset in range(count):
            await store.append_message(
                MessageRecord(
                    conversation_id=conversation.conversation_id,
                    org_id=self._ORG,
                    role=MessageRole.USER,
                    content_text=f"msg-{offset:02d}",
                    created_at=base + timedelta(seconds=offset),
                )
            )

    async def test_returns_newest_window_ascending_when_truncated(self, store) -> None:
        conversation = await self._new_conversation(store)
        await self._seed(store, conversation, count=10)
        page = await store.list_messages(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
            limit=3,
        )
        texts = [m.content_text for m in page]
        # The TAIL (newest three), not the head, in ascending order.
        assert texts == ["msg-07", "msg-08", "msg-09"]
        # Ascending: created_at is non-decreasing and the last row is newest.
        created = [m.created_at for m in page]
        assert created == sorted(created)
        assert page[-1].content_text == "msg-09"

    async def test_before_keyset_returns_strictly_older_page_ascending(
        self, store
    ) -> None:
        conversation = await self._new_conversation(store)
        await self._seed(store, conversation, count=10)
        first = await store.list_messages(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
            limit=3,
        )
        oldest = first[0]  # ASC → oldest of the newest window (msg-07)
        older = await store.list_messages(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
            limit=3,
            before_created_at=oldest.created_at,
            before_message_id=oldest.message_id,
        )
        older_texts = [m.content_text for m in older]
        # Strictly older than the prior window's oldest, still ascending.
        assert older_texts == ["msg-04", "msg-05", "msg-06"]
        assert all(m.created_at < oldest.created_at for m in older)


class TestEventOrderingConformance(_CrudSeedMixin):
    """Event sequence is monotonic, contiguous, and cursor-replayable."""

    async def test_sequence_is_contiguous_and_cursor_replayable(self, store) -> None:
        conversation, run = await self._new_run(store)
        base = await store.get_latest_sequence(run_id=run.run_id)
        for i in range(5):
            await self._append_event(
                store,
                run=run,
                conversation_id=conversation.conversation_id,
                summary=f"e{i}",
            )
        events = await store.list_events_after(
            org_id=self._ORG, run_id=run.run_id, after_sequence=0
        )
        sequences = [e.sequence_no for e in events]
        assert sequences == list(range(1, len(events) + 1))
        assert await store.get_latest_sequence(run_id=run.run_id) == len(events)
        # A cursor after N returns only the strictly-greater suffix.
        suffix = await store.list_events_after(
            org_id=self._ORG, run_id=run.run_id, after_sequence=base + 2
        )
        assert [e.sequence_no for e in suffix] == [
            base + 3,
            base + 4,
            base + 5,
        ]


class TestIdempotencyConformance(_CrudSeedMixin):
    """Idempotent creates and last-write-wins upserts match across backends."""

    async def test_conversation_creation_is_idempotent(self, store) -> None:
        first = await store.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG,
                user_id=self._USER,
                assistant_id="assistant",
                idempotency_key="dedupe-key-1",
            )
        )
        second = await store.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG,
                user_id=self._USER,
                assistant_id="assistant",
                idempotency_key="dedupe-key-1",
            )
        )
        assert first.conversation_id == second.conversation_id
        listed = await store.list_conversations(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        matching = [c for c in listed if c.conversation_id == first.conversation_id]
        assert len(matching) == 1

    async def test_message_upsert_is_last_write_wins(self, store) -> None:
        conversation = await self._new_conversation(store)
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        original = MessageRecord(
            conversation_id=conversation.conversation_id,
            org_id=self._ORG,
            role=MessageRole.ASSISTANT,
            content_text="v1",
            created_at=created,
        )
        await store.append_message(original)
        await store.append_message(original.model_copy(update={"content_text": "v2"}))
        messages = await store.list_messages(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
            limit=50,
        )
        same_id = [m for m in messages if m.message_id == original.message_id]
        assert len(same_id) == 1  # no duplicate row
        assert same_id[0].content_text == "v2"


class TestTenantIsolationConformance(_CrudSeedMixin):
    """One org's conversations are invisible to another org."""

    async def test_conversation_is_not_visible_to_another_org(self, store) -> None:
        created = await self._new_conversation(store)
        # get_conversation_for_org filters by org only — a different org misses.
        assert (
            await store.get_conversation_for_org(
                org_id="other_org", conversation_id=created.conversation_id
            )
            is None
        )
        assert (
            await store.list_conversations(
                org_id="other_org", user_id=self._USER, limit=50
            )
            == ()
        )
        # Messages are scoped by org too.
        await store.append_message(
            MessageRecord(
                conversation_id=created.conversation_id,
                org_id=self._ORG,
                role=MessageRole.USER,
                content_text="tenant-a-secret",
            )
        )
        assert (
            await store.list_messages(
                org_id="other_org",
                conversation_id=created.conversation_id,
                limit=50,
            )
            == ()
        )


class TestSoftDeleteConformance(_CrudSeedMixin):
    """soft_delete_conversation hides by default, is idempotent, never truncates."""

    async def test_soft_delete_hides_and_is_idempotent(self, store) -> None:
        created = await self._new_conversation(store)
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        deleted = await store.soft_delete_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
            now=now,
        )
        assert deleted is not None
        assert deleted.deleted_at is not None
        # Hidden from the default listing...
        default_list = await store.list_conversations(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert all(c.conversation_id != created.conversation_id for c in default_list)
        # ...but still retrievable with include_deleted (never truncated away).
        with_deleted = await store.list_conversations(
            org_id=self._ORG,
            user_id=self._USER,
            limit=50,
            include_deleted=True,
        )
        assert any(c.conversation_id == created.conversation_id for c in with_deleted)
        # Idempotent re-delete returns the already-deleted record.
        again = await store.soft_delete_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=created.conversation_id,
            now=datetime(2026, 6, 2, tzinfo=timezone.utc),
        )
        assert again is not None
        assert again.conversation_id == created.conversation_id


class TestRuntimeQueueLifecycleConformance:
    """Queue claim, retry, and dead-letter transitions across backends."""

    async def test_claim_retry_and_dead_letter(self, store) -> None:
        command = RuntimeRunCommand(
            run_id="run_123",
            conversation_id="conversation_123",
            org_id="org_123",
            user_id="user_123",
            trace_id="trace_123",
            runtime_context={
                "user_id": "user_123",
                "org_id": "org_123",
                "roles": ["employee"],
                "permission_scopes": ["docs:read"],
                "connector_scopes": {},
                "model_profile": {
                    "provider": "fake",
                    "model_name": "fake-enterprise-model",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                "request_id": "request_123",
                "run_id": "run_123",
                "trace_id": "trace_123",
            },
        )

        await store.enqueue_run(command)
        first_claim = await store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        assert first_claim is not None
        assert first_claim.run_id == "run_123"
        # A second worker cannot claim the locked command.
        assert (
            await store.claim_next(
                worker_id="worker_2",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            is None
        )

        await store.mark_retry(
            result=RuntimeWorkerResult(
                command_id=first_claim.command_id,
                succeeded=False,
                retry_available_at=datetime.now(timezone.utc),
            )
        )
        retry_claim = await store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        assert retry_claim is not None
        assert retry_claim.attempts == 2

        await store.mark_dead_letter(
            result=RuntimeWorkerResult(
                command_id=retry_claim.command_id, succeeded=False
            )
        )
        assert (
            await store.claim_next(
                worker_id="worker_3",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            is None
        )


class TestRegenerateMessageConformance:
    """Regeneration re-uses the original parent user message across backends."""

    async def test_regenerate_reuses_parent_user_message(self, store) -> None:
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        model_resolver = ModelConfigResolver(settings)
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=model_resolver,
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id="org_123", user_id="user_123", assistant_id="assistant_123"
            )
        )
        first = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="Original question",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        assistant = await store.append_message(
            store.messages[first.user_message_id].model_copy(
                update={
                    "message_id": "assistant_123",
                    "run_id": first.run_id,
                    "role": MessageRole.ASSISTANT,
                    "content_text": "Original answer",
                    "parent_message_id": first.user_message_id,
                }
            )
        )

        regenerated = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="Regenerate",
                regenerate_from_message_id=assistant.message_id,
                branch_id="branch_retry",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )

        user_messages = [
            message
            for message in store.messages.values()
            if message.role == MessageRole.USER
        ]
        assert len(user_messages) == 1
        assert regenerated.user_message_id == first.user_message_id
        assert (
            store.runs[regenerated.run_id].runtime_context.trace_metadata["branch_id"]
            == "branch_retry"
        )


class TestConversationPinConformance(_CrudSeedMixin):
    """PRD-H.4 — pin toggle + list-field source helpers across backends."""

    async def test_set_pinned_toggles_and_persists(self, store) -> None:
        conversation = await self._new_conversation(store)
        assert conversation.pinned is False

        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        pinned = await store.set_conversation_pinned(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
            pinned=True,
            now=now,
        )
        assert pinned is not None
        assert pinned.pinned is True

        # Persisted: a fresh read reflects the flag.
        reread = await store.get_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
        )
        assert reread is not None
        assert reread.pinned is True

        # And it rides along on the list projection.
        listed = await store.list_conversations(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        row = next(
            c for c in listed if c.conversation_id == conversation.conversation_id
        )
        assert row.pinned is True

        # Unpin returns it to the default bucket.
        unpinned = await store.set_conversation_pinned(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
            pinned=False,
            now=now + timedelta(minutes=1),
        )
        assert unpinned is not None
        assert unpinned.pinned is False

    async def test_set_pinned_is_idempotent_no_updated_at_churn(self, store) -> None:
        conversation = await self._new_conversation(store)
        now = datetime(2026, 2, 2, tzinfo=timezone.utc)
        first = await store.set_conversation_pinned(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
            pinned=True,
            now=now,
        )
        assert first is not None
        # Re-pin at a later timestamp: no-op must not reshuffle updated_at.
        again = await store.set_conversation_pinned(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
            pinned=True,
            now=now + timedelta(hours=1),
        )
        assert again is not None
        assert again.pinned is True
        assert again.updated_at == first.updated_at

    async def test_set_pinned_is_scoped_by_user(self, store) -> None:
        conversation = await self._new_conversation(store)
        # A different user in the same org cannot pin someone else's chat.
        result = await store.set_conversation_pinned(
            org_id=self._ORG,
            user_id="intruder",
            conversation_id=conversation.conversation_id,
            pinned=True,
            now=datetime(2026, 2, 3, tzinfo=timezone.utc),
        )
        assert result is None
        # The owner's row is untouched.
        owner_view = await store.get_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
        )
        assert owner_view is not None
        assert owner_view.pinned is False

    async def test_latest_message_and_run_projection_sources(self, store) -> None:
        conversation, run = await self._new_run(store)
        latest_message = await store.get_latest_message_for_conversation(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
        )
        assert latest_message is not None
        # The seeded run created a user message "hello".
        assert latest_message.content_text == "hello"

        latest_run = await store.get_latest_run_for_conversation(
            org_id=self._ORG,
            conversation_id=conversation.conversation_id,
        )
        assert latest_run is not None
        assert latest_run.run_id == run.run_id
        assert latest_run.model_name == "gpt-5.4-mini"

    async def test_latest_message_and_run_none_when_empty(self, store) -> None:
        conversation = await self._new_conversation(store)
        assert (
            await store.get_latest_message_for_conversation(
                org_id=self._ORG,
                conversation_id=conversation.conversation_id,
            )
            is None
        )
        assert (
            await store.get_latest_run_for_conversation(
                org_id=self._ORG,
                conversation_id=conversation.conversation_id,
            )
            is None
        )


class TestRunHistory(_CrudSeedMixin):
    """PRD-05 — ``list_runs_for_org`` is the org-scoped, all-status, newest-first,
    keyset-paginated run history behind ``GET /v1/agent/runs``.

    Runs identically against ``in_memory`` + ``file`` (the ``postgres`` param is
    present-but-marked so the contract names it). This is the capability that
    makes FINISHED runs reachable — the defect this PRD exists to fix.
    """

    async def _conv(self, store, *, org_id=None, user_id=None, title="run-history"):
        return await store.create_conversation(
            CreateConversationRequest(
                org_id=org_id or self._ORG,
                user_id=user_id or self._USER,
                assistant_id="assistant",
                title=title,
            )
        )

    async def _seed_run(
        self,
        store,
        *,
        conversation,
        idem,
        org_id=None,
        user_id=None,
        status=None,
    ):
        run_coordinator = self._run_coordinator(store)
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=org_id or self._ORG,
                user_id=user_id or self._USER,
                user_input="hello",
                idempotency_key=idem,
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        if status is not None:
            run = await store.update_run_status(run_id=run.run_id, status=status)
        return run

    async def test_completed_run_is_returned(self, store) -> None:
        """Regression guard for this PRD's bug: a COMPLETED run is reachable by a
        list caller — impossible on ``main`` (every adapter's active-run query
        filters terminal statuses out)."""
        conversation = await self._conv(store)
        run = await self._seed_run(
            store,
            conversation=conversation,
            idem="run-done",
            status=AgentRunStatus.COMPLETED,
        )
        history = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert len(history) == 1
        assert history[0].run_id == run.run_id
        assert history[0].status is AgentRunStatus.COMPLETED
        assert history[0].conversation_id == conversation.conversation_id
        assert history[0].conversation_title == "run-history"

    async def test_all_eight_statuses_are_reachable(self, store) -> None:
        conversation = await self._conv(store)
        statuses = list(AgentRunStatus)
        assert len(statuses) == 8
        for index, status in enumerate(statuses):
            await self._seed_run(
                store,
                conversation=conversation,
                idem=f"run-status-{index}",
                status=status,
            )
        history = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert {entry.status for entry in history} == set(statuses)

    async def test_ordering_and_keyset(self, store) -> None:
        conversation = await self._conv(store)
        for index in range(10):
            await self._seed_run(
                store, conversation=conversation, idem=f"run-order-{index}"
            )

        full = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert len(full) == 10
        keys = [(entry.created_at, entry.run_id) for entry in full]
        # Strictly descending on the (created_at, run_id) composite.
        assert all(keys[i] > keys[i + 1] for i in range(len(keys) - 1))

        # Page 1, then page 2 via the oldest row's keyset — disjoint, ordered.
        page1 = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=3
        )
        oldest = page1[-1]
        page2 = await store.list_runs_for_org(
            org_id=self._ORG,
            user_id=self._USER,
            limit=3,
            before_created_at=oldest.created_at,
            before_run_id=oldest.run_id,
        )
        ids1 = {entry.run_id for entry in page1}
        ids2 = {entry.run_id for entry in page2}
        assert ids1.isdisjoint(ids2)

        # Concatenating pages of size 3 reproduces the single-page ordering.
        walked: list = []
        before_created_at = None
        before_run_id = None
        while True:
            page = await store.list_runs_for_org(
                org_id=self._ORG,
                user_id=self._USER,
                limit=3,
                before_created_at=before_created_at,
                before_run_id=before_run_id,
            )
            if not page:
                break
            walked.extend(page)
            before_created_at = page[-1].created_at
            before_run_id = page[-1].run_id
        assert [entry.run_id for entry in walked] == [entry.run_id for entry in full]

    async def test_is_scoped_by_org_and_user(self, store) -> None:
        conv_a = await self._conv(store, org_id="org_a", user_id="user_a")
        conv_b_org = await self._conv(store, org_id="org_b", user_id="user_a")
        conv_a_other = await self._conv(store, org_id="org_a", user_id="user_b")
        mine = await self._seed_run(
            store,
            conversation=conv_a,
            idem="mine",
            org_id="org_a",
            user_id="user_a",
        )
        await self._seed_run(
            store,
            conversation=conv_b_org,
            idem="other-org",
            org_id="org_b",
            user_id="user_a",
        )
        await self._seed_run(
            store,
            conversation=conv_a_other,
            idem="other-user",
            org_id="org_a",
            user_id="user_b",
        )
        history = await store.list_runs_for_org(
            org_id="org_a", user_id="user_a", limit=50
        )
        assert [entry.run_id for entry in history] == [mine.run_id]

    async def test_soft_deleted_conversation_runs_are_hidden(self, store) -> None:
        conversation = await self._conv(store)
        await self._seed_run(
            store,
            conversation=conversation,
            idem="run-softdel",
            status=AgentRunStatus.COMPLETED,
        )
        before = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert len(before) == 1

        await store.soft_delete_conversation(
            org_id=self._ORG,
            user_id=self._USER,
            conversation_id=conversation.conversation_id,
            now=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        after = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert after == ()

    async def test_history_deletion_clears_run_history(self, store) -> None:
        conversation = await self._conv(store)
        await self._seed_run(
            store,
            conversation=conversation,
            idem="run-hist-del",
            status=AgentRunStatus.COMPLETED,
        )
        assert (
            len(
                await store.list_runs_for_org(
                    org_id=self._ORG, user_id=self._USER, limit=50
                )
            )
            == 1
        )

        await store.delete_user_history(org_id=self._ORG, user_id=self._USER)
        after = await store.list_runs_for_org(
            org_id=self._ORG, user_id=self._USER, limit=50
        )
        assert after == ()


class TestToolInvocationLedgerConformance(_CrudSeedMixin):
    """PRD-08 D1b — record_tool_invocation + the two Activity meta aggregates
    behave identically across in_memory / file / (DB-gated) postgres."""

    async def _conv(self, store):
        return await store.create_conversation(
            CreateConversationRequest(
                org_id=self._ORG, user_id=self._USER, assistant_id="assistant"
            )
        )

    async def _seed_run(self, store, conversation, idem):
        return await self._run_coordinator(store).create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=self._ORG,
                user_id=self._USER,
                user_input="hello",
                idempotency_key=idem,
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )

    async def test_counts_steps_and_distinct_connectors_for_a_run(self, store):
        from agent_runtime.persistence.records import ToolInvocationRecord

        conversation = await self._conv(store)
        run = await self._seed_run(store, conversation, idem="ti-count")

        # 7 tool invocations across 4 distinct connectors (2 native → None).
        connectors = ["sheets", "safe", "dune", "sheets", "docs", None, None]
        for i, slug in enumerate(connectors):
            await store.record_tool_invocation(
                ToolInvocationRecord(
                    run_id=run.run_id,
                    org_id=self._ORG,
                    tool_name=f"tool_{i}",
                    connector_slug=slug,
                    call_id=f"call_{i}",
                )
            )

        counts = await store.count_tool_invocations_for_runs(
            org_id=self._ORG, run_ids=[run.run_id]
        )
        # (step_count, connector_count)
        assert counts[run.run_id] == (7, 4)

    async def test_run_without_invocations_is_absent_from_the_map(self, store):
        conversation = await self._conv(store)
        run = await self._seed_run(store, conversation, idem="ti-empty")
        counts = await store.count_tool_invocations_for_runs(
            org_id=self._ORG, run_ids=[run.run_id]
        )
        # Absent — the service renders None/unknown, never (0, 0).
        assert run.run_id not in counts

    async def test_upsert_is_idempotent_on_invocation_id(self, store):
        from agent_runtime.persistence.records import (
            ToolInvocationRecord,
            ToolInvocationStatus,
        )

        conversation = await self._conv(store)
        run = await self._seed_run(store, conversation, idem="ti-upsert")
        rec = ToolInvocationRecord(
            run_id=run.run_id,
            org_id=self._ORG,
            tool_name="tool_x",
            connector_slug="github",
            call_id="call_x",
        )
        await store.record_tool_invocation(rec)
        # Same invocation_id, settled — must NOT create a second row.
        await store.record_tool_invocation(
            rec.model_copy(update={"status": ToolInvocationStatus.COMPLETED})
        )
        counts = await store.count_tool_invocations_for_runs(
            org_id=self._ORG, run_ids=[run.run_id]
        )
        assert counts[run.run_id] == (1, 1)

    async def test_counts_pending_approvals_for_a_run(self, store):
        from runtime_api.schemas import ApprovalRequestRecord

        conversation = await self._conv(store)
        run = await self._seed_run(store, conversation, idem="ti-appr")

        # Two pending approvals on this run.
        for i in range(2):
            await store.create_approval_request(
                record=ApprovalRequestRecord(
                    approval_id=f"appr_{i}",
                    run_id=run.run_id,
                    conversation_id=conversation.conversation_id,
                    org_id=self._ORG,
                    user_id=self._USER,
                    metadata={"message": "approve a swap", "risk_level": "low"},
                )
            )

        pending = await store.count_pending_approvals_for_runs(
            org_id=self._ORG, run_ids=[run.run_id]
        )
        assert pending[run.run_id] == 2

    async def test_tenant_isolation_on_the_aggregates(self, store):
        from agent_runtime.persistence.records import ToolInvocationRecord

        conversation = await self._conv(store)
        run = await self._seed_run(store, conversation, idem="ti-tenant")
        await store.record_tool_invocation(
            ToolInvocationRecord(
                run_id=run.run_id,
                org_id=self._ORG,
                tool_name="tool_a",
                connector_slug="sheets",
                call_id="call_a",
            )
        )
        # A different org must not see this run's counts.
        counts = await store.count_tool_invocations_for_runs(
            org_id="org_other", run_ids=[run.run_id]
        )
        assert run.run_id not in counts
