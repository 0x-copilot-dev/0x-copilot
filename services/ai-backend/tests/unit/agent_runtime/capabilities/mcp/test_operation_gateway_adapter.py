"""D1 MCP convergence tests: classify before client creation, then read or stage.

These tests deliberately bind the operation context/services directly.  The
worker composition root is reserved for a separate stream; that does not
weaken this contract because an unbound service context uses the legacy path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.mcp import CallMcpTool, DynamicMcpRegistry, McpLoader
from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.mcp.effect_material import McpEffectMaterial
from agent_runtime.capabilities.mcp.cards import McpAuthState, McpLoadError
from agent_runtime.capabilities.mcp.client import (
    McpAmbiguousDispatchError,
    McpAuthError,
    McpLeaseError,
)
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSession
from agent_runtime.capabilities.mcp.gateway_context import (
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.capabilities.mcp.execution_services import McpOperationStoredResult
from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationArgumentMaterialResolver,
    McpOperationAdapter,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.operations.errors import OperationGatewayError
from agent_runtime.capabilities.operations.presentation import (
    SurfaceLedgerOperationOutcomePresenter,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.ledger_models import EffectActor, LedgerEventType
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_SERVER = "linear"


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_d1",
        org_id="org_d1",
        roles={"employee"},
        permission_scopes={"docs:read", "docs:write"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-4o-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0.0,
        ),
        run_id="run_d1",
        trace_id="trace_d1",
    )


@dataclass
class _RecordedOperationEvents:
    rows: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del summary
        self.rows.append((event_type.value, dict(payload)))


@dataclass
class _ResultStore:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def store_read_result(self, *, request, output: Mapping[str, object]):  # type: ignore[no-untyped-def]
        body = {str(key): value for key, value in output.items()}
        self.calls.append((request.operation_id, body))
        return McpOperationStoredResult(
            result_ref=f"operation://{request.operation_id}/result",
            model_output={"items": body.get("items", [])},
        )


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


@dataclass
class _RecordingOutcomePresenter:
    outcomes: list[object] = field(default_factory=list)

    async def present(self, outcome: object) -> None:
        self.outcomes.append(outcome)


class _AuthSessions:
    async def create_auth_session(self, *, server_id: str, runtime_context):  # type: ignore[no-untyped-def]
        del runtime_context
        return McpAuthSession(
            server_id=server_id,
            server_name=_SERVER,
            display_name="Linear",
            auth_url="https://vendor.example/oauth",
            expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


@dataclass
class _RejectedInterrupt:
    calls: int = 0

    def __call__(self, payload: dict[str, object]) -> object:
        del payload
        self.calls += 1
        return {"decision": "rejected"}


class _ExpiredClient:
    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, object]
    ) -> object:
        del tool_name, arguments
        raise McpAuthError("credentials expired")


class _Fixture(DynamicMcpLoadingMixin):
    def make_call_tool(
        self,
        *,
        output: Mapping[str, object] | None = None,
        gate: ToolAccessGate | None = None,
        auth_state: McpAuthState = McpAuthState.AUTHENTICATED,
    ) -> tuple[CallMcpTool, object]:
        client = self.FakeMcpClient(
            tools=(
                self.make_tool(name="list_issues"),
                self.make_tool(name="update_issue"),
                self.make_tool(name="delete_issue"),
                self.make_tool(name="unlisted_operation"),
            ),
            resources=(),
            tool_outputs={"list_issues": output or {"items": [{"id": "L-1"}]}},
        )
        card = self.make_card(name=_SERVER).model_copy(
            update={"auth_state": auth_state, "server_id": "srv_linear"}
        )
        provider = self.FakeMcpProvider(
            cards=(card,),
            clients={_SERVER: client},
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        return (
            CallMcpTool(
                registry=registry,
                loader=McpLoader(registry),
                runtime_context=_runtime_context(),
                gate=gate,
            ),
            provider,
        )

    def bind_enforced(
        self,
        *,
        mode: OperationGatewayMode = OperationGatewayMode.ENFORCE,
        outcome_presenter: object | None = None,
    ):  # type: ignore[no-untyped-def]
        context = _runtime_context()
        events = _RecordedOperationEvents()
        operation_token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id="conv_d1",
                run_id=context.run_id,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                workspace=None,
                user=None,
            ),
            ledger_emitter=events,
            artifact_service=None,
            outcome_presenter=(
                outcome_presenter
                if outcome_presenter is not None
                else SurfaceLedgerOperationOutcomePresenter()
            ),
            mode=mode,
            canonical_arguments_durable=True,
        )
        descriptors = OperationDescriptorRegistry()
        classifier = OperationClassifier(descriptors=descriptors)
        ledger = FakeLedger()
        result_store = _ResultStore()
        argument_store = _ArgumentStore()
        service_token = McpOperationGatewayContext.bind_for_run(
            McpOperationGatewayServices(
                gateway=OperationGateway(
                    descriptors=descriptors,
                    classifier=classifier,
                ),
                descriptors=descriptors,
                classifier=classifier,
                stager=EffectStager(
                    ledger=ledger,
                    outbox=FakeOutbox(),
                    clock=FakeClock(),
                    stage_ids=FakeStageIds(),
                ),
                stage_scope=EffectStageScope(
                    run_id=context.run_id,
                    owner_ref="principal://users/user_d1",
                ),
                stage_author=EffectActorIdentity(
                    actor=EffectActor.USER,
                    principal_ref="principal://users/user_d1",
                ),
                result_store=result_store,
                argument_store=argument_store,
                connector_overrides=ConnectorWritePolicyOverrides(),
            )
        )
        return context, events, ledger, result_store, operation_token, service_token


def _invoke(
    tool: CallMcpTool, name: str, arguments: dict[str, object]
) -> dict[str, object]:
    return asyncio.run(
        tool.ainvoke(
            {
                "server_name": _SERVER,
                "tool_name": name,
                "arguments": arguments,
                "tool_call_id": "call_d1",
            }
        )
    )


def test_catalog_read_executes_once_persists_result_and_projects_v2_ledger() -> None:
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    _context, events, ledger, result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    presentation_rows: list[str] = []

    async def legacy_emit(
        event_type: str, payload: Mapping[str, object], summary: str | None
    ) -> None:
        del payload, summary
        presentation_rows.append(event_type)

    legacy_token = WorkLedgerEmitter.bind_for_run(WorkLedgerEmitter(emit=legacy_emit))
    try:
        result = _invoke(tool, "list_issues", {"team": "ENG"})
    finally:
        WorkLedgerEmitter.unbind(legacy_token)
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == [_SERVER]
    assert result["output"] == {
        "status": "completed",
        "operation_id": result["output"]["operation_id"],
        "summary": "Fetched list_issues from linear.",
        "result_ref": result["output"]["result_ref"],
        "result": {"items": [{"id": "L-1"}]},
    }
    assert result_store.calls and result_store.calls[0][1] == {"items": [{"id": "L-1"}]}
    assert ledger.events_by_stage == {}
    assert [event_type for event_type, _payload in events.rows] == [
        LedgerEventType.OPERATION_REQUESTED.value,
        LedgerEventType.OPERATION_CLASSIFIED.value,
        LedgerEventType.OPERATION_COMPLETED.value,
    ]
    assert LedgerEventType.READ_EXECUTED.value not in {
        event_type for event_type, _payload in events.rows
    }
    # The canonical adapter, not a direct MCP compatibility branch, projects
    # the persisted read into the v2 canvas ledger after gateway completion.
    assert presentation_rows == [
        LedgerEventType.ACTION_CLASSIFIED.value,
        LedgerEventType.READ_EXECUTED.value,
        LedgerEventType.SURFACE_CREATED.value,
        LedgerEventType.VIEW_DERIVED.value,
    ]


def test_gateway_presents_the_neutral_outcome_after_mcp_result_persistence() -> None:
    """MCP supplies result facts; the generic gateway owns presentation."""

    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    presenter = _RecordingOutcomePresenter()
    _context, _events, _ledger, result_store, operation_token, service_token = (
        fixture.bind_enforced(outcome_presenter=presenter)
    )
    try:
        result = _invoke(tool, "list_issues", {"team": "ENG"})
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == [_SERVER]
    assert result_store.calls
    assert len(presenter.outcomes) == 1
    outcome = presenter.outcomes[0]
    assert outcome.operation_id == result["output"]["operation_id"]
    assert outcome.capability == _SERVER
    assert outcome.op == "list_issues"
    assert outcome.output == {"items": [{"id": "L-1"}]}


@pytest.mark.parametrize(
    ("code", "acquisition_safe", "expected_retryable"),
    (
        ("ambiguous", False, False),
        ("ambiguous_transport_failure", False, False),
        ("lease_invalid", False, False),
        ("lease_wrong_owner", False, False),
        ("lease_stale_pre_dispatch", False, False),
        ("pool_saturated", True, True),
        ("server_unavailable", True, True),
    ),
)
def test_lease_failure_retryability_never_authorizes_effect_replay(
    code: str,
    acquisition_safe: bool,
    expected_retryable: bool,
) -> None:
    fixture = _Fixture()
    client = fixture.FakeMcpClient(tools=(), resources=())
    dispatches = 0

    async def call_tool(*, tool_name: str, arguments: Mapping[str, object]) -> object:
        nonlocal dispatches
        del tool_name, arguments
        dispatches += 1
        if code == "ambiguous":
            raise McpAmbiguousDispatchError("proxy outcome is unknown")
        raise McpLeaseError(code, acquisition_safe=acquisition_safe)

    client.call_tool = call_tool  # type: ignore[method-assign]
    card = fixture.make_card(name=_SERVER).model_copy(
        update={"auth_state": McpAuthState.AUTHENTICATED, "server_id": "srv_linear"}
    )
    provider = fixture.FakeMcpProvider(cards=(card,), clients={_SERVER: client})
    registry = DynamicMcpRegistry(providers=(provider,))

    async def dispatch() -> OperationGatewayError:
        resolution = await registry.resolve_server(_SERVER)
        assert not isinstance(resolution, McpLoadError)
        adapter = McpOperationAdapter(
            registry=registry,
            runtime_context=_runtime_context(),
            timeout_seconds=1,
            server_name=_SERVER,
            tool_name="list_issues",
            arguments={},
            gate=None,
            execution=None,  # type: ignore[arg-type]
            tool_call_id="call_lease",
        )
        with pytest.raises(OperationGatewayError) as exc_info:
            await adapter._dispatch(resolution)
        return exc_info.value

    error = asyncio.run(dispatch())

    assert error.retryable is expected_retryable
    assert dispatches == 1


def test_unbound_gateway_holds_before_client_construction() -> None:
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()

    result = _invoke(tool, "list_issues", {"team": "ENG"})

    assert provider.created_clients == []
    assert result["output"] == {
        "status": "held",
        "summary": (
            "The connector operation is held until the canonical operation gateway "
            "is available; no connector call was made."
        ),
    }


def test_catalog_write_stages_exact_arguments_without_creating_an_mcp_client() -> None:
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    _context, events, ledger, _result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    arguments = {"issue_id": "L-1", "title": "Approved title", "labels": ["p1"]}
    annotation_token = McpToolAnnotationsRegistry.bind_for_run({})
    McpToolAnnotationsRegistry.register(
        _SERVER,
        "update_issue",
        McpToolAnnotations(read_only_hint=True),
    )
    try:
        result = _invoke(tool, "update_issue", arguments)
    finally:
        McpToolAnnotationsRegistry.unbind(annotation_token)
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == []
    assert result["output"]["status"] == "staged"
    # A contradictory provider `readOnlyHint` may never loosen catalog write.
    assert provider.created_clients == []
    assert result["output"]["summary"] == (
        "Proposed update_issue on linear; no external change has been made."
    )
    staged = next(iter(ledger.events_by_stage.values()))[0].payload
    assert staged["capability"] == _SERVER
    assert staged["op"] == "update_issue"
    assert staged["proposal_content_ref"].startswith("operation://op_")
    assert staged["proposal_digest"]
    assert arguments["title"] not in str(staged)
    assert [event_type for event_type, _payload in events.rows] == [
        LedgerEventType.OPERATION_REQUESTED.value,
        LedgerEventType.OPERATION_CLASSIFIED.value,
        LedgerEventType.OPERATION_COMPLETED.value,
    ]


def test_unknown_and_destructive_mcp_operations_stage_fail_closed_without_dispatch() -> (
    None
):
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    _context, _events, ledger, _result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    try:
        unknown = _invoke(tool, "unlisted_operation", {"id": "L-1"})
        destructive = _invoke(tool, "delete_issue", {"id": "L-2"})
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == []
    assert unknown["output"]["status"] == "staged"
    assert destructive["output"]["status"] == "staged"
    staged = [events[0].payload for events in ledger.events_by_stage.values()]
    assert {row["effect_class"] for row in staged} == {
        "unknown",
        "external_destructive",
    }


def test_gateway_mode_off_cannot_restore_direct_write_or_unknown_dispatch() -> None:
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    _context, _events, ledger, _result_store, operation_token, service_token = (
        fixture.bind_enforced(mode=OperationGatewayMode.OFF)
    )
    try:
        write = _invoke(tool, "update_issue", {"issue_id": "L-1"})
        unknown = _invoke(tool, "unlisted_operation", {"issue_id": "L-2"})
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == []
    assert write["output"]["status"] == "staged"
    assert unknown["output"]["status"] == "staged"
    assert len(ledger.events_by_stage) == 2


def test_missing_auth_parks_the_classified_read_before_client_creation() -> None:
    fixture = _Fixture()
    interrupt = _RejectedInterrupt()
    runtime_context = _runtime_context()
    gate = ToolAccessGate(
        auth_session_creator=_AuthSessions(),
        runtime_context=runtime_context,
        interrupt_handler=interrupt,
        classifier=None,
    )
    tool, provider = fixture.make_call_tool(
        gate=gate,
        auth_state=McpAuthState.UNAUTHENTICATED,
    )
    _context, _events, _ledger, _result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    try:
        result = _invoke(tool, "list_issues", {})
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert interrupt.calls == 1
    assert provider.created_clients == []
    assert result["output"]["status"] == "failed"


def test_auth_expiry_reparks_the_canonical_read_without_replay() -> None:
    fixture = _Fixture()
    interrupt = _RejectedInterrupt()
    runtime_context = _runtime_context()
    gate = ToolAccessGate(
        auth_session_creator=_AuthSessions(),
        runtime_context=runtime_context,
        interrupt_handler=interrupt,
        classifier=None,
    )
    card = fixture.make_card(name=_SERVER).model_copy(
        update={"auth_state": McpAuthState.AUTHENTICATED, "server_id": "srv_linear"}
    )
    provider = fixture.FakeMcpProvider(
        cards=(card,), clients={_SERVER: _ExpiredClient()}
    )
    tool = CallMcpTool(
        registry=DynamicMcpRegistry(providers=(provider,)),
        loader=McpLoader(DynamicMcpRegistry(providers=(provider,))),
        runtime_context=runtime_context,
        gate=gate,
    )
    _context, _events, _ledger, _result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    try:
        result = _invoke(tool, "list_issues", {})
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert interrupt.calls == 1
    assert provider.created_clients == [_SERVER]
    assert result["output"]["status"] == "failed"


def test_explicit_flag_off_keeps_reads_on_the_canonical_gateway(
    monkeypatch,
) -> None:
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    _context, events, ledger, result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    monkeypatch.setenv("SURFACES_V2", "false")
    try:
        result = _invoke(tool, "list_issues", {"team": "ENG"})
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == [_SERVER]
    assert result["output"]["status"] == "completed"
    assert result["output"]["result"] == {"items": [{"id": "L-1"}]}
    assert [event_type for event_type, _payload in events.rows] == [
        LedgerEventType.OPERATION_REQUESTED.value,
        LedgerEventType.OPERATION_CLASSIFIED.value,
        LedgerEventType.OPERATION_COMPLETED.value,
    ]
    assert ledger.events_by_stage == {}
    assert result_store.calls == [
        (result["output"]["operation_id"], {"items": [{"id": "L-1"}]})
    ]


def test_flag_off_gateway_cannot_dispatch_model_originated_write_or_unknown_operation(
    monkeypatch,
) -> None:
    """D9 canary: rollback holds model MCP work without a direct escape hatch."""

    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    monkeypatch.setenv("SURFACES_V2", "false")

    write = _invoke(tool, "update_issue", {"id": "L-1"})
    unknown = _invoke(tool, "unlisted_operation", {"id": "L-2"})

    assert provider.created_clients == []
    assert write["output"]["status"] == "held"
    assert unknown["output"]["status"] == "held"


@pytest.mark.parametrize(
    "annotations",
    (
        McpToolAnnotations(destructive_hint=True),
        McpToolAnnotations(read_only_hint=False),
        McpToolAnnotations(read_only_hint=True, destructive_hint=True),
    ),
)
def test_unbound_gateway_holds_catalog_read_regardless_of_tightening_annotations(
    monkeypatch,
    annotations: McpToolAnnotations,
) -> None:
    """D9 canary: annotations cannot revive a direct read compatibility path."""

    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    monkeypatch.setenv("SURFACES_V2", "false")
    registry_token = McpToolAnnotationsRegistry.bind_for_run({})
    McpToolAnnotationsRegistry.register(_SERVER, "list_issues", annotations)
    try:
        result = _invoke(tool, "list_issues", {"team": "ENG"})
    finally:
        McpToolAnnotationsRegistry.unbind(registry_token)

    assert provider.created_clients == []
    assert result["output"]["status"] == "held"


def test_unbound_gateway_holds_even_a_read_only_annotation(monkeypatch) -> None:
    fixture = _Fixture()
    tool, provider = fixture.make_call_tool()
    monkeypatch.setenv("SURFACES_V2", "false")
    registry_token = McpToolAnnotationsRegistry.bind_for_run({})
    McpToolAnnotationsRegistry.register(
        _SERVER,
        "list_issues",
        McpToolAnnotations(read_only_hint=True),
    )
    try:
        result = _invoke(tool, "list_issues", {"team": "ENG"})
    finally:
        McpToolAnnotationsRegistry.unbind(registry_token)

    assert provider.created_clients == []
    assert result["output"]["status"] == "held"


def test_material_resolver_returns_only_the_exact_canonical_stage_arguments() -> None:
    fixture = _Fixture()
    _context, _events, _ledger, _result_store, operation_token, service_token = (
        fixture.bind_enforced()
    )
    arguments = {"issue_id": "L-1", "metadata": {"priority": "high"}}
    try:
        request = OperationRequestFactory.create(
            capability=_SERVER,
            op="update_issue",
            arguments=arguments,
        )
        argument_store = _ArgumentStore()
        stored = OperationContext.require().arguments.get(request.canonical_args_ref)
        assert stored is not None
        asyncio.run(
            argument_store.persist(
                ref=request.canonical_args_ref,
                digest=stored[0],
                canonical_bytes=stored[1],
            )
        )
        material = asyncio.run(
            McpOperationArgumentMaterialResolver(arguments=argument_store).resolve(
                type(
                    "Request",
                    (),
                    {
                        "target_ref": "mcp-target://linear/update_issue",
                        "target_digest": "a" * 64,
                        "proposal_ref": "proposal://stg_00000000-0000-4000-8000-000000000001/revisions/1",
                        "proposal_content_ref": request.canonical_args_ref,
                        "proposal_digest": request.args_digest,
                    },
                )()
            )
        )
    finally:
        McpOperationGatewayContext.unbind(service_token)
        OperationContext.unbind(operation_token)

    assert isinstance(material, McpEffectMaterial)
    assert material.arguments == arguments
    assert material.proposal_content_ref == request.canonical_args_ref
    assert material.proposal_digest == request.args_digest
