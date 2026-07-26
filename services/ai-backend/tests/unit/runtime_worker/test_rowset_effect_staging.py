"""Adversarial coverage for the D2 row-set runtime assembly slice."""

from __future__ import annotations

import json

import pytest

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
    StageRowsetWriteTool,
)
from agent_runtime.capabilities.tools.tool_use_enforcement import (
    ToolUsePolicyResolver,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.staging import StagedWriteError
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeRunCommand,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.rowset_effect_staging import RuntimeRowSetEffectProposalPort

pytestmark = pytest.mark.anyio

_ROWS = [
    {
        "row_key": "iss-1",
        "title": "Acme renewal",
        "target_args": {"id": "iss-1", "priority": 2},
        "changes": [{"field": "priority", "old": 1, "new": 2}],
    },
    {
        "row_key": "iss-2",
        "title": "Beta onboarding",
        "target_args": {"id": "iss-2", "priority": 3},
        "changes": [{"field": "priority", "old": 1, "new": 3}],
    },
]
_INPUT = {
    "target_connector": "linear",
    "target_op": "update_issue",
    "title": "Reprioritize",
    "rows": _ROWS,
    "agent_holds": [{"row_key": "iss-2", "reason": "recent reply"}],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(
    mode: OperationGatewayMode,
    *,
    surfaces_v2: bool = True,
) -> RuntimeSettings:
    environment = {
        "SURFACES_V2": "true" if surfaces_v2 else "false",
        "OPERATION_GATEWAY_MODE": mode.value,
    }
    if mode is OperationGatewayMode.ENFORCE and surfaces_v2:
        capabilities = (
            "operation_gateway",
            "effect_stager",
            "effect_commit",
            "mcp_gateway",
        )
        environment.update(
            {
                "EFFECT_STAGER_MODE": "enforce",
                "EFFECT_COMMIT_MODE": "enforce",
                "MCP_GATEWAY_MODE": "enforce",
                "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                    [
                        {
                            "capability": capability,
                            "org_id": "org_d2",
                            "user_id": "user_d2",
                        }
                        for capability in capabilities
                    ]
                ),
            }
        )
    return RuntimeSettings.load(environ=environment)


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_d2",
        org_id="org_d2",
        roles={"employee"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5.4-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run_d2",
        trace_id="trace_d2",
    )


def _run(context: AgentRuntimeContext | None = None) -> RunRecord:
    runtime_context = context or _runtime_context()
    return RunRecord(
        run_id="run_d2",
        conversation_id="conv_d2",
        org_id="org_d2",
        user_id="user_d2",
        user_message_id="msg_d2",
        trace_id="trace_d2",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=runtime_context,
    )


def _command(context: AgentRuntimeContext | None = None) -> RuntimeRunCommand:
    runtime_context = context or _runtime_context()
    return RuntimeRunCommand(
        run_id="run_d2",
        conversation_id="conv_d2",
        org_id="org_d2",
        user_id="user_d2",
        trace_id="trace_d2",
        runtime_context=runtime_context,
    )


def _handler(
    store: InMemoryRuntimeApiStore,
    *,
    settings: RuntimeSettings,
    durable_effects: bool,
) -> RuntimeRunHandler:
    if not durable_effects:
        return RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
            queue=store,
        )
    coordinator = InMemoryArtifactPublicationCoordinator()
    return RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
        queue=store,
        artifact_blob_store=InMemoryArtifactBlobStore(coordinator),
        artifact_reference_store=InMemoryArtifactReferenceStore(coordinator),
    )


def _bind_operation_context(
    handler: RuntimeRunHandler,
    *,
    run: RunRecord,
    command: RuntimeRunCommand,
    mode: OperationGatewayMode,
    durable_arguments: bool,
) -> object:
    return OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
        ),
        policy_snapshot=ToolUsePolicyResolver.resolve(command.runtime_context),
        ledger_emitter=handler._build_operation_ledger_emitter(run),
        artifact_service=None,
        mode=mode,
        canonical_arguments_durable=durable_arguments,
    )


