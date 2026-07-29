"""F3.5 — the capability invoke path re-enters the ordinary Operation Gateway.

These tests deliberately compose the *real* gateway, the *real* ``CallMcpTool``
dispatcher, a *real* built catalog, and the *real* Step RB revalidator. A fake
executor would prove nothing about the property under test: that an inner
operation is not merely equivalent to a directly registered MCP tool call, but
is literally the same one.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    CapabilityBridgeRecursionError,
    CapabilityCatalog,
    CapabilityCatalogAccess,
    CapabilityCatalogRevisionAuthority,
    CapabilityCatalogScope,
    CapabilityDiscoveryErrorCode,
    CapabilityIndexEntry,
    CapabilityInvocationStatus,
    CapabilityInvocationTarget,
    CapabilityInvokeTool,
    CapabilityRefRevalidation,
    CapabilityRefRevisionBinding,
    CapabilitySource,
)
from agent_runtime.capabilities.discovery.executor import (
    CapabilityArgumentSchemaCheck,
    CapabilityDispatchBinding,
    CapabilityDispatchBindingPort,
    GatewayCapabilityExecutor,
    RunScopedCapabilityDispatchBindings,
)
from agent_runtime.capabilities.discovery.tool_bridge import CapabilityExecutionRefused
from agent_runtime.capabilities.mcp import CallMcpTool, DynamicMcpRegistry, McpLoader
from agent_runtime.capabilities.mcp.execution_services import McpOperationStoredResult
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
from agent_runtime.capabilities.tool_budget_guard import (
    ToolBudgetGuard,
    ToolBudgetGuardedTool,
)
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.control_plane.revision_binding import RevisionBindingRevalidator
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.persistence.records import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from agent_runtime.surfaces_v2.ledger_models import EffectActor, LedgerEventType
from runtime_worker.tool_call_ledger import ToolCallLedger

from tests.unit.agent_runtime.capabilities.discovery.test_revision_authority import (
    InMemoryCatalogGenerationSource,
)
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
_REFERENCE_KEY = b"f35-executor-reference-key-32-bytes!!!!!"
_SELECTION_REF = f"task-policy-selection://run_f35/research/sha256/{'d' * 64}"
_SERVER = "linear"
_READ_TOOL = "list_issues"
_WRITE_TOOL = "update_issue"

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["team"],
    "additionalProperties": False,
}
_CHANGED_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team_id": {"type": "string"}},
    "required": ["team_id"],
    "additionalProperties": False,
}
_WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"issue_id": {"type": "string"}, "title": {"type": "string"}},
    "required": ["issue_id"],
}


@dataclass
class _RecordedOperationEvents:
    """Capture the canonical operation ledger rows the gateway itself emits."""

    rows: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del summary
        self.rows.append((event_type.value, dict(payload)))

    def types(self) -> list[str]:
        return [event_type for event_type, _payload in self.rows]

    def operation_ids(self) -> list[str]:
        return [
            str(payload.get("operation_id"))
            for _event_type, payload in self.rows
            if payload.get("operation_id") is not None
        ]


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


class _EmptyBindings:
    """A binding port that never resolves — the fail-closed control."""

    def binding_for(self, capability_ref: str) -> CapabilityDispatchBinding | None:
        del capability_ref
        return None


class ExecutorHarness(DynamicMcpLoadingMixin):
    """Compose the whole real invoke path: bridge → executor → gateway → MCP."""

    def context(self, *, run_id: str = "run_f35") -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_f35",
            org_id="org_f35",
            roles={"employee"},
            permission_scopes={"docs:read", "docs:write"},
            connector_scopes={"drive": frozenset({"docs:read"})},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-test",
                max_input_tokens=32_000,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=run_id,
            trace_id="trace_f35",
        )

    def catalog(
        self,
        context: AgentRuntimeContext,
        *,
        expires_at: datetime | None = None,
    ) -> CapabilityCatalog:
        """Build a real catalog, then admit the two expanded MCP capabilities.

        The tier-one builder projects *server* cards; F3.3 projects the server's
        individual tools. Rebuilding the catalog with those entries mirrors what
        expansion hands the run without importing another lane's private seams.
        """

        base = AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=CapabilityCatalogScope.from_context(
                context,
                profile_id="research",
                policy_revision="policy_f35",
                connector_scope_revision="scope_f35",
            ),
            task_policy_selection_ref=_SELECTION_REF,
            mcp_server_cards=(self.card(),),
            expires_at=expires_at or (_NOW + timedelta(minutes=15)),
        )
        entries = (
            *base.entries,
            self._expanded_entry(_READ_TOOL, "1"),
            self._expanded_entry(_WRITE_TOOL, "2"),
        )
        return CapabilityCatalog(
            scope=base.scope,
            revision=base.revision.model_copy(
                update={"descriptor_count": len(entries)}
            ),
            entries=entries,
        )

    @staticmethod
    def _expanded_entry(tool_name: str, salt: str) -> CapabilityIndexEntry:
        return CapabilityIndexEntry(
            capability_ref=f"cap_{salt * 32}",
            source=CapabilitySource.MCP_SERVER,
            stable_name=tool_name,
            display_name=tool_name,
            concise_description=f"{tool_name} on {_SERVER}",
            connector_label=_SERVER,
        )

    def card(self):  # type: ignore[no-untyped-def]
        return self.make_card(
            name=_SERVER,
            required_scopes=("docs:read",),
        ).model_copy(update={"server_id": "srv_linear"})

    def tools(
        self,
        *,
        read_schema: Mapping[str, Any] | None = None,
        omit: Sequence[str] = (),
    ):  # type: ignore[no-untyped-def]
        descriptors = [
            self.make_tool(
                name=_READ_TOOL, input_schema=dict(read_schema or _READ_SCHEMA)
            ),
            self.make_tool(name=_WRITE_TOOL, input_schema=dict(_WRITE_SCHEMA)),
        ]
        return tuple(item for item in descriptors if item.name not in omit)

    def mcp(
        self,
        context: AgentRuntimeContext,
        *,
        read_schema: Mapping[str, Any] | None = None,
        omit: Sequence[str] = (),
        tool_outputs: Mapping[str, Mapping[str, object]] | None = None,
        call_error: Exception | None = None,
    ):  # type: ignore[no-untyped-def]
        """Return the connector-side dispatch witness and the real dispatcher.

        ``client.calls`` — not ``provider.created_clients`` — is the witness for
        "did anything actually reach the connector". Re-resolving the live
        descriptor legitimately opens a client of its own, so counting clients
        would confuse revalidation with dispatch.
        """

        client = _RecordingClient(
            tools=self.tools(read_schema=read_schema, omit=omit),
            outputs=dict(tool_outputs or {_READ_TOOL: {"items": [{"id": "L-1"}]}}),
            error=call_error,
        )
        provider = self.FakeMcpProvider(cards=(self.card(),), clients={_SERVER: client})
        registry = DynamicMcpRegistry(providers=(provider,))
        return (
            registry,
            client,
            CallMcpTool(
                registry=registry,
                loader=McpLoader(registry),
                runtime_context=context,
            ),
        )

    def bindings(
        self, catalog: CapabilityCatalog
    ) -> RunScopedCapabilityDispatchBindings:
        by_name = {tool.name: tool for tool in self.tools()}
        return RunScopedCapabilityDispatchBindings.from_disclosed(
            (entry.capability_ref, _SERVER, by_name[entry.stable_name])
            for entry in catalog.entries
            if entry.stable_name in by_name
        )

    def revalidation(
        self,
        context: AgentRuntimeContext,
        catalog: CapabilityCatalog,
        *,
        published: CapabilityCatalog | None = None,
    ) -> CapabilityRefRevalidation:
        source = InMemoryCatalogGenerationSource()
        generation = catalog.generation
        live = (published or catalog).generation
        assert generation is not None
        assert live is not None
        source.publish(
            CapabilityRefRevisionBinding.scope_for(generation, run_id=context.run_id),
            live,
        )
        return CapabilityRefRevalidation(
            revalidator=RevisionBindingRevalidator(
                CapabilityCatalogRevisionAuthority(source)
            ),
            subject_fingerprint=AuthorizedCatalogBuilder(
                reference_key=_REFERENCE_KEY
            ).subject_fingerprint(context),
        )

    def bind_gateway(self, context: AgentRuntimeContext):  # type: ignore[no-untyped-def]
        events = _RecordedOperationEvents()
        operation_token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id="conv_f35",
                run_id=context.run_id,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                workspace=None,
                user=None,
            ),
            ledger_emitter=events,
            artifact_service=None,
            mode=OperationGatewayMode.ENFORCE,
            canonical_arguments_durable=True,
        )
        descriptors = OperationDescriptorRegistry()
        classifier = OperationClassifier(descriptors=descriptors)
        result_store = _ResultStore()
        stage_ledger = FakeLedger()
        service_token = McpOperationGatewayContext.bind_for_run(
            McpOperationGatewayServices(
                gateway=OperationGateway(
                    descriptors=descriptors,
                    classifier=classifier,
                ),
                descriptors=descriptors,
                classifier=classifier,
                stager=EffectStager(
                    ledger=stage_ledger,
                    outbox=FakeOutbox(),
                    clock=FakeClock(),
                    stage_ids=FakeStageIds(),
                ),
                stage_scope=EffectStageScope(
                    run_id=context.run_id,
                    owner_ref=f"principal://users/{context.user_id}",
                ),
                stage_author=EffectActorIdentity(
                    actor=EffectActor.USER,
                    principal_ref=f"principal://users/{context.user_id}",
                ),
                result_store=result_store,
                argument_store=_ArgumentStore(),
                connector_overrides=ConnectorWritePolicyOverrides(),
            )
        )
        return events, result_store, stage_ledger, operation_token, service_token

    def invoke_tool(
        self,
        *,
        context: AgentRuntimeContext,
        catalog: CapabilityCatalog,
        executor: object,
        revalidation: CapabilityRefRevalidation,
    ) -> CapabilityInvokeTool:
        return CapabilityInvokeTool(
            access=CapabilityCatalogAccess(
                catalog=catalog,
                runtime_context=context,
                clock=lambda: _NOW,
            ),
            executor=executor,  # type: ignore[arg-type]
            revalidation=revalidation,
        )

    @staticmethod
    def ref_for(catalog: CapabilityCatalog, stable_name: str) -> str:
        return next(
            entry.capability_ref
            for entry in catalog.entries
            if entry.stable_name == stable_name
        )

    @staticmethod
    def target_for(catalog: CapabilityCatalog, stable_name: str):  # type: ignore[no-untyped-def]
        return CapabilityInvocationTarget.from_catalog_entry(
            next(entry for entry in catalog.entries if entry.stable_name == stable_name)
        )


@dataclass
class _RecordingClient:
    """Connector fake that records every dispatch and can fail like a vendor."""

    tools: Sequence[object]
    resources: Sequence[object] = ()
    outputs: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    error: Exception | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[object]:
        return self.tools

    async def list_resources(self) -> Sequence[object]:
        return self.resources

    async def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]):  # type: ignore[no-untyped-def]
        self.calls.append((tool_name, dict(arguments)))
        if self.error is not None:
            raise self.error
        return self.outputs.get(
            tool_name,
            {"content": [{"type": "text", "text": f"called {tool_name}"}]},
        )


class TestInnerOperationEntersTheGateway(ExecutorHarness):
    """The inner operation is a real gateway operation, not a parallel route."""

    async def test_a_bridge_invocation_runs_one_full_gateway_operation(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        events, result_store, _stage, op_token, svc_token = self.bind_gateway(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["invocation"]["receipt"]["status"] == (
            CapabilityInvocationStatus.COMPLETED.value
        )
        # The gateway emitted its own canonical operation rows for the inner op.
        assert events.types() == [
            LedgerEventType.OPERATION_REQUESTED.value,
            LedgerEventType.OPERATION_CLASSIFIED.value,
            LedgerEventType.OPERATION_COMPLETED.value,
        ]
        # ...under one operation identity, which is also the receipt's handle.
        operation_ids = set(events.operation_ids())
        assert len(operation_ids) == 1
        operation_id = operation_ids.pop()
        assert (
            result["invocation"]["receipt"]["invocation_ref"]
            == f"operation://{operation_id}/result"
        )
        assert result_store.calls[0][0] == operation_id
        assert [name for name, _args in client.calls] == [_READ_TOOL]

    async def test_an_effectful_capability_stages_rather_than_completing(self) -> None:
        """Approval/effect staging is the gateway's, not the bridge's."""

        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        events, _results, stage_ledger, op_token, svc_token = self.bind_gateway(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _WRITE_TOOL),
                    "arguments": {"issue_id": "L-1", "title": "renamed"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["invocation"]["receipt"]["status"] == (
            CapabilityInvocationStatus.STAGED.value
        )
        # A staged effect never reaches the connector — the gateway held it.
        assert client.calls == []
        # The effect landed in the real stage ledger under its own operation id.
        assert stage_ledger.events_by_stage
        assert events.types()[:2] == [
            LedgerEventType.OPERATION_REQUESTED.value,
            LedgerEventType.OPERATION_CLASSIFIED.value,
        ]
        assert len(set(events.operation_ids())) == 1

    async def test_the_inner_operation_carries_the_run_derived_audit_identity(
        self,
    ) -> None:
        """Its identity is allocated by the same seam a direct MCP call uses.

        A directly registered MCP tool takes its operation id from the active
        :class:`RuntimeCallContext`, which derives it from the run snapshot and
        the model's tool-call id. The bridge path must not invent one, or the
        inner operation would not be auditable back to the model turn.
        """

        from agent_runtime.execution.call_identity import (
            RuntimeCallContext,
            RuntimeToolCallIdentity,
        )

        context = self.context()
        catalog = self.catalog(context)
        _registry, _client, dispatcher = self.mcp(context)
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        identity = RuntimeToolCallIdentity(
            run_id=context.run_id,
            snapshot_id=f"snap_{'e' * 32}",
            execution_scope="supervisor",
            model_turn=1,
            model_tool_call_id="call_bridge_1",
            operation_id="op_00000000-0000-4000-8000-0000000000f3",
            control_call_id="runtime-control:f35",
        )
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            with RuntimeCallContext.bind(identity):
                await tool.ainvoke(
                    {
                        "capability_ref": self.ref_for(catalog, _READ_TOOL),
                        "arguments": {"team": "ENG"},
                    }
                )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert set(events.operation_ids()) == {identity.derived_operation_id(1)}

    def test_the_executor_imports_no_second_dispatch_path(self) -> None:
        """Reuse is structural: the gateway machinery is absent by construction."""

        import agent_runtime.capabilities.discovery.executor as module

        tree = ast.parse(Path(module.__file__).read_text())
        imported = {
            f"{node.module or ''}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        forbidden = {
            "OperationGateway",
            "OperationRequestFactory",
            "OperationRequest",
            "McpOperationAdapter",
            "McpOperationGatewayContext",
            "McpOperationGatewayServices",
            "EffectStager",
            "OperationDescriptorRegistry",
            "OperationClassifier",
            "DynamicMcpRegistry",
        }
        assert not {name.rsplit(".", 1)[-1] for name in imported} & forbidden
        assert any(name.endswith(".CallMcpTool") for name in imported)

    def test_the_dispatcher_field_admits_only_the_mcp_dispatcher(self) -> None:
        with pytest.raises(TypeError) as exc_info:
            GatewayCapabilityExecutor(
                bindings=_EmptyBindings(),
                loader=McpLoader(DynamicMcpRegistry(providers=())),
                dispatcher=object(),  # type: ignore[arg-type]
            )

        assert str(exc_info.value) == (
            GatewayCapabilityExecutor.Messages.DISPATCHER_TYPE
        )


class TestRevalidationBeforeDispatch(ExecutorHarness):
    """Nothing is dispatched until the live descriptor agrees with disclosure."""

    async def test_a_schema_change_between_describe_and_invoke_fails_closed(
        self,
    ) -> None:
        context = self.context()
        catalog = self.catalog(context)
        # The catalog (and therefore the recorded binding) was disclosed against
        # the original schema; the server now publishes a different one.
        bindings = self.bindings(catalog)
        _registry, client, dispatcher = self.mcp(
            context,
            read_schema=_CHANGED_READ_SCHEMA,
        )
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=bindings,
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    # Arguments that satisfied the *old* schema.
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )
        # Deterministic: nothing reached the connector or the gateway.
        assert client.calls == []
        assert events.rows == []

    async def test_a_withdrawn_capability_refuses_without_dispatching(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        bindings = self.bindings(catalog)
        _registry, client, dispatcher = self.mcp(context, omit=(_READ_TOOL,))
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=bindings,
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )
        assert client.calls == []
        assert events.rows == []

    async def test_a_superseded_generation_refuses_before_the_executor_runs(
        self,
    ) -> None:
        """The RB revalidation gate precedes every descriptor or connector call."""

        context = self.context()
        catalog = self.catalog(context)
        superseded = self.catalog(
            context,
            expires_at=_NOW + timedelta(minutes=30),
        )
        _registry, client, dispatcher = self.mcp(context)
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        recording = _RecordingExecutor(
            inner=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            )
        )
        # Publish a *different* live generation for the same bound scope.
        other = AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=catalog.scope,
            task_policy_selection_ref=f"{_SELECTION_REF}-next",
            mcp_server_cards=(self.card(),),
            expires_at=_NOW + timedelta(minutes=15),
        )
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=recording,
            revalidation=self.revalidation(context, catalog, published=other),
        )
        del superseded

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )
        assert recording.calls == 0
        assert client.calls == []
        assert events.rows == []

    async def test_an_unbound_capability_is_undispatchable(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        executor = GatewayCapabilityExecutor(
            bindings=_EmptyBindings(),
            loader=dispatcher.loader,
            dispatcher=dispatcher,
        )

        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            await executor.execute(
                target=self.target_for(catalog, _READ_TOOL),
                arguments={"team": "ENG"},
                idempotency_key=None,
                runtime_context=context,
            )

        assert exc_info.value.code is (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )
        assert client.calls == []

    async def test_a_product_tool_card_has_no_non_model_dispatch_seam(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        executor = GatewayCapabilityExecutor(
            bindings=self.bindings(catalog),
            loader=dispatcher.loader,
            dispatcher=dispatcher,
        )
        card_target = CapabilityInvocationTarget(
            capability_ref=f"cap_{'7' * 32}",
            stable_name="drive_search",
            source=CapabilitySource.TOOL_CARD,
            connector_label="drive",
            effect_class=catalog.entries[0].effect_class,
            approval_cue=catalog.entries[0].approval_cue,
        )

        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            await executor.execute(
                target=card_target,
                arguments={},
                idempotency_key=None,
                runtime_context=context,
            )

        assert exc_info.value.code is (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )

    async def test_a_foreign_subject_never_dispatches(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        executor = GatewayCapabilityExecutor(
            bindings=self.bindings(catalog),
            loader=dispatcher.loader,
            dispatcher=dispatcher,
        )

        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            await executor.execute(
                target=self.target_for(catalog, _READ_TOOL),
                arguments={"team": "ENG"},
                idempotency_key=None,
                runtime_context=self.context(run_id="run_other"),
            )

        assert exc_info.value.code is (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )
        assert client.calls == []

    async def test_an_idempotency_key_refuses_rather_than_being_dropped(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        executor = GatewayCapabilityExecutor(
            bindings=self.bindings(catalog),
            loader=dispatcher.loader,
            dispatcher=dispatcher,
        )

        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            await executor.execute(
                target=self.target_for(catalog, _READ_TOOL),
                arguments={"team": "ENG"},
                idempotency_key="once-only",
                runtime_context=context,
            )

        assert exc_info.value.code is (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )
        assert client.calls == []


@dataclass
class _RecordingExecutor:
    """Count how often the real executor is reached at all."""

    inner: GatewayCapabilityExecutor
    calls: int = 0

    async def execute(self, **kwargs: object):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await self.inner.execute(**kwargs)  # type: ignore[arg-type]


class TestArgumentsAreCheckedAgainstTheRevalidatedSchema(ExecutorHarness):
    """The live schema governs, and the check can only refuse."""

    async def test_a_missing_required_argument_refuses_before_dispatch(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"limit": 5},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.INVALID_REQUEST.value
        )
        assert client.calls == []
        assert events.rows == []

    @pytest.mark.parametrize(
        "arguments",
        [
            {"team": 7},
            {"team": "ENG", "limit": "many"},
            {"team": "ENG", "unknown": 1},
        ],
    )
    def test_the_check_refuses_shapes_the_live_schema_rejects(
        self,
        arguments: dict[str, Any],
    ) -> None:
        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            CapabilityArgumentSchemaCheck.enforce(
                arguments=arguments,
                schema=_READ_SCHEMA,
            )

        assert exc_info.value.code is CapabilityDiscoveryErrorCode.INVALID_REQUEST

    @pytest.mark.parametrize(
        "arguments",
        [
            {"team": "ENG"},
            {"team": "ENG", "limit": 5},
        ],
    )
    def test_the_check_admits_shapes_the_live_schema_accepts(
        self,
        arguments: dict[str, Any],
    ) -> None:
        CapabilityArgumentSchemaCheck.enforce(
            arguments=arguments,
            schema=_READ_SCHEMA,
        )

    def test_an_open_schema_keeps_undeclared_arguments(self) -> None:
        """Refusing extras against an open schema would narrow availability only."""

        CapabilityArgumentSchemaCheck.enforce(
            arguments={"issue_id": "L-1", "vendor_extra": True},
            schema=_WRITE_SCHEMA,
        )

    @pytest.mark.parametrize(
        "schema",
        ["not-a-mapping", {"type": "array"}],
    )
    def test_an_uncheckable_schema_is_undispatchable(self, schema: object) -> None:
        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            CapabilityArgumentSchemaCheck.enforce(
                arguments={},
                schema=schema,  # type: ignore[arg-type]
            )

        assert exc_info.value.code is (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )

    def test_an_unreadable_type_constraint_is_not_invented(self) -> None:
        """Silence, not a guess: this check may only reject what it can read."""

        CapabilityArgumentSchemaCheck.enforce(
            arguments={"team": {"nested": True}},
            schema={
                "type": "object",
                "properties": {"team": {"type": "vendor-specific"}},
            },
        )

    def test_a_boolean_never_satisfies_a_numeric_constraint(self) -> None:
        with pytest.raises(CapabilityExecutionRefused):
            CapabilityArgumentSchemaCheck.enforce(
                arguments={"limit": True},
                schema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                },
            )


class TestBridgeRecursionGuardIsExtended(ExecutorHarness):
    """A bridge tool can never become a dispatch coordinate."""

    @pytest.mark.parametrize(
        "spelling",
        ["invoke_capability", "Invoke_Capability", " search_capabilities "],
    )
    def test_a_bridge_name_can_never_be_a_dispatch_tool(self, spelling: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityDispatchBinding(
                capability_ref=f"cap_{'3' * 32}",
                server_name=_SERVER,
                tool_name=spelling,
                schema_digest="a" * 64,
            )

        assert isinstance(
            exc_info.value.errors()[0].get("ctx", {}).get("error"),
            CapabilityBridgeRecursionError,
        )

    def test_a_bridge_name_can_never_be_a_dispatch_server(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CapabilityDispatchBinding(
                capability_ref=f"cap_{'3' * 32}",
                server_name="describe_capability",
                tool_name=_READ_TOOL,
                schema_digest="a" * 64,
            )

        assert isinstance(
            exc_info.value.errors()[0].get("ctx", {}).get("error"),
            CapabilityBridgeRecursionError,
        )

    def test_the_reserved_set_is_derived_from_the_closed_enum(self) -> None:
        """A fourth bridge tool extends this guard without editing it."""

        from agent_runtime.capabilities.discovery.contracts import (
            CapabilityBridgeToolName,
        )

        for reserved in CapabilityBridgeToolName.reserved_names():
            with pytest.raises(ValidationError):
                CapabilityDispatchBinding(
                    capability_ref=f"cap_{'3' * 32}",
                    server_name=_SERVER,
                    tool_name=reserved,
                    schema_digest="a" * 64,
                )

    async def test_probing_the_bridge_through_the_bridge_finds_nothing(self) -> None:
        """A bridge ref is answered exactly like any unknown ref."""

        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        result = await tool.ainvoke(
            {"capability_ref": f"cap_{'0' * 32}", "arguments": {}}
        )

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND.value
        )
        assert client.calls == []

    def test_no_bridge_tool_is_reachable_as_a_catalog_capability(self) -> None:
        context = self.context()
        catalog = self.catalog(context)

        assert not any(
            self.bindings(catalog).binding_for(entry.capability_ref) is not None
            and entry.stable_name
            in {"search_capabilities", "describe_capability", "invoke_capability"}
            for entry in catalog.entries
        )


class TestFailuresAreSanitized(ExecutorHarness):
    """Connector detail never becomes model-visible output."""

    async def test_a_connector_failure_returns_a_fixed_safe_message(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(
            context,
            call_error=RuntimeError("vendor-secret token=abc123 at /Users/host/path"),
        )
        _events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        try:
            result = await tool.ainvoke(
                {
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        rendered = repr(result)
        assert "vendor-secret" not in rendered
        assert "abc123" not in rendered
        assert "/Users/host/path" not in rendered
        assert "RuntimeError" not in rendered
        # The connector really was reached, and really failed.
        assert [name for name, _args in client.calls] == [_READ_TOOL]
        # The model reads a body-free refusal receipt, never the vendor text.
        receipt = result["invocation"]["receipt"]
        assert receipt["status"] == CapabilityInvocationStatus.REFUSED.value
        assert receipt["safe_summary"] == (
            "The capability did not run; no external change was made."
        )

    def test_a_typed_refusal_carries_a_code_and_no_text(self) -> None:
        refusal = CapabilityExecutionRefused(
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE
        )

        assert refusal.code is CapabilityDiscoveryErrorCode.CAPABILITY_STALE
        assert str(refusal) == CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value

    def test_an_inadmissible_code_collapses_to_execution_failed(self) -> None:
        """An executor may never become the catalog-membership oracle."""

        refusal = CapabilityExecutionRefused(
            CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND
        )

        assert refusal.code is CapabilityDiscoveryErrorCode.EXECUTION_FAILED

    async def test_a_held_dispatch_never_reports_a_completed_effect(self) -> None:
        """With no gateway composed, the dispatcher holds and the bridge refuses."""

        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        tool = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        result = await tool.ainvoke(
            {
                "capability_ref": self.ref_for(catalog, _READ_TOOL),
                "arguments": {"team": "ENG"},
            }
        )

        assert result.get("invocation") is None
        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.EXECUTION_FAILED.value
        )
        assert client.calls == []


class TestBindingTable(ExecutorHarness):
    """The binding table is the only source of dispatch coordinates."""

    def test_it_satisfies_the_declared_port(self) -> None:
        assert isinstance(
            RunScopedCapabilityDispatchBindings(),
            CapabilityDispatchBindingPort,
        )

    def test_duplicate_refs_are_refused_rather_than_overwritten(self) -> None:
        binding = CapabilityDispatchBinding(
            capability_ref=f"cap_{'5' * 32}",
            server_name=_SERVER,
            tool_name=_READ_TOOL,
            schema_digest="b" * 64,
        )

        with pytest.raises(ValueError) as exc_info:
            RunScopedCapabilityDispatchBindings((binding, binding))

        assert str(exc_info.value) == (
            RunScopedCapabilityDispatchBindings.Messages.DUPLICATE_REF
        )

    def test_the_schema_digest_is_reproducible_and_key_order_free(self) -> None:
        first = CapabilityDispatchBinding.schema_digest_for(
            {"type": "object", "properties": {"a": {"type": "string"}}}
        )
        second = CapabilityDispatchBinding.schema_digest_for(
            {"properties": {"a": {"type": "string"}}, "type": "object"}
        )
        third = CapabilityDispatchBinding.schema_digest_for({"type": "object"})

        assert first == second
        assert first != third

    def test_an_unknown_ref_resolves_to_nothing(self) -> None:
        context = self.context()
        catalog = self.catalog(context)

        assert self.bindings(catalog).binding_for(f"cap_{'9' * 32}") is None


class TestBudgetAccounting(ExecutorHarness):
    """One bridge call is one model-visible F4 call; the inner op is its own."""

    @staticmethod
    def _budget(tool_name: str) -> ToolBudgetRecord:
        return ToolBudgetRecord(
            org_id=None,
            tool_name=tool_name,
            max_calls_per_run=5,
            enforcement=ToolBudgetEnforcement.HARD,
        )

    #: Every tool name this run could conceivably have spent budget under.
    CHARGEABLE_NAMES = (
        "invoke_capability",
        "search_capabilities",
        "describe_capability",
        "call_mcp_tool",
        _READ_TOOL,
        _WRITE_TOOL,
    )

    @classmethod
    def _total_charged(cls, ledger: ToolCallLedger) -> int:
        """Return the run's total budget-scoped spend across every such name."""

        return sum(ledger.charged_calls(name) for name in cls.CHARGEABLE_NAMES)

    async def test_one_bridge_call_charges_exactly_one_model_visible_call(
        self,
    ) -> None:
        from langchain_core.tools import StructuredTool

        from agent_runtime.capabilities.discovery.contracts import (
            CapabilityBridgeToolName,
            CapabilityInvokeRequest,
        )

        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        bridge = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )
        # Wrap the bridge exactly as the factory wraps any other model tool.
        guarded = ToolBudgetGuardedTool(
            name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value,
            description=bridge.description,
            inner=StructuredTool.from_function(
                coroutine=bridge.ainvoke,
                name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value,
                description=bridge.description,
                args_schema=CapabilityInvokeRequest,
            ),
        )
        ledger = ToolCallLedger(run_id=context.run_id)
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [
                    self._budget(CapabilityBridgeToolName.INVOKE_CAPABILITY.value),
                    self._budget("call_mcp_tool"),
                ]
            ),
            ledger=ledger,
        )
        guard_token = ToolBudgetGuard.bind_for_run(guard)

        try:
            result = await guarded._arun(
                raw_input={
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            ToolBudgetGuard.unbind(guard_token)
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["invocation"]["receipt"]["status"] == (
            CapabilityInvocationStatus.COMPLETED.value
        )
        # Exactly one model-visible F4 call — the bridge's own.
        assert (
            ledger.charged_calls(CapabilityBridgeToolName.INVOKE_CAPABILITY.value) == 1
        )
        # The inner operation is not a second model-visible tool call, so it is
        # never charged in the model-visible dimension a second time.
        assert ledger.charged_calls("call_mcp_tool") == 0
        # Nothing else was charged anywhere in that dimension either.
        assert self._total_charged(ledger) == 1
        # It is charged in its *own* dimension instead: one gateway operation.
        assert len(set(events.operation_ids())) == 1
        assert events.types().count(LedgerEventType.OPERATION_REQUESTED.value) == 1

    async def test_a_direct_connector_call_does_charge_the_dimension_it_skips(
        self,
    ) -> None:
        """Negative control: ``call_mcp_tool`` is chargeable, and here it is charged.

        Without this, "the inner operation charged ``call_mcp_tool`` zero times"
        would be indistinguishable from "this test never registered a budget
        that could charge it". The same dispatcher, wrapped as a model tool and
        called by the model, spends exactly one call in that dimension.
        """

        from langchain_core.tools import StructuredTool

        from agent_runtime.capabilities.mcp.cards import McpToolCallRequest

        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(context)
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        guarded = ToolBudgetGuardedTool(
            name="call_mcp_tool",
            description=dispatcher.description,
            inner=StructuredTool.from_function(
                coroutine=dispatcher.ainvoke,
                name="call_mcp_tool",
                description=dispatcher.description,
                args_schema=McpToolCallRequest,
            ),
        )
        ledger = ToolCallLedger(run_id=context.run_id)
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware([self._budget("call_mcp_tool")]),
            ledger=ledger,
        )
        guard_token = ToolBudgetGuard.bind_for_run(guard)

        try:
            await guarded._arun(
                raw_input={
                    "server_name": _SERVER,
                    "tool_name": _READ_TOOL,
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            ToolBudgetGuard.unbind(guard_token)
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert [name for name, _args in client.calls] == [_READ_TOOL]
        assert ledger.charged_calls("call_mcp_tool") == 1
        # Same one inner operation as the bridge path produces — the difference
        # is only which dimension the model-visible call landed in.
        assert len(set(events.operation_ids())) == 1
        del catalog

    async def test_a_refused_bridge_call_still_costs_exactly_one_call(self) -> None:
        """Refusal accounting cannot become cheaper than admission accounting."""

        from langchain_core.tools import StructuredTool

        from agent_runtime.capabilities.discovery.contracts import (
            CapabilityBridgeToolName,
            CapabilityInvokeRequest,
        )

        context = self.context()
        catalog = self.catalog(context)
        _registry, client, dispatcher = self.mcp(
            context,
            read_schema=_CHANGED_READ_SCHEMA,
        )
        events, _results, _stage, op_token, svc_token = self.bind_gateway(context)
        bridge = self.invoke_tool(
            context=context,
            catalog=catalog,
            executor=GatewayCapabilityExecutor(
                bindings=self.bindings(catalog),
                loader=dispatcher.loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )
        guarded = ToolBudgetGuardedTool(
            name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value,
            description=bridge.description,
            inner=StructuredTool.from_function(
                coroutine=bridge.ainvoke,
                name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value,
                description=bridge.description,
                args_schema=CapabilityInvokeRequest,
            ),
        )
        ledger = ToolCallLedger(run_id=context.run_id)
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [self._budget(CapabilityBridgeToolName.INVOKE_CAPABILITY.value)]
            ),
            ledger=ledger,
        )
        guard_token = ToolBudgetGuard.bind_for_run(guard)

        try:
            result = await guarded._arun(
                raw_input={
                    "capability_ref": self.ref_for(catalog, _READ_TOOL),
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            ToolBudgetGuard.unbind(guard_token)
            McpOperationGatewayContext.unbind(svc_token)
            OperationContext.unbind(op_token)

        assert result["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )
        assert (
            ledger.charged_calls(CapabilityBridgeToolName.INVOKE_CAPABILITY.value) == 1
        )
        # No inner operation was opened, so nothing was charged there.
        assert events.rows == []
