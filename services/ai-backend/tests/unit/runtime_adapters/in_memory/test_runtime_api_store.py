from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRole,
    RuntimeRunCommand,
)
from agent_runtime.persistence.records import RuntimeWorkerResult


class TestInMemoryRuntimeQueueLifecycle:
    """Queue claim, retry, and dead-letter transitions."""

    def test_claim_retry_and_dead_letter(self) -> None:
        store = InMemoryRuntimeApiStore()
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

        store.enqueue_run(command)
        first_claim = store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert first_claim is not None
        assert first_claim.run_id == "run_123"
        assert (
            store.claim_next(
                worker_id="worker_2",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            is None
        )

        store.mark_retry(
            result=RuntimeWorkerResult(
                command_id=first_claim.command_id,
                succeeded=False,
                retry_available_at=datetime.now(timezone.utc),
            )
        )
        retry_claim = store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert retry_claim is not None
        assert retry_claim.attempts == 2

        store.mark_dead_letter(
            result=RuntimeWorkerResult(
                command_id=retry_claim.command_id, succeeded=False
            )
        )
        assert (
            store.claim_next(
                worker_id="worker_3",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            is None
        )


class TestInMemoryRegenerateMessage:
    """Regeneration re-uses the original parent user message."""

    def test_regenerate_reuses_parent_user_message(self) -> None:
        store = InMemoryRuntimeApiStore()
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=RuntimeSettings.load(
                environ={
                    "OPENAI_API_KEY": "sk-test",
                    "RUNTIME_DEFAULT_PROVIDER": "openai",
                    "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                }
            ),
        )
        conversation = asyncio.run(
            service.create_conversation(
                CreateConversationRequest(
                    org_id="org_123",
                    user_id="user_123",
                    assistant_id="assistant_123",
                )
            )
        )
        first = asyncio.run(
            service.create_run(
                CreateRunRequest(
                    conversation_id=conversation.conversation_id,
                    org_id="org_123",
                    user_id="user_123",
                    user_input="Original question",
                    model={"provider": "openai", "model_name": "gpt-5.4-mini"},
                )
            )
        )
        assistant = store.append_message(
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

        regenerated = asyncio.run(
            service.create_run(
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
