"""Adversarial D1 proof: approval cannot outlive connector authorization."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.mcp.effect_material import McpEffectMaterial
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.effects.claims import EffectClaimState
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.effects.coordinator import (
    EffectCoordinator,
    EffectCoordinatorStatus,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.executor_registry import EffectExecutorRegistry
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from agent_runtime.surfaces_v2.entities import EffectTarget, EffectExecutionRequest
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectOutcome,
    EffectPolicy,
    EffectProposalKind,
)
from agent_runtime.surfaces_v2.mcp_connector import McpStageCommitConnector
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore
from runtime_worker.mcp_effect_executor import McpEffectExecutor
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_ORG_ID = "org_f013"
_USER_ID = "user_f013"
_RUN_ID = "run_f013"
_CAPABILITY = "linear"
_OP = "update_issue"
_CONTENT_REF = "operation://op_00000000-0000-4000-8000-000000000013/args"


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        org_id=_ORG_ID,
        user_id=_USER_ID,
        run_id=_RUN_ID,
        trace_id="trace_f013",
        roles={"employee"},
        permission_scopes={"docs:write"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-4o-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
    )


def _scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id=_ORG_ID,
        user_id=_USER_ID,
        conversation_id="conv_f013",
        run_id=_RUN_ID,
        owner_ref=f"principal://users/{_USER_ID}",
    )


@dataclass(frozen=True)
class _ScopeResolver:
    async def resolve(self, *, run_id: str) -> EffectExecutionScope | None:
        return _scope() if run_id == _RUN_ID else None


@dataclass(frozen=True)
class _References:
    values: Mapping[str, bytes]

    def open(
        self, *, scope: EffectExecutionScope, reference: str
    ) -> AsyncIterator[bytes]:
        async def _stream() -> AsyncIterator[bytes]:
            if scope != _scope():
                return
            value = self.values.get(reference)
            if value is not None:
                yield value

        return _stream()


@dataclass(frozen=True)
class _MaterialResolver:
    material: McpEffectMaterial

    async def resolve(
        self, request: EffectExecutionRequest
    ) -> McpEffectMaterial | None:
        return (
            self.material
            if request.proposal_digest == self.material.proposal_digest
            else None
        )


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, object]
    ) -> Mapping[str, object]:
        self.calls.append((tool_name, dict(arguments)))
        return {"ok": True}


@pytest.mark.asyncio
async def test_revoked_connector_after_approval_never_reaches_mcp_dispatcher() -> None:
    """The coordinator's last authorization decision is before ``apply``.

    The stage is legitimately classified, staged and approved.  Only after
    that historical approval does the connector become disabled.  The worker
    must refuse the command without constructing a client or calling the MCP
    dispatcher, proving no stale approval can revive a revoked grant.
    """

    arguments = {"issue_id": "ENG-13", "title": "Approved exactly"}
    proposal_bytes = canonical_json_bytes(arguments)
    proposal_digest = canonical_json_sha256(arguments)
    target_ref = f"mcp-target://{_CAPABILITY}/{_OP}"
    target_bytes = canonical_json_bytes({"capability": _CAPABILITY, "op": _OP})
    target_digest = canonical_json_sha256({"capability": _CAPABILITY, "op": _OP})
    stage_scope = EffectStageScope(run_id=_RUN_ID, owner_ref=_scope().owner_ref)
    actor = EffectActorIdentity(
        actor=EffectActor.USER, principal_ref=_scope().owner_ref
    )
    ledger = FakeLedger()
    outbox = FakeOutbox()
    stager = EffectStager(
        ledger=ledger,
        outbox=outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    staged = await stager.stage(
        scope=stage_scope,
        proposed_effect=ProposedEffect(
            operation_id="op_00000000-0000-4000-8000-000000000013",
            executor=EffectExecutorKind.MCP,
            target=EffectTarget(
                executor=EffectExecutorKind.MCP,
                capability=_CAPABILITY,
                op=_OP,
                target_ref=target_ref,
                display_label="update_issue on linear",
            ),
            target_digest=target_digest,
            display_target="update_issue on linear",
            proposal_kind=EffectProposalKind.CANONICAL_ARGUMENTS,
            proposal_content_ref=_CONTENT_REF,
            proposal_digest=proposal_digest,
            proposal_media_type="application/json",
            effect_class=EffectClass.EXTERNAL_REVERSIBLE,
            policy_snapshot_ref="policy://runs/run_f013/mcp",
        ),
        policy_snapshot=EffectPolicySnapshot(
            snapshot_ref="policy://runs/run_f013/mcp",
            descriptor_known=True,
            user_policy=EffectPolicy.ASK,
        ),
        actor=actor,
        idempotency_key="stage-f013",
    )
    await stager.decide(
        scope=stage_scope,
        stage_id=staged.stage_id,
        revision=staged.current_revision.revision,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=staged.current_revision.proposal_digest,
        target_digest=staged.target_digest,
        actor=actor,
        idempotency_key="approve-f013",
    )
    command = next(iter(outbox.commands.values()))

    fixture = DynamicMcpLoadingMixin()
    client = _RecordingClient()
    # This is the current registry state at worker dispatch time, not the
    # historical stage-time state: authorization has been revoked.
    revoked_card = fixture.make_card(
        name=_CAPABILITY,
        required_scopes={"docs:write"},
        enabled=False,
    )
    provider = fixture.FakeMcpProvider(
        cards=(revoked_card,),
        clients={_CAPABILITY: client},  # type: ignore[arg-type]
    )
    registry = DynamicMcpRegistry(providers=(provider,))
    connector = McpStageCommitConnector(
        runtime_context=_runtime_context(),
        dependencies_factory=lambda _context: SimpleNamespace(mcp_registry=registry),
        timeout_seconds=1,
    )
    material = McpEffectMaterial(
        target_connector=_CAPABILITY,
        target_op=_OP,
        arguments=arguments,
        target_ref=target_ref,
        target_digest=target_digest,
        proposal_ref=staged.current_revision.proposal_ref,
        proposal_content_ref=_CONTENT_REF,
        proposal_digest=proposal_digest,
    )
    executor = McpEffectExecutor(
        scope=_scope(),
        connector=connector,
        material_resolver=_MaterialResolver(material),
        enabled=True,
    )
    claims = InMemoryEffectClaimStore()
    coordinator = EffectCoordinator(
        ledger=ledger,
        claims=claims,
        scopes=_ScopeResolver(),
        references=_References(
            {
                _CONTENT_REF: proposal_bytes,
                target_ref: target_bytes,
            }
        ),
        executors=EffectExecutorRegistry({EffectExecutorKind.MCP: lambda _: executor}),
    )

    result = await coordinator.handle(command)

    assert result.status is EffectCoordinatorStatus.REFUSED
    assert result.safe_code == "mcp_authorization_revoked"
    assert provider.created_clients == []
    assert client.calls == []
    claim = await claims.get(
        org_id=_ORG_ID,
        executor=EffectExecutorKind.MCP,
        idempotency_key=command.idempotency_key,
    )
    assert claim is not None and claim.state is EffectClaimState.COMPLETED
    assert claim.outcome is EffectOutcome.FAILED
