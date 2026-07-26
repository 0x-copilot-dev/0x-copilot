"""Bind legacy staged-write approvals to the canonical effect dispatcher.

The v2 staged-write ledger keeps its own fold and presentation events for
backward compatibility.  It does not keep an executor: once the fold proves an
exact approved revision, this adapter turns that server-held revision into an
``EffectDispatchRequest`` and uses the same claim/authorize/apply coordinator
and ``McpEffectExecutor`` as generic MCP operations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.capabilities.mcp.effect_material import McpEffectMaterial
from agent_runtime.capabilities.mcp.target_ref import McpTargetRefCodec
from agent_runtime.effects.claims import EffectClaimStore
from agent_runtime.effects.contracts import EffectDispatchRequest
from agent_runtime.effects.dispatch import (
    EffectDispatchCoordinator,
    EffectDispatchResult,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.commit_engine import StageCommitRequest
from agent_runtime.surfaces_v2.ledger_models import EffectActor, EffectExecutorKind
from agent_runtime.surfaces_v2.mcp_connector import McpStageCommitConnector
from runtime_api.schemas import RunRecord

from runtime_worker.mcp_effect_executor import McpEffectExecutor

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]


@runtime_checkable
class StagedWriteEffectDispatcher(Protocol):
    """The only surface available to the staged-write worker handler."""

    async def dispatch(
        self,
        *,
        request: StageCommitRequest,
        proposal_ref: str,
        actor: EffectActor,
    ) -> EffectDispatchResult:
        """Dispatch one already-approved staged revision through A5."""


@runtime_checkable
class StagedWriteEffectDispatcherFactory(Protocol):
    """Bind a staged-write dispatcher to a verified runtime run."""

    def for_run(self, *, run: RunRecord) -> StagedWriteEffectDispatcher:
        """Return a dispatcher that cannot escape ``run``'s verified scope."""


class _StageCommitMaterialResolver:
    """Expose one handler-built immutable material object to the MCP executor."""

    def __init__(self, *, material: McpEffectMaterial) -> None:
        self._material = material

    async def resolve(self, request: EffectDispatchRequest) -> McpEffectMaterial | None:
        material = self._material
        if (
            request.target_ref != material.target_ref
            or request.target_digest != material.target_digest
            or request.proposal_ref != material.proposal_ref
            or request.proposal_content_ref != material.proposal_content_ref
            or request.proposal_digest != material.proposal_digest
        ):
            return None
        return material


@dataclass(frozen=True)
class RuntimeStagedWriteEffectDispatcher:
    """Adapt one legacy staged-write revision onto the shared effect core."""

    scope: EffectExecutionScope
    claims: EffectClaimStore
    connector: McpStageCommitConnector

    async def dispatch(
        self,
        *,
        request: StageCommitRequest,
        proposal_ref: str,
        actor: EffectActor,
    ) -> EffectDispatchResult:
        arguments = request.tool_arguments()
        proposal_digest = sha256_hex(canonical_json_bytes(arguments))
        target_ref = McpTargetRefCodec.format(
            capability=request.target_connector,
            op=request.target_op,
        )
        target_digest = sha256_hex(
            canonical_json_bytes(
                {"capability": request.target_connector, "op": request.target_op}
            )
        )
        dispatch_request = EffectDispatchRequest(
            stage_id=request.stage_id,
            revision=request.rev,
            idempotency_key=f"stage-commit:{request.commit_key()}",
            executor=EffectExecutorKind.MCP,
            target_ref=target_ref,
            target_digest=target_digest,
            proposal_ref=proposal_ref,
            proposal_content_ref=proposal_ref,
            proposal_digest=proposal_digest,
            actor=actor,
            decision_ledger_id=f"stage-decision-{request.decision_seq}",
        )
        material = McpEffectMaterial(
            target_connector=request.target_connector,
            target_op=request.target_op,
            arguments=arguments,
            target_ref=target_ref,
            target_digest=target_digest,
            proposal_ref=proposal_ref,
            proposal_content_ref=proposal_ref,
            proposal_digest=proposal_digest,
            row_key=request.row_key,
        )
        executor = McpEffectExecutor(
            scope=self.scope,
            connector=self.connector,
            material_resolver=_StageCommitMaterialResolver(material=material),
            enabled=True,
        )
        return await EffectDispatchCoordinator(claims=self.claims).dispatch(
            scope=self.scope,
            request=dispatch_request,
            executor=executor,
        )


@dataclass(frozen=True)
class RuntimeStagedWriteEffectDispatcherFactory:
    """Production construction of the stage adapter over the shared core."""

    claims: EffectClaimStore
    dependencies_factory: RuntimeDependenciesFactory
    timeout_seconds: float

    def for_run(self, *, run: RunRecord) -> RuntimeStagedWriteEffectDispatcher:
        scope = EffectExecutionScope(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            owner_ref=f"principal://users/{run.user_id}",
        )
        connector = McpStageCommitConnector(
            runtime_context=run.runtime_context,
            dependencies_factory=self.dependencies_factory,
            timeout_seconds=self.timeout_seconds,
        )
        return RuntimeStagedWriteEffectDispatcher(
            scope=scope,
            claims=self.claims,
            connector=connector,
        )


__all__ = (
    "RuntimeStagedWriteEffectDispatcher",
    "RuntimeStagedWriteEffectDispatcherFactory",
    "StagedWriteEffectDispatcher",
    "StagedWriteEffectDispatcherFactory",
)
