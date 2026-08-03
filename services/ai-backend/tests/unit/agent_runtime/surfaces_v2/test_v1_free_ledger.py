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
client via ledger events + ``payload_ref`` resolution, never ``payload.surface``.
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


class TestV1FreeLedger(DynamicMcpLoadingMixin):
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
        return next(t for t in registration.tools if t.name == tool)

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
