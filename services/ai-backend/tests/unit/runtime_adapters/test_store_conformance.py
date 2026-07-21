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
    behaviours are exercised by the DB-gated suite under ``postgres/``. It never
    requires a database in CI.
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
