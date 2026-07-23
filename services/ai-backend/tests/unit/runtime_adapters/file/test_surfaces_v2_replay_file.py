"""PRD-A3 DoD: restart/reopen replay reconstructs identical SurfaceStore state.

Append the four ledger events through the real producer against a file-backed
store, fold once, then reopen a *fresh* ``FileRuntimeApiStore`` over the same
root, replay ``list_events_after(0)``, and fold again — the two
``SurfaceStoreState`` values must be equal field-for-field (the canvas is a pure
replay of the ledger, SDR §6).
"""

from __future__ import annotations

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
)

_ORG = "org_file_v2"
_USER = "user_file_v2"
_SURFACE_ID = "record://linear/get_issue/issue-1"


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
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=producer,
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )
    conv_coordinator = ConversationCoordinator(
        persistence=store, settings=settings, run_coordinator=run_coordinator
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(org_id=_ORG, user_id=_USER, assistant_id="assistant")
    )
    run_response = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input="Read it.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    run = await store.get_run(org_id=_ORG, run_id=run_response.run_id)
    return producer, run


async def _append_ledger(producer: RuntimeEventProducer, run) -> None:
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.ACTION_CLASSIFIED,
        payload={
            "v": 1,
            "call_id": "c1",
            "connector": "linear",
            "op": "get_issue",
            "class": "unknown",
            "basis": "default",
        },
    )
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.READ_EXECUTED,
        payload={
            "v": 1,
            "call_id": "c1",
            "connector": "linear",
            "op": "get_issue",
            "latency_ms": 7,
            "payload_ref": "call:c1",
        },
    )
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.SURFACE_CREATED,
        payload={
            "v": 1,
            "surface_id": _SURFACE_ID,
            "kind": "record",
            "source": {"connector": "linear", "op": "get_issue"},
            "title": "ENG-1 Fix",
            "payload_ref": "call:c1",
        },
    )
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.VIEW_DERIVED,
        payload={
            "v": 1,
            "surface_id": _SURFACE_ID,
            "tier": "shaped",
            "basis": "registry",
        },
    )


class TestFileStoreSurfacesReplay:
    async def test_reopen_reconstructs_identical_surface_store_state(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        producer, run = await _seed_run(store)
        await _append_ledger(producer, run)

        first_events = await store.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        first_state = SurfaceStoreProjection.fold(run.run_id, first_events)
        await store.close()

        # Reopen a FRESH instance over the same root — nothing in memory carries.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        replayed_events = await reopened.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        replayed_state = SurfaceStoreProjection.fold(run.run_id, replayed_events)
        await reopened.close()

        assert replayed_state == first_state
        assert len(replayed_state.surfaces) == 1
        surface = replayed_state.surfaces[0]
        assert surface.surface_id == _SURFACE_ID
        assert surface.view is not None
        assert surface.view.tier == "shaped"
