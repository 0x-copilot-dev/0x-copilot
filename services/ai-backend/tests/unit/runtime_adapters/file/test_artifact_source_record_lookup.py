"""Durability test for exact result-source records used by artifact promotion."""

from __future__ import annotations

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


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


async def _seed_run(store: FileRuntimeApiStore):
    settings = _settings()
    producer = RuntimeEventProducer(
        persistence=store,
        event_store=store,
        on_event_appended=None,
    )
    coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=producer,
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )
    conversations = ConversationCoordinator(
        persistence=store,
        settings=settings,
        run_coordinator=coordinator,
    )
    conversation = await conversations.create_conversation(
        CreateConversationRequest(
            org_id="org_source_record",
            user_id="user_source_record",
            assistant_id="assistant_source_record",
        )
    )
    created = await coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_source_record",
            user_id="user_source_record",
            user_input="make a result",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    run = await store.get_run(org_id="org_source_record", run_id=created.run_id)
    assert run is not None
    return run, conversation


class TestFileArtifactSourceRecordLookup:
    async def test_exact_event_id_lookup_survives_fresh_store_reopen(self, tmp_path):
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        try:
            run, conversation = await _seed_run(store)
            event_id = "op_123e4567-e89b-42d3-a456-426614174000"
            appended = await store.append_event(
                RuntimeEventDraft(
                    org_id=run.org_id,
                    event_id=event_id,
                    run_id=run.run_id,
                    conversation_id=conversation.conversation_id,
                    trace_id=run.trace_id,
                    source=StreamEventSource.RUNTIME,
                    event_type=RuntimeApiEventType.TOOL_RESULT,
                    payload={"output": "durable result"},
                )
            )
        finally:
            await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        try:
            found = await reopened.get_event_by_id(
                org_id=run.org_id,
                run_id=run.run_id,
                event_id=event_id,
            )
            assert found == appended
            assert (
                await reopened.get_event_by_id(
                    org_id="org_foreign",
                    run_id=run.run_id,
                    event_id=event_id,
                )
                is None
            )
        finally:
            await reopened.close()