async def test_enforce_assembly_stages_real_a4_rowset_and_never_dispatches() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    command = _command(run.runtime_context)
    store.runs[run.run_id] = run
    store.events_by_run[run.run_id] = []
    handler = _handler(
        store,
        settings=_settings(OperationGatewayMode.ENFORCE),
        durable_effects=True,
    )
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None
    tool = handler._stage_rowset_write_tool(
        command,
        run,
        mcp_gateway_services=services,
    )
    assert isinstance(tool, StageRowsetWriteTool)
    assert isinstance(tool.proposal_stager, RuntimeRowSetEffectProposalPort)
    assert tool.stager is None

    token = _bind_operation_context(
        handler,
        run=run,
        command=command,
        mode=OperationGatewayMode.ENFORCE,
        durable_arguments=True,
    )
    try:
        result = await tool.ainvoke(_INPUT)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    events = store.events_by_run[run.run_id]
    assert [event.event_type.value for event in events] == [
        LedgerEventType.OPERATION_REQUESTED.value,
        LedgerEventType.OPERATION_CLASSIFIED.value,
        LedgerEventType.EFFECT_STAGED.value,
        LedgerEventType.OPERATION_COMPLETED.value,
    ]
    requested, classified, staged, completed = events
    operation_id = requested.payload["operation_id"]
    assert classified.payload == {
        "v": 1,
        "operation_id": operation_id,
        "effect_class": "external_reversible",
        "basis": "descriptor",
        "confidence": 1.0,
    }
    assert staged.payload["operation_id"] == operation_id
    assert staged.payload["executor"] == "builtin"
    assert staged.payload["capability"] == "linear"
    assert staged.payload["op"] == "update_issue"
    assert staged.payload["proposal_kind"] == "row_set"
    assert staged.payload["policy"] == "require"
    assert staged.payload["agent_hold"] is True
    assert completed.payload["operation_id"] == operation_id
    assert completed.payload["outcome"] == "staged"
    assert result == {
        "ok": True,
        "stage_id": staged.payload["stage_id"],
        "surface_id": staged.payload["proposal_ref"],
        "rows_staged": 2,
        "rows_pre_held": 1,
        "status": "held",
    }

    body = await services.argument_store.resolve(
        ref=staged.payload["proposal_content_ref"],
        digest=staged.payload["proposal_digest"],
    )
    assert body == canonical_json_bytes(
        RowSetEffectProposal.model_validate(_INPUT).model_dump(mode="json")
    )
    assert json.loads(body) == RowSetEffectProposal.model_validate(_INPUT).model_dump(
        mode="json"
    )
    assert store.effect_commit_commands == []


async def test_shadow_assembly_keeps_exact_legacy_event_family() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    command = _command(run.runtime_context)
    store.runs[run.run_id] = run
    store.events_by_run[run.run_id] = []
    handler = _handler(
        store,
        settings=_settings(OperationGatewayMode.SHADOW),
        durable_effects=False,
    )
    assert handler._build_mcp_operation_gateway_services(run) is None
    tool = handler._stage_rowset_write_tool(command, run)
    assert isinstance(tool, StageRowsetWriteTool)
    assert tool.proposal_stager is None
    assert tool.stager is not None

    token = _bind_operation_context(
        handler,
        run=run,
        command=command,
        mode=OperationGatewayMode.SHADOW,
        durable_arguments=False,
    )
    try:
        result = await tool.ainvoke(_INPUT)
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["status"] == "staged"
    assert [event.event_type.value for event in store.events_by_run[run.run_id]] == [
        LedgerEventType.SURFACE_CREATED.value,
        LedgerEventType.WRITE_STAGED.value,
        LedgerEventType.REVISION_ADDED.value,
    ]
    assert store.effect_commit_commands == []


async def test_enforce_rejects_tampered_canonical_body_before_stage() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    command = _command(run.runtime_context)
    store.runs[run.run_id] = run
    store.events_by_run[run.run_id] = []
    handler = _handler(
        store,
        settings=_settings(OperationGatewayMode.ENFORCE),
        durable_effects=True,
    )
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None
    port = RuntimeRowSetEffectProposalPort(
        stager=services.stager,
        scope=services.stage_scope,
        actor=services.stage_author,
        argument_store=services.argument_store,
    )
    operation_id = "op_00000000-0000-4000-8000-000000000001"
    content_ref = OperationArgsRefCodec.format(operation_id)
    tampered = canonical_json_bytes(
        {
            **RowSetEffectProposal.model_validate(_INPUT).model_dump(mode="json"),
            "target_op": "delete_issue",
        }
    )
    token = _bind_operation_context(
        handler,
        run=run,
        command=command,
        mode=OperationGatewayMode.ENFORCE,
        durable_arguments=True,
    )
    try:
        OperationContext.require().arguments.put(
            ref=content_ref,
            digest=sha256_hex(tampered),
            canonical_bytes=tampered,
        )
        with pytest.raises(StagedWriteError, match="content changed"):
            await port.stage_row_set(
                proposal=RowSetEffectProposal.model_validate(_INPUT),
                operation_id=operation_id,
            )
    finally:
        OperationContext.unbind(token)  # type: ignore[arg-type]

    assert store.events_by_run[run.run_id] == []
    assert store.effect_commit_commands == []


def test_flag_off_assembly_keeps_model_tool_surface_absent() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    handler = _handler(
        store,
        settings=_settings(OperationGatewayMode.ENFORCE, surfaces_v2=False),
        durable_effects=True,
    )

    assert handler._build_mcp_operation_gateway_services(run) is None
    assert handler._stage_rowset_write_tool(_command(run.runtime_context), run) is None
    assert store.events_by_run == {}
    assert store.effect_commit_commands == []
