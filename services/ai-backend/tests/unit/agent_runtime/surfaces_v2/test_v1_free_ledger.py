"""E3 cutover keystone (T4): the run ledger is v1-free, yet v2 canvas is complete.

Drives an executed MCP read through the per-tool pipeline with the run handler's
real :class:`WorkLedgerEmitter` bound (built with ``SURFACES_V2`` **unset** — the
E3 default-on posture, no env flag needed), then asserts:

* **No v1 residue** — no event payload emitted for the run carries a ``surface``
  or top-level ``surface_uri`` key (the retired v1 appendage is gone from the
  wire, not just from the result dict);
* **v2 is complete** — ``surface.created`` and ``view.derived`` events still
  appear, and :meth:`SurfaceStoreProjection.fold` yields a canvas surface with a
  non-empty ``title`` / ``kind`` / ``payload_ref`` — i.e. v2 needs *nothing* from
  the v1 pipeline.

This is the cutover proof the retirement rests on: surface data reaches the
client from ledger events alone, never from ``payload.surface``.

It is also the only place in this suite where events reach the fold the way they
reach a user — appended through ``append_api_event``, so through the transport
allow-list that persists and publishes them. That matters more than it sounds:
an allow-list which does not name a key DELETES it, silently, at every layer.
Calling the emitter and the fold directly is precisely the shape that stayed
green for a release while the wire between them was dropping the payload on the
floor, so tests about what a client receives belong here rather than there.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from langchain_core.tools import BaseTool

from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.connection import McpServerConnectionConfig
from agent_runtime.capabilities.mcp.per_tool_registration import (
    McpPerToolCollaborators,
    McpPerToolRegistrar,
)
from agent_runtime.capabilities.mcp.cards import McpAuthState
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
from agent_runtime.capabilities.mcp.gateway_context import (
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.operations.presentation import (
    SurfaceLedgerOperationOutcomePresenter,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.content import SurfaceContentProjection
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import EffectActor, LedgerEventType
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.mcp_operation_storage import RuntimeMcpOperationResultStore

from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin
from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers

_LINEAR_ISSUE_OUTPUT: dict[str, object] = {
    "issue": {
        "id": "issue-uuid-1",
        "identifier": "ENG-1421",
        "title": "Fix login redirect loop",
        "state": {"name": "In Progress"},
        "assignee": {"displayName": "Sarah Chen"},
        "priorityLabel": "High",
        "updatedAt": "2026-07-20T10:00:00Z",
        "url": "https://linear.app/acme/issue/ENG-1421",
    }
}

_SURFACE_KEYS = ("surface", "surface_uri")


@dataclass
class _ArgumentStore:
    rows: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    async def persist(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None:
        self.rows[ref] = (digest, canonical_bytes)

    async def resolve(self, *, ref: str, digest: str) -> bytes | None:
        stored = self.rows.get(ref)
        if stored is None or stored[0] != digest:
            return None
        return stored[1]


def _default_on_settings() -> RuntimeSettings:
    # SURFACES_V2 deliberately UNSET — proves E3's default-on flip (surfaces_v2
    # resolves True with no env flag) as well as the v1-free invariant.
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _runtime_context_for_run(run_id: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_123",
        roles={"employee"},
        permission_scopes={"docs:read", "docs:write"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5.4-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0.0,
        ),
        run_id=run_id,
        trace_id=f"trace_{run_id}",
    )


class _WorkerToolResult:
    """The `tool_result` event the worker emits for every tool call."""

    event_type = "tool_result"
    sequence_no = 10_000

    def __init__(self, *, payload: dict) -> None:
        self.payload = payload


class LedgerDrivenReadMixin(DynamicMcpLoadingMixin):
    """Drives one real MCP read through the real per-tool pipeline.

    Shared by both concrete classes below because the point of this file is that
    only the composed pipeline can see these defects — a stand-in for any part
    of it recreates the blind spot.
    """

    async def _call_tool(
        self,
        runtime_context: AgentRuntimeContext,
        *,
        server: str,
        tool: str,
        output: Mapping[str, object],
    ):
        """Build the per-tool MCP tool whose PRESENT stage reaches the ledger.

        The gateway used to be what put a read on the Work Ledger. Per-tool
        registration replaced it, and the PRESENT stage is where that job now
        lives -- so this drives the real composed pipeline rather than a stand-in.
        """

        card = self.make_card(name=server).model_copy(
            update={"auth_state": McpAuthState.AUTHENTICATED, "server_id": server}
        )

        class _Inner(BaseTool):
            name: str = tool
            description: str = "ported"
            response_format: str = "content_and_artifact"

            def _run(self, *a, **k):
                raise AssertionError("async only")

            async def _arun(self, *a, **k):
                return ("ok", dict(output))

        class _Dir:
            async def connection_for(self, server_id):
                return McpServerConnectionConfig(
                    url="https://mcp.invalid/mcp", transport=McpTransport.HTTP
                )

        class _Creds:
            async def auth_for(self, server_id):
                return {}

        class _Client:
            async def get_tools(self, *, server_name=None):
                return [_Inner()]

        class _Factory:
            def create(self, connections):
                return _Client()

        class _Provider:
            async def list_server_cards(self):
                return (card,)

        class _Registry:
            providers = (_Provider(),)

            def resolve_server(self, *a, **k):
                return card

        registration = await McpPerToolRegistrar.build(
            runtime_context=runtime_context,
            mcp_registry=_Registry(),
            collaborators=McpPerToolCollaborators(
                directory=_Dir(), credentials=_Creds(), client_factory=_Factory()
            ),
            gate=None,
            reserved_names=frozenset(),
        )
        assert registration is not None, "per-tool registration must produce the tool"
        # Registration namespaces the model surface, so the tool is registered
        # as ``mcp__linear__get_issue``, not as the bare name the connector
        # advertises. Matching on the bare name here found nothing and the
        # generator raised StopIteration.
        registered = McpToolName.compose(server=server, tool=tool)
        return next(t for t in registration.tools if t.name == registered)

    def _bind_canonical_gateway(
        self,
        *,
        handler: RuntimeRunHandler,
        store: InMemoryRuntimeApiStore,
        run,
    ) -> tuple[object, object]:
        operation_token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id=run.org_id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                workspace=None,
                user=None,
            ),
            ledger_emitter=handler._build_operation_ledger_emitter(run),
            artifact_service=None,
            outcome_presenter=SurfaceLedgerOperationOutcomePresenter(),
            mode=OperationGatewayMode.ENFORCE,
            canonical_arguments_durable=True,
        )
        descriptors = OperationDescriptorRegistry()
        classifier = OperationClassifier(descriptors=descriptors)
        owner_ref = f"principal://users/{run.user_id}"
        service_token = McpOperationGatewayContext.bind_for_run(
            McpOperationGatewayServices(
                gateway=OperationGateway(
                    descriptors=descriptors,
                    classifier=classifier,
                ),
                descriptors=descriptors,
                classifier=classifier,
                stager=EffectStager(
                    ledger=FakeLedger(),
                    outbox=FakeOutbox(),
                    clock=FakeClock(),
                    stage_ids=FakeStageIds(),
                ),
                stage_scope=EffectStageScope(
                    run_id=run.run_id,
                    owner_ref=owner_ref,
                ),
                stage_author=EffectActorIdentity(
                    actor=EffectActor.SYSTEM,
                    principal_ref="principal://system/mcp-operation-gateway",
                ),
                result_store=RuntimeMcpOperationResultStore(
                    event_producer=RuntimeEventProducer(
                        persistence=store,
                        event_store=store,
                    ),
                    run=run,
                ),
                argument_store=_ArgumentStore(),
                connector_overrides=ConnectorWritePolicyOverrides(),
            )
        )
        return operation_token, service_token

    async def _stored_events(self, *, tool: str = "get_issue"):
        """Run one real MCP read and return the events it persisted.

        The events come back off the STORE, so they have been through
        ``append_api_event`` and its transport allow-list — the layer that
        silently deleted the spec for a release.
        """

        store = InMemoryRuntimeApiStore()
        settings = _default_on_settings()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id="org_123", run_id=run_id)
        assert run is not None
        handler = RuntimeRunHandler(
            persistence=store, event_store=store, settings=settings
        )
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None

        bound_tool = await self._call_tool(
            _runtime_context_for_run(run_id),
            server="linear",
            tool=tool,
            output=_LINEAR_ISSUE_OUTPUT,
        )
        token = WorkLedgerEmitter.bind_for_run(emitter)
        operation_token, service_token = self._bind_canonical_gateway(
            handler=handler, store=store, run=run
        )
        try:
            await bound_tool.ainvoke(
                {"query": "ENG-1421", "tool_call_id": "call_linear_get_issue"}
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            WorkLedgerEmitter.unbind(token)

        return list(
            await store.list_events_after(
                org_id="org_123", run_id=run_id, after_sequence=0
            )
        )

    @staticmethod
    def _created(events) -> Mapping[str, object]:
        return next(
            event.payload
            for event in events
            if event.event_type is RuntimeApiEventType.SURFACE_CREATED
        )


class TestV1FreeLedger(LedgerDrivenReadMixin):
    async def test_executed_read_leaves_no_v1_surface_but_full_v2_canvas(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        settings = _default_on_settings()

        # Default-on proof: the master flag resolves True with SURFACES_V2 unset.
        assert settings.execution.surfaces_v2 is True

        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id="org_123", run_id=run_id)
        assert run is not None

        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
        )
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None, (
            "default-on: emitter must bind with SURFACES_V2 unset"
        )
        runtime_context = _runtime_context_for_run(run_id)

        tool = await self._call_tool(
            runtime_context,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        token = WorkLedgerEmitter.bind_for_run(emitter)
        operation_token, service_token = self._bind_canonical_gateway(
            handler=handler,
            store=store,
            run=run,
        )
        try:
            result = await tool.ainvoke(
                {**{"query": "ENG-1421"}, "tool_call_id": "call_linear_get_issue"}
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            WorkLedgerEmitter.unbind(token)

        # The tool result itself is v1-free.
        for key in _SURFACE_KEYS:
            assert key not in result

        events = list(
            await store.list_events_after(
                org_id="org_123", run_id=run_id, after_sequence=0
            )
        )

        # (1) No v1 residue anywhere on the run's event stream.
        for event in events:
            for key in _SURFACE_KEYS:
                assert key not in event.payload, (
                    f"v1 residue {key!r} on {event.event_type}"
                )

        # (2) v2 surface events still appear.
        types = [event.event_type for event in events]
        assert RuntimeApiEventType.ACTION_CLASSIFIED in types
        assert RuntimeApiEventType.READ_EXECUTED in types
        assert RuntimeApiEventType.SURFACE_CREATED in types
        assert RuntimeApiEventType.VIEW_DERIVED in types

        # The surface.created payload keys off surface_id + payload_ref, NOT a
        # v1 surface envelope.
        created = next(
            e.payload
            for e in events
            if e.event_type is RuntimeApiEventType.SURFACE_CREATED
        )
        assert created["surface_id"] == "record://linear/get_issue/issue-uuid-1"
        assert created["payload_ref"].startswith("call:")

        # (3) The fold yields a complete canvas surface from ledger events alone.
        state = SurfaceStoreProjection.fold(run_id, events)
        assert len(state.surfaces) == 1
        surface = state.surfaces[0]
        assert surface.surface_id == "record://linear/get_issue/issue-uuid-1"
        assert surface.kind
        assert surface.title
        assert surface.payload_ref.startswith("call:")
        assert surface.view is not None
        assert surface.view.tier == "shaped"

    async def test_one_served_name_reaches_the_client_for_a_read(
        self,
    ) -> None:
        # WHAT PRODUCTION ACTUALLY SERVES, measured through the real chain
        # rather than by calling the two provenance sites directly with names
        # of the test's choosing. The distinction matters and is the reason
        # this test lives here: an MCP name is slug-folded at the OUTERMOST
        # boundary (`McpToolCallRequest` and `McpToolDescriptor` both run every
        # server/tool name through `normalize_slug`), so `Get_Issue` is already
        # `get_issue` before any surface code runs. A test that hands the
        # emitter a mixed-case name proves something about the function and
        # nothing about the screen.
        #
        # So the claim under test is the one that is actually true end to end:
        # there is exactly ONE served name, the surface layer does not re-spell
        # it, and it agrees with the pair the v1 envelope carries.
        store = InMemoryRuntimeApiStore()
        settings = _default_on_settings()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id="org_123", run_id=run_id)
        assert run is not None
        handler = RuntimeRunHandler(
            persistence=store, event_store=store, settings=settings
        )
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None
        runtime_context = _runtime_context_for_run(run_id)

        # The connector publishes `get_issue`; the caller asks for `Get_Issue`.
        # The MCP boundary folds the request onto the published name, which is
        # exactly why nothing downstream ever sees the caller's spelling.
        tool = await self._call_tool(
            runtime_context,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )
        token = WorkLedgerEmitter.bind_for_run(emitter)
        operation_token, service_token = self._bind_canonical_gateway(
            handler=handler,
            store=store,
            run=run,
        )
        try:
            # Per-tool: the connector's own tool, addressed directly. The
            # `Get_Issue` spelling this test used to type went into the
            # envelope's `tool_name`; there is no envelope now, and the served
            # name is the registered one — which is the property asserted below.
            await tool.ainvoke({"query": "ENG-1421", "tool_call_id": "call_jira"})
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            WorkLedgerEmitter.unbind(token)

        events = list(
            await store.list_events_after(
                org_id="org_123", run_id=run_id, after_sequence=0
            )
        )
        created = next(
            e.payload
            for e in events
            if e.event_type is RuntimeApiEventType.SURFACE_CREATED
        )
        classified = next(
            e.payload
            for e in events
            if e.event_type is RuntimeApiEventType.ACTION_CLASSIFIED
        )

        # What reaches the screen. `state.source` is the pair the tier-3 note
        # reads out; it is the ledger's `surface.created.source` restated by the
        # v2 fold, and it is `get_issue` — the name as it survived the MCP
        # boundary, NOT re-lowercased a second time by the surface layer, and
        # not the `Get_Issue` the test typed (which never reached this code).
        # `fold` hydrates a surface from the run's `tool_result` STREAM event —
        # a generic tool event the WORKER emits for every tool call, not
        # something the surfaces emitter produces. This test drives the tool
        # directly, so it stands in for the worker; under the umbrella the event
        # arrived because the test bound the Operation Gateway, which the
        # per-tool path does not traverse.
        worker_tool_result = _WorkerToolResult(
            payload={"call_id": "call_jira", "output": _LINEAR_ISSUE_OUTPUT}
        )
        content = SurfaceContentProjection.fold(
            [*events, worker_tool_result],
            surface_payload_refs={created["surface_id"]: created["payload_ref"]},
        )
        served = content[created["surface_id"]]["source"]
        assert served == {"server": "linear", "tool": "get_issue"}

        # One name, not two. The emitter restates the projector's `state.source`
        # instead of deriving its own, so the ledger pair and the pair a v1
        # envelope would carry are the same value rather than two computations
        # that happen to agree.
        assert created["source"] == {
            "connector": served["server"],
            "op": served["tool"],
        }

        # And the identity register is untouched by any of that: the surface URI
        # is a stable name and the classification is what the curated read
        # catalog is keyed on.
        assert created["surface_id"] == "record://linear/get_issue/issue-uuid-1"
        assert classified["connector"] == "linear"
        assert classified["op"] == "get_issue"
        assert classified["class"] == "read"

    async def test_read_executed_event_is_present_for_the_call(
        self,
    ) -> None:
        # Sanity: the read path itself is recorded (so a canvas title/source can
        # resolve the payload_ref), independent of the surface events.
        store = InMemoryRuntimeApiStore()
        settings = _default_on_settings()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id="org_123", run_id=run_id)
        assert run is not None
        handler = RuntimeRunHandler(
            persistence=store, event_store=store, settings=settings
        )
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None
        runtime_context = _runtime_context_for_run(run_id)

        tool = await self._call_tool(
            runtime_context,
            server="linear",
            tool="Get_Issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )
        token = WorkLedgerEmitter.bind_for_run(emitter)
        operation_token, service_token = self._bind_canonical_gateway(
            handler=handler,
            store=store,
            run=run,
        )
        try:
            await tool.ainvoke(
                {**{"query": "ENG-1421"}, "tool_call_id": "call_linear_get_issue"}
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            WorkLedgerEmitter.unbind(token)

        events = list(
            await store.list_events_after(
                org_id="org_123", run_id=run_id, after_sequence=0
            )
        )
        read_values = {
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
        }
        seen = {e.event_type.value for e in events if e.event_type.value in read_values}
        assert seen == read_values


class TestSurfaceArrivesWithItsData(LedgerDrivenReadMixin):
    """A surface must reach the fold WITH the payload it was shaped against.

    The floor PRD's whole thesis, pinned end to end. The projector already built
    one coherent ``SurfaceEnvelope``; the pipeline used to take it apart and ship
    the pieces on separate events, so four independent hops had to work for one
    object to arrive — and each of them broke in production while ~9,000 unit
    tests passed:

    1. the transport allow-list silently stripped the spec;
    2. ``payload_ref`` is always ``call:unattributed`` on the live agent path,
       so the id-based join to the payload could never resolve;
    3. the persisted ``tool_result.output`` is the model-facing content half
       while the spec was inferred from the artifact half — one read, two
       representations, and ``items_path`` matching neither;
    4. the client's own id round-trip lost the surface on the way back.

    Every one of those is invisible to a test that calls the emitter and the
    fold directly, because each lives in the wire BETWEEN them. So this drives
    the real tool through the real pipeline and reads what the store actually
    holds.
    """

    async def test_the_transport_delivers_the_state_it_was_given(self) -> None:
        # Break 1, directly. The allow-list is the layer that deleted the spec,
        # and it deletes by OMISSION — no error, no warning, nothing to grep for.
        # These assertions read the persisted payload, so they fail if a future
        # widening of the emitter forgets to land here in the same pass.
        events = await self._stored_events()

        state = self._created(events)["state"]
        assert state["source"] == {"server": "linear", "tool": "get_issue"}
        assert state["data"] == _LINEAR_ISSUE_OUTPUT

    async def test_the_surface_reaches_the_fold_with_its_data(self) -> None:
        # Breaks 2 and 3 together. No ``tool_result`` is fabricated here and
        # none exists on this path, so there is nothing to join against: if the
        # data arrives, it arrived carried. The version of this test that stood
        # up a stand-in ``tool_result`` proved the join worked on inputs
        # production never produces.
        events = await self._stored_events()
        created = self._created(events)

        content = SurfaceContentProjection.fold(
            events,
            surface_payload_refs={created["surface_id"]: created["payload_ref"]},
        )

        assert content[created["surface_id"]]["data"] == _LINEAR_ISSUE_OUTPUT

    async def test_the_data_is_the_register_the_spec_was_resolved_against(
        self,
    ) -> None:
        # Break 3 specifically. The delivered payload must be the STRUCTURED
        # artifact half — the one a spec's ``items_path`` is written against —
        # not the model-facing content envelope the run persists, whose keys are
        # ``['content']`` and whose body is JSON encoded twice. A spec bound to
        # that shape resolves nothing and renders its columns over zero rows.
        events = await self._stored_events()
        created = self._created(events)

        data = self._created(events)["state"]["data"]
        assert isinstance(data, Mapping)
        assert set(data) == set(_LINEAR_ISSUE_OUTPUT)
        assert "content" not in data
        # And the identity register is untouched by carrying the body.
        assert created["surface_id"] == "record://linear/get_issue/issue-uuid-1"

    async def test_payload_ref_survives_as_provenance(self) -> None:
        # The reference stops being the only way to reach the payload; it does
        # not stop existing. The receipt and audit folds read it, and a historic
        # run that carries no state still resolves its body through it.
        events = await self._stored_events()

        assert self._created(events)["payload_ref"].startswith("call:")
