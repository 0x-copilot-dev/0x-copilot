from __future__ import annotations

import asyncio
from collections.abc import Sequence
import os
from uuid import uuid4

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for Postgres adapter tests.",
)


class TestPostgresAdapterRunLifecycle:
    """End-to-end run processing through the Postgres adapter."""

    def test_processes_run_and_persists_final_response(self) -> None:
        store = PostgresRuntimeApiStore(os.environ["TEST_DATABASE_URL"])
        store.migrate()
        suffix = uuid4().hex
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "postgres",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        conversation = asyncio.run(
            service.create_conversation(
                CreateConversationRequest(
                    org_id=f"org_{suffix}",
                    user_id=f"user_{suffix}",
                    assistant_id="assistant_test",
                )
            )
        )
        run = asyncio.run(
            service.create_run(
                CreateRunRequest(
                    conversation_id=conversation.conversation_id,
                    org_id=conversation.org_id,
                    user_id=conversation.user_id,
                    user_input="hi",
                    model={"provider": "openai", "model_name": "gpt-5.4-mini"},
                )
            )
        )

        def fake_agent_factory(
            *,
            context: AgentRuntimeContext,
            dependencies: RuntimeDependencies,
        ) -> RuntimeHarness:
            return RuntimeHarness(
                agent=object(),
                context=context,
                dependencies=dependencies,
                tools=(),
                mcp_servers=(),
                subagents=(),
                memory_backend=None,
                skill_directories=(),
            )

        async def fake_invoker(
            _harness: RuntimeHarness, _messages: Sequence[object]
        ) -> object:
            return {"messages": [{"role": "assistant", "content": "hi there"}]}

        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                agent_factory=fake_agent_factory,
                runtime_invoker=fake_invoker,
            ),
        )

        assert asyncio.run(worker.run_until_idle()) >= 1
        completed = asyncio.run(
            service.get_run(
                org_id=conversation.org_id,
                user_id=conversation.user_id,
                run_id=run.run_id,
            )
        )
        replay = asyncio.run(
            service.replay_events(
                org_id=conversation.org_id,
                user_id=conversation.user_id,
                run_id=run.run_id,
                after_sequence=0,
            )
        )

        assert completed.status == "completed"
        assert [event.event_type for event in replay.events] == [
            "run_queued",
            "run_started",
            "final_response",
            "run_completed",
        ]
        assert replay.events[2].payload["message"] == "hi there"


class TestPostgresAdapterSyntheticParent:
    """Synthetic assistant-<run_id> parent IDs resolve to real message IDs."""

    def test_resolves_live_assistant_parent_id(self) -> None:
        store = PostgresRuntimeApiStore(os.environ["TEST_DATABASE_URL"])
        store.migrate()
        suffix = uuid4().hex
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "postgres",
                "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        conversation = asyncio.run(
            service.create_conversation(
                CreateConversationRequest(
                    org_id=f"org_{suffix}",
                    user_id=f"user_{suffix}",
                    assistant_id="assistant_test",
                )
            )
        )
        first = asyncio.run(
            service.create_run(
                CreateRunRequest(
                    conversation_id=conversation.conversation_id,
                    org_id=conversation.org_id,
                    user_id=conversation.user_id,
                    user_input="Remember this Postgres detail.",
                    model={"provider": "openai", "model_name": "gpt-5.4-mini"},
                )
            )
        )
        assistant = store.append_message(
            MessageRecord(
                message_id=f"assistant_{suffix}",
                conversation_id=conversation.conversation_id,
                org_id=conversation.org_id,
                run_id=first.run_id,
                role=MessageRole.ASSISTANT,
                content_text="Postgres detail remembered.",
                parent_message_id=first.user_message_id,
            )
        )

        follow_up = asyncio.run(
            service.create_run(
                CreateRunRequest(
                    conversation_id=conversation.conversation_id,
                    org_id=conversation.org_id,
                    user_id=conversation.user_id,
                    user_input="What detail did I ask you to remember?",
                    parent_message_id=f"assistant-{first.run_id}",
                    model={"provider": "openai", "model_name": "gpt-5.4-mini"},
                )
            )
        )
        messages = asyncio.run(
            service.list_messages(
                org_id=conversation.org_id,
                user_id=conversation.user_id,
                conversation_id=conversation.conversation_id,
            )
        ).messages
        follow_up_user = next(
            message
            for message in messages
            if message.message_id == follow_up.user_message_id
        )

        assert follow_up_user.parent_message_id == assistant.message_id
