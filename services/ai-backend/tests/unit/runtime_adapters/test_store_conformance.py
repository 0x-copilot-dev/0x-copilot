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
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.persistence.records import RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRole,
    RuntimeRunCommand,
)


@pytest.fixture(params=["in_memory", "file"])
async def store(request, tmp_path):
    """Yield an opened runtime store for each conformance backend."""

    if request.param == "in_memory":
        instance = InMemoryRuntimeApiStore()
    else:
        instance = FileRuntimeApiStore(tmp_path / "store")
    await instance.open()
    try:
        yield instance
    finally:
        await instance.close()


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
