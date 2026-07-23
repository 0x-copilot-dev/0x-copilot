"""``ConversationQueryService.list_run_surfaces`` + the surfaces endpoint (D7).

Real ``InMemoryRuntimeApiStore`` + ``RuntimeEventProducer``: a scope mismatch is
a 404; an empty run yields an empty surface list; appended ledger events fold
into the projected surfaces; and a ``"ref"``-keyed payload is marked OFFLOADED
(pinning D5). Ledger events are appended through the real producer so the
projector allow-lists run exactly as they do in production.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    StreamEventSource,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)
from runtime_api.schemas.common import RuntimeEventRedactionState


class RunSurfacesEndpointMixin:
    ORG = "org_123"
    USER = "user_123"

    async def _setup(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> tuple[
        InMemoryRuntimeApiStore,
        RuntimeEventProducer,
        ConversationQueryService,
        RunRecord,
    ]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        model_resolver = ModelConfigResolver(settings)
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=settings,
            model_resolver=model_resolver,
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=self.ORG, user_id=self.USER, title="Surfaces"
            )
        )
        run_response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=self.ORG,
                user_id=self.USER,
                user_input="Read the issue.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        cqs = ConversationQueryService(
            persistence=store,
            event_store=store,
            settings=settings,
            model_resolver=model_resolver,
        )
        return store, producer, cqs, store.runs[run_response.run_id]

    @staticmethod
    async def _append_surface_ledger(
        producer: RuntimeEventProducer, run: RunRecord, *, call_id: str = "call_1"
    ) -> None:
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.ACTION_CLASSIFIED,
            payload={
                "v": 1,
                "call_id": call_id,
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
                "call_id": call_id,
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 12,
                "payload_ref": f"call:{call_id}",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": "record://linear/get_issue/issue-1",
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue"},
                "title": "ENG-1 Fix",
                "payload_ref": f"call:{call_id}",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={
                "v": 1,
                "surface_id": "record://linear/get_issue/issue-1",
                "tier": "shaped",
                "basis": "registry",
            },
        )


class TestRunSurfacesEndpoint(RunSurfacesEndpointMixin):
    async def test_scope_mismatch_is_404(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        _store, _producer, cqs, run = await self._setup(runtime_context_admin)

        with pytest.raises(RuntimeApiError) as exc:
            await cqs.list_run_surfaces(
                org_id=self.ORG, user_id="someone_else", run_id=run.run_id
            )

        assert exc.value.http_status == 404

    async def test_unknown_run_is_404(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        _store, _producer, cqs, _run = await self._setup(runtime_context_admin)

        with pytest.raises(RuntimeApiError) as exc:
            await cqs.list_run_surfaces(
                org_id=self.ORG, user_id=self.USER, run_id="run_does_not_exist"
            )

        assert exc.value.http_status == 404

    async def test_empty_run_returns_no_surfaces(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        _store, _producer, cqs, run = await self._setup(runtime_context_admin)

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )

        assert response.run_id == run.run_id
        assert response.surfaces == ()

    async def test_fold_reflects_appended_ledger_events(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        _store, producer, cqs, run = await self._setup(runtime_context_admin)
        await self._append_surface_ledger(producer, run)

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )

        assert len(response.surfaces) == 1
        surface = response.surfaces[0]
        assert surface.surface_id == "record://linear/get_issue/issue-1"
        assert surface.kind == "record"
        assert surface.connector == "linear"
        assert surface.op == "get_issue"
        assert surface.title == "ENG-1 Fix"
        assert surface.payload_ref == "call:call_1"
        assert surface.view is not None
        assert surface.view.tier == "shaped"
        assert surface.view.basis == "registry"
        assert surface.ledger_id.startswith("r")

    async def test_ref_keyed_payload_is_offloaded(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Pins D5: ``_redaction_state_for`` marks any ``ref``-keyed payload
        # OFFLOADED — ``read.executed`` / ``surface.created`` carry payload_ref.
        store, producer, _cqs, run = await self._setup(runtime_context_admin)
        await self._append_surface_ledger(producer, run)

        events = list(
            await store.list_events_after(
                org_id=self.ORG, run_id=run.run_id, after_sequence=0
            )
        )
        by_type = {event.event_type: event for event in events}
        read = by_type[RuntimeApiEventType.READ_EXECUTED]
        created = by_type[RuntimeApiEventType.SURFACE_CREATED]
        assert read.redaction_state is RuntimeEventRedactionState.OFFLOADED
        assert created.redaction_state is RuntimeEventRedactionState.OFFLOADED
