"""``ConversationQueryService.list_run_surfaces`` + the surfaces endpoint (D7).

Real ``InMemoryRuntimeApiStore`` + ``RuntimeEventProducer``: a scope mismatch is
a 404; an empty run yields an empty surface list; appended ledger events fold
into the projected surfaces; and a ``"ref"``-keyed payload is marked OFFLOADED
(pinning D5). Ledger events are appended through the real producer so the
projector allow-lists run exactly as they do in production.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.surfaces.projector import SurfaceProjector
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    StreamEventSource,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
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

    @staticmethod
    async def _append_surface_content(
        producer: RuntimeEventProducer, run: RunRecord
    ) -> None:
        """Append the persisted result and derived spec the projection consumes."""
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={
                "call_id": "call_1",
                "output": {"id": "ENG-1", "title": "Fix"},
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_SPEC_GENERATED,
            payload={
                "surface_uri": "record://linear/get_issue/issue-1",
                "spec": {"archetype": "record"},
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

    async def test_content_hydration_serves_materialized_state(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # PRD-B2: the metadata snapshot is enriched with the v1 surface
        # envelope's {spec, source, data}, resolved from the same run's events.
        _store, producer, cqs, run = await self._setup(runtime_context_admin)
        await self._append_surface_ledger(producer, run)
        await self._append_surface_content(producer, run)

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )

        assert len(response.surfaces) == 1
        surface = response.surfaces[0]
        assert surface.state == {
            "spec": {"archetype": "record"},
            "data": {"id": "ENG-1", "title": "Fix"},
            # The read's own ``surface.created`` provenance, restated in the
            # renderer's vocabulary. The client reads ONLY ``state`` as renderer
            # input, so the sibling ``connector`` / ``op`` metadata below cannot
            # reach the view.
            "source": {"server": "linear", "tool": "get_issue"},
        }
        assert surface.connector == "linear"
        assert surface.op == "get_issue"

    async def test_surface_without_content_event_has_null_state(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # No tool_result envelope yet ⇒ state is None (honest "not hydrated").
        _store, producer, cqs, run = await self._setup(runtime_context_admin)
        await self._append_surface_ledger(producer, run)

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )

        assert response.surfaces[0].state is None

    async def test_state_omits_source_when_the_read_never_named_one(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Every surface written before ``state.source`` existed replays through
        # here. Absent provenance must hydrate exactly as it always did — the
        # client's "unknown tool" sentence, never a 500 and never a half-source.
        _store, producer, cqs, run = await self._setup(runtime_context_admin)
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": "record://legacy/read/1",
                "kind": "record",
                "title": "Legacy",
                "payload_ref": "call:call_1",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={"call_id": "call_1", "output": {"id": "ENG-1"}},
        )

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )

        assert response.surfaces[0].state == {"data": {"id": "ENG-1"}}

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


class TestSpecLessSurfaceWire(RunSurfacesEndpointMixin):
    """The inference floor, end to end on the real wire.

    This class used to assert the opposite: that an unmatched tool reached the
    client *without* a spec, so the renderer could name the tool no spec matched.
    The generative-UI floor PRD removes that state. A mapping-shaped read with no
    builtin and no store now resolves a spec on rung 0 — deterministically, with
    no model — so "spec-less" is unreachable here, and the assertions below are
    inverted deliberately rather than relaxed. A non-mapping output still yields
    no surface at all, which is a different case and not this one.

    Nothing here is hand-written ledger payloads. The real :class:`SurfaceProjector`
    resolves the tool output, the real :class:`WorkLedgerEmitter` writes the
    ledger through the real event producer (the same closure
    ``RunHandler._build_work_ledger_emitter`` binds), and the endpoint folds it
    back. That matters more now, not less: the spec is carried on
    ``surface.created``, and that event is rebuilt from a strict allow-list in
    ``RuntimeEventPresentationProjector`` before it is persisted. A test that
    drove the emitter and the fold directly — and several do — stays green while
    the allow-list silently strips the spec and the client renders nothing. Only
    a test that goes through ``append_api_event`` can see that.

    ``INFERRED_FIELD_LABELS`` is the observable contract of the inference: the
    labels a reader sees. It is asserted rather than the whole spec so that
    tuning a ranking weight does not red this test, while dropping the floor
    entirely does.
    """

    SERVER = "pagerduty"
    TOOL = "pagerduty.incident.read"
    CALL_ID = "call_4127"
    OUTPUT: dict[str, object] = {
        "incident_number": "4127",
        "title": "Elevated 5xx on checkout",
        "status": "acknowledged",
        "service": {"id": "svc-9", "name": "checkout-api"},
    }
    SOURCE_REF: dict[str, object] = {"server": SERVER, "tool": TOOL}
    # What rung 0 makes of the payload above.
    #
    # `title` is absent from this set on purpose: the floor promotes it to
    # `title_path`, so it heads the card instead of being repeated as a row —
    # which is why the set is asserted alongside INFERRED_TITLE_PATH rather
    # than on its own.
    #
    # `status` is low-cardinality so it draws as a badge, and `service` is a
    # mapping carrying `name`, so the floor binds the nested path rather than
    # dumping the object into a cell.
    INFERRED_FIELD_LABELS: set[str] = {"Status", "Incident Number", "Service"}
    INFERRED_TITLE_PATH = "title"

    async def _emit_real_read(
        self, producer: RuntimeEventProducer, run: RunRecord
    ) -> None:
        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            await producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType(str(event_type_value)),
                summary=summary,
                payload=dict(payload),
            )

        envelope = SurfaceProjector().resolve(
            self.SERVER, self.TOOL, self.OUTPUT, call_id=self.CALL_ID
        )
        assert envelope is not None
        # No builtin and no store ⇒ the ladder falls to rung 0, which always
        # answers. This assertion is the floor's precondition: if it ever goes
        # back to None, the two wire tests below are testing nothing.
        assert envelope.state.spec is not None
        await WorkLedgerEmitter(emit=_emit).on_tool_result(
            server_name=self.SERVER,
            tool_name=self.TOOL,
            call_id=self.CALL_ID,
            output=self.OUTPUT,
            surface=envelope.model_dump(mode="json", exclude_none=True),
            surface_uri=envelope.surface_uri,
            latency_ms=12,
        )
        # The persisted result the surface's ``payload_ref`` points at.
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={"call_id": self.CALL_ID, "output": self.OUTPUT},
        )

    async def test_the_inferred_spec_survives_the_transport_allow_list(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The regression this class exists for. `surface.created` is rebuilt
        # field-by-field by the presentation projector before it is persisted,
        # so a spec the emitter writes does not imply a spec the client reads.
        # Everything below the endpoint is the production path.
        _store, producer, cqs, run = await self._setup(runtime_context_admin)
        await self._emit_real_read(producer, run)

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )

        assert len(response.surfaces) == 1
        surface = response.surfaces[0]
        assert surface.state is not None
        # Data and provenance are unchanged by the floor.
        assert surface.state["data"] == self.OUTPUT
        assert surface.state["source"] == self.SOURCE_REF
        # …and the spec now arrives, which is the whole change.
        spec = surface.state.get("spec")
        assert isinstance(spec, dict), "the inferred spec was stripped on the wire"
        assert spec["archetype"] == "record"
        assert spec["title_path"] == self.INFERRED_TITLE_PATH
        assert {field["label"] for field in spec["fields"]} == (
            self.INFERRED_FIELD_LABELS
        )
        # The nested bind is the part a flat key-dump would get wrong.
        by_label = {field["label"]: field for field in spec["fields"]}
        assert by_label["Service"]["path"] == "service.name"
        assert by_label["Status"]["format"] == "badge"

    async def test_the_ledger_tier_agrees_with_what_renders(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # The defect that started this work was a ledger recording
        # `tier: shaped, basis: registry` over a screen showing raw JSON. A
        # compliance record that disagrees with the render is worse than none,
        # so the two are asserted together, on one wire, in one test.
        _store, producer, cqs, run = await self._setup(runtime_context_admin)
        await self._emit_real_read(producer, run)

        response = await cqs.list_run_surfaces(
            org_id=self.ORG, user_id=self.USER, run_id=run.run_id
        )
        surface = response.surfaces[0]

        assert surface.view is not None
        # An inferred spec IS a spec, so the tier is `shaped`; its basis is the
        # payload's own structure. No new ledger vocabulary was minted for this.
        assert surface.view.tier == "shaped"
        assert surface.view.basis == "schema"
        assert surface.state is not None and surface.state.get("spec") is not None

    async def test_the_v1_envelope_carries_the_same_state(self) -> None:
        # The envelope path (legacy hosts fold ``payload.surface.state``) must
        # agree with the hydrated path above, or the same surface would render
        # shaped on one wire and unshaped on the other.
        envelope = SurfaceProjector().resolve(
            self.SERVER, self.TOOL, self.OUTPUT, call_id=self.CALL_ID
        )
        assert envelope is not None
        dumped = envelope.model_dump(mode="json", exclude_none=True)

        assert dumped["state"]["data"] == self.OUTPUT
        assert dumped["state"]["source"] == self.SOURCE_REF
        assert {
            field["label"] for field in dumped["state"]["spec"]["fields"]
        } == self.INFERRED_FIELD_LABELS
