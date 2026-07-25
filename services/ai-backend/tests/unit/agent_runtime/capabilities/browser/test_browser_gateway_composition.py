"""Model-boundary proof: browser actions stage and never call the read client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.mcp import CallMcpTool, DynamicMcpRegistry, McpLoader
from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationGateResolver,
    McpOperationGatewayContext,
    McpOperationGatewayServices,
    McpOperationStoredResult,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.ledger_models import EffectActor, LedgerEventType
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_worker.browser_operation_storage import RuntimeBrowserActionPlanStore
from runtime_worker.mcp_operation_storage import RuntimeMcpOperationArgumentStore
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-browser",
        org_id="org-browser",
        roles={"employee"},
        permission_scopes={"browser:use", "docs:read"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run-browser",
        trace_id="trace-browser",
    )


def _exact_click() -> dict[str, object]:
    return {
        "sessionRef": "browser-session://ses_exact",
        "pageRef": "browser-page://pg_exact",
        "origin": "https://example.com",
        "topLevelOrigin": "https://example.com",
        "elementRef": "e4_2",
        "elementFingerprint": "a" * 64,
        "pageGeneration": 4,
        "formFingerprint": "d" * 64,
        "formPayloadDigest": "e" * 64,
        "formActionUrl": "https://example.com/send",
        "method": "POST",
    }


@dataclass
class _OperationEvents:
    rows: list[str] = field(default_factory=list)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del payload, summary
        self.rows.append(event_type.value)


class _UnusedResultStore:
    async def store_read_result(
        self, *, request: object, output: Mapping[str, object]
    ) -> McpOperationStoredResult:
        del request, output
        raise AssertionError("a staged browser action cannot persist a read result")


class _Fixture(DynamicMcpLoadingMixin):
    def call_tool(self) -> tuple[CallMcpTool, object]:
        client = self.FakeMcpClient(
            tools=(self.make_tool(name="browser_click"),),
            resources=(),
        )
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name="desktop_browser"),),
            clients={"desktop_browser": client},
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        return (
            CallMcpTool(
                registry=registry,
                loader=McpLoader(registry),
                runtime_context=_runtime_context(),
            ),
            provider,
        )


async def test_exact_click_enters_a4_without_creating_browser_client(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SURFACES_V2", "true")
    context = _runtime_context()
    events = _OperationEvents()
    operation_token = OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id="conv-browser",
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
    coordinator = InMemoryArtifactPublicationCoordinator()
    blobs = InMemoryArtifactBlobStore(coordinator)
    references = InMemoryArtifactReferenceStore(coordinator)
    plans = RuntimeBrowserActionPlanStore(
        blobs=blobs,
        references=references,
        org_id=context.org_id,
        user_id=context.user_id,
    )
    descriptors = DEFAULT_OPERATION_DESCRIPTORS
    classifier = OperationClassifier(descriptors=descriptors)
    ledger = FakeLedger()
    services_token = McpOperationGatewayContext.bind_for_run(
        McpOperationGatewayServices(
            gateway=OperationGateway(
                descriptors=descriptors,
                classifier=classifier,
                gates=McpOperationGateResolver(),
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
                owner_ref=f"principal://users/{context.user_id}",
            ),
            stage_author=EffectActorIdentity(
                actor=EffectActor.SYSTEM,
                principal_ref="principal://system/browser-operation-gateway",
            ),
            result_store=_UnusedResultStore(),
            argument_store=RuntimeMcpOperationArgumentStore(
                blobs=blobs,
                references=references,
                org_id=context.org_id,
                user_id=context.user_id,
            ),
            browser_plans=plans,
            connector_overrides=ConnectorWritePolicyOverrides(),
        )
    )
    tool, provider = _Fixture().call_tool()
    try:
        result = await tool.ainvoke(
            {
                "server_name": "desktop_browser",
                "tool_name": "browser_click",
                "arguments": _exact_click(),
                "tool_call_id": "call-browser",
            }
        )
    finally:
        McpOperationGatewayContext.unbind(services_token)
        OperationContext.unbind(operation_token)

    assert provider.created_clients == []
    assert result["output"]["status"] == "staged"
    staged = next(iter(ledger.events_by_stage.values()))[0].payload
    assert staged["executor"] == "browser"
    assert staged["proposal_kind"] == "browser_submission"
    assert staged["agent_hold"] is True
    assert staged["policy"] == "require"
    plan = await plans.load(content_ref=staged["proposal_content_ref"])
    assert plan is not None
    assert plan.element_ref == "e4_2"
    assert plan.form_payload_digest == "e" * 64
    assert plan.digest == staged["proposal_digest"]
    assert events.rows == [
        "operation.requested",
        "operation.classified",
        "operation.completed",
    ]
