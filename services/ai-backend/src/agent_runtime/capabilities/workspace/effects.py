"""C3 workspace adapter from A3 operations to A4 effect stages.

This module can stage and revise immutable workspace proposals, but it cannot
apply one. Host authority remains exclusively behind the C2
``WorkspaceEffectExecutor`` consumed by A5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationAdapter,
    OperationClassification,
    OperationDescriptor,
    OperationRawResult,
    OperationRequest,
    ProposedEffect as GatewayProposedEffect,
)
from agent_runtime.capabilities.workspace.contracts import (
    OverlayEntry,
    WorkspaceMutationResult,
    mount_id_for_path,
    normalize_virtual_path,
)
from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.overlay import WorkspaceOverlayService
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectRevisionProposal,
    EffectStageState,
    EffectStageStatus,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.entities import EffectTarget, OperationDisposition
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
    GateKind,
    LedgerEventType,
    OperationOutcome,
)
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
)

_WORKSPACE_MEDIA_TYPE = "application/vnd.0xcopilot.workspace-change-set+json"
_STAGED_SUMMARY = "Workspace change staged for review; the host was not modified."
_NO_GRANT = "Workspace access is required; no host change was made."
_READ_ONLY = "This workspace grant is read-only; no host change was made."
_DELETE_FORBIDDEN = (
    "This workspace grant does not allow delete or move; no host change was made."
)


class WorkspaceGrantBinding(RuntimeContract):
    """Path-free grant facts supplied by the trusted desktop host bridge."""

    mount_name: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\]+$")
    grant_id: str = Field(min_length=1, max_length=255)
    mount_label: str = Field(min_length=1, max_length=255)
    mode: Literal["read_only", "read_write_no_delete", "read_write"]
    status: Literal["active", "revoked"] = "active"


class WorkspaceStoredProposal(RuntimeContract):
    """Immutable refs and digests produced before an A4 stage is appended."""

    proposal_content_ref: str
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_ref: str
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    precondition_ref: str
    precondition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_target: str = Field(min_length=1, max_length=512)


@runtime_checkable
class WorkspaceProposalStorePort(Protocol):
    """Persist exact C2 proposal, target, and precondition material."""

    async def persist(
        self,
        *,
        operation_id: str,
        grant: WorkspaceGrantBinding,
        entries: tuple[OverlayEntry, ...],
    ) -> WorkspaceStoredProposal:
        """Return immutable refs only after all material is durable."""


@dataclass(frozen=True)
class WorkspaceGatewayServices:
    """Per-run dependencies for the enforced workspace adapter."""

    merged: MergedWorkspaceBackend
    overlay: WorkspaceOverlayService
    stager: EffectStager
    scope: EffectStageScope
    actor: EffectActorIdentity
    proposals: WorkspaceProposalStorePort
    grants: tuple[WorkspaceGrantBinding, ...]

    def grant_for_path(self, virtual_path: str) -> WorkspaceGrantBinding | None:
        mount = mount_id_for_path(virtual_path)
        return next(
            (grant for grant in self.grants if grant.mount_name == mount),
            None,
        )


class WorkspaceGrantGate:
    """Resolve current grant mode before an overlay mutation can be built."""

    def __init__(self, *, grants: tuple[WorkspaceGrantBinding, ...]) -> None:
        self._grants = grants

    async def resolve(
        self,
        *,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        classification: OperationClassification,
    ) -> GateResolution:
        del descriptor, classification
        arguments = _request_arguments(request)
        paths = _operation_paths(request.op, arguments)
        bindings = tuple(self._grant_for_path(path) for path in paths)
        if not bindings or any(
            binding is None or binding.status != "active" for binding in bindings
        ):
            return await self._blocked(
                request=request,
                kind=GateKind.GRANT,
                reason="workspace_grant_missing_or_revoked",
                summary=_NO_GRANT,
            )
        active = tuple(binding for binding in bindings if binding is not None)
        if len({binding.grant_id for binding in active}) != 1:
            return await self._blocked(
                request=request,
                kind=GateKind.CAPABILITY,
                reason="workspace_cross_grant_mutation_forbidden",
                summary="Workspace changes must stay within one active grant.",
            )
        if any(binding.mode == "read_only" for binding in active):
            return await self._blocked(
                request=request,
                kind=GateKind.CAPABILITY,
                reason="workspace_grant_read_only",
                summary=_READ_ONLY,
            )
        if request.op in {"delete", "move"} and any(
            binding.mode != "read_write" for binding in active
        ):
            return await self._blocked(
                request=request,
                kind=GateKind.CAPABILITY,
                reason="workspace_delete_capability_required",
                summary=_DELETE_FORBIDDEN,
            )
        return GateResolution(allowed=True)

    def _grant_for_path(self, path: str) -> WorkspaceGrantBinding | None:
        mount = mount_id_for_path(path)
        return next(
            (grant for grant in self._grants if grant.mount_name == mount),
            None,
        )

    @staticmethod
    async def _blocked(
        *,
        request: OperationRequest,
        kind: GateKind,
        reason: str,
        summary: str,
    ) -> GateResolution:
        await OperationContext.require().ledger_emitter.emit(
            LedgerEventType.GATE_OPENED_V2,
            {
                "v": 1,
                "gate_id": f"workspace:{request.operation_id}",
                "operation_id": request.operation_id,
                "gate_kind": kind.value,
                "capability": "workspace",
                "reason": reason,
            },
            summary,
        )
        return GateResolution(
            allowed=False,
            gate_kind=kind,
            safe_summary=summary,
        )


class WorkspaceOperationAdapter(OperationAdapter):
    """Build one exact overlay mutation and bind it to an A4 stage."""

    def __init__(self, *, services: WorkspaceGatewayServices) -> None:
        self._services = services

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        del request
        raise RuntimeError("workspace reads use the merged read backend")

    async def build_proposal(self, request: OperationRequest) -> GatewayProposedEffect:
        arguments = _request_arguments(request)
        before = await self._services.overlay.manifest()
        mutation = await self._mutate(request.op, arguments)
        paths = _bound_paths(request.op, arguments, mutation)

        if mutation.entry is None:
            prior = next(
                (
                    before.entry_at(path)
                    for path in paths
                    if before.entry_at(path) is not None
                ),
                None,
            )
            if prior is None or prior.stage_id is None:
                raise RuntimeError("workspace mutation produced no external effect")
            state = await self._services.stager.get_state(
                scope=self._services.scope,
                stage_id=prior.stage_id,
            )
            state = await self._services.stager.decide(
                scope=self._services.scope,
                stage_id=state.stage_id,
                revision=state.current_revision.revision,
                decision=EffectDecisionKind.CANCEL,
                proposal_digest=state.current_revision.proposal_digest,
                target_digest=state.target_digest,
                actor=self._services.actor,
                idempotency_key=f"workspace-cancel:{request.operation_id}",
            )
            return GatewayProposedEffect(
                stage_id=state.stage_id,
                proposal_ref=state.current_revision.proposal_ref,
                safe_summary=(
                    "The pending workspace create was cancelled; "
                    "the host was not modified."
                ),
                activity_ref=state.current_revision.proposal_ref,
            )

        entries = tuple(
            entry
            for path in paths
            if (entry := mutation.manifest.entry_at(path)) is not None
        )
        if not entries:
            raise RuntimeError("workspace proposal has no overlay entries")
        grant = self._services.grant_for_path(entries[0].virtual_path)
        if grant is None or grant.status != "active":
            raise RuntimeError("workspace grant changed while staging")
        stored = await self._services.proposals.persist(
            operation_id=request.operation_id,
            grant=grant,
            entries=entries,
        )
        effect_class = (
            EffectClass.EXTERNAL_DESTRUCTIVE
            if request.op == "delete"
            else EffectClass.EXTERNAL_REVERSIBLE
        )
        snapshot = self._policy_snapshot(
            request=request,
            effect_class=effect_class,
            grant=grant,
        )
        existing_stage_id = next(
            (entry.stage_id for entry in entries if entry.stage_id is not None),
            None,
        )
        state = await self._stage_or_revise(
            request=request,
            stored=stored,
            effect_class=effect_class,
            snapshot=snapshot,
            existing_stage_id=existing_stage_id,
        )
        revision = state.current_revision
        try:
            await self._services.overlay.bind_stage(
                virtual_paths=paths,
                stage_id=state.stage_id,
                stage_revision=revision.revision,
                expected_manifest_version=mutation.manifest.version,
            )
        except Exception:
            # A stage whose overlay binding lost its optimistic-CAS race must
            # never remain approvable. Best-effort cancellation makes the
            # durable stage visibly inert; the original conflict still fails
            # the operation and a fresh request must re-read current content.
            await self._cancel_safely(
                request=request,
                state=state,
                reason="workspace-bind-race",
            )
            raise
        return GatewayProposedEffect(
            stage_id=state.stage_id,
            proposal_ref=revision.proposal_ref,
            safe_summary=_STAGED_SUMMARY,
            activity_ref=revision.proposal_ref,
            artifact_source_ref=(
                entries[0].content_ref
                if len(entries) == 1 and entries[0].content_ref is not None
                else None
            ),
        )

    async def _mutate(
        self, op: str, arguments: dict[str, object]
    ) -> WorkspaceMutationResult:
        author = "agent"
        if op in {"create", "replace", "write"}:
            path = _required_text(arguments, "virtual_path")
            content = _required_text(arguments, "content")
            if op == "create":
                return await self._services.overlay.propose_create(
                    path, content, author=author
                )
            return await self._services.overlay.propose_replace(
                path, content, author=author
            )
        if op == "edit":
            return await self._services.overlay.propose_edit(
                _required_text(arguments, "virtual_path"),
                _required_text(arguments, "old_string"),
                _required_text(arguments, "new_string", allow_empty=True),
                replace_all=bool(arguments.get("replace_all", False)),
                author=author,
            )
        if op == "delete":
            return await self._services.overlay.propose_delete(
                _required_text(arguments, "virtual_path"), author=author
            )
        if op == "move":
            return await self._services.overlay.propose_move(
                _required_text(arguments, "source_virtual_path"),
                _required_text(arguments, "destination_virtual_path"),
                author=author,
            )
        if op == "mkdir":
            return await self._services.overlay.propose_mkdir(
                _required_text(arguments, "virtual_path"), author=author
            )
        raise RuntimeError("workspace operation is not stageable")

    async def _stage_or_revise(
        self,
        *,
        request: OperationRequest,
        stored: WorkspaceStoredProposal,
        effect_class: EffectClass,
        snapshot: EffectPolicySnapshot,
        existing_stage_id: str | None,
    ) -> EffectStageState:
        if existing_stage_id is not None:
            current = await self._services.stager.get_state(
                scope=self._services.scope,
                stage_id=existing_stage_id,
            )
            if (
                current.status is not EffectStageStatus.CANCELLED
                and current.effect_class is effect_class
                and current.target.target_ref == stored.target_ref
                and current.target_digest == stored.target_digest
                and current.current_revision.precondition_ref == stored.precondition_ref
                and current.current_revision.precondition_digest
                == stored.precondition_digest
            ):
                return await self._services.stager.revise(
                    scope=self._services.scope,
                    stage_id=current.stage_id,
                    expected_revision=current.current_revision.revision,
                    proposal=EffectRevisionProposal(
                        proposal_kind=EffectProposalKind.WORKSPACE_CHANGE_SET,
                        proposal_content_ref=stored.proposal_content_ref,
                        proposal_digest=stored.proposal_digest,
                        proposal_media_type=_WORKSPACE_MEDIA_TYPE,
                        target_ref=stored.target_ref,
                        target_digest=stored.target_digest,
                        display_target=stored.display_target,
                        precondition_ref=stored.precondition_ref,
                        precondition_digest=stored.precondition_digest,
                    ),
                    actor=self._services.actor,
                    idempotency_key=f"workspace-revise:{request.operation_id}",
                )
            if current.status is not EffectStageStatus.CANCELLED:
                await self._services.stager.decide(
                    scope=self._services.scope,
                    stage_id=current.stage_id,
                    revision=current.current_revision.revision,
                    decision=EffectDecisionKind.CANCEL,
                    proposal_digest=current.current_revision.proposal_digest,
                    target_digest=current.target_digest,
                    actor=self._services.actor,
                    idempotency_key=f"workspace-supersede:{request.operation_id}",
                )

        return await self._services.stager.stage(
            scope=self._services.scope,
            proposed_effect=ProposedEffect(
                operation_id=request.operation_id,
                executor=EffectExecutorKind.WORKSPACE,
                target=EffectTarget(
                    executor=EffectExecutorKind.WORKSPACE,
                    capability="workspace",
                    op=request.op,
                    target_ref=stored.target_ref,
                    precondition_ref=stored.precondition_ref,
                    display_label=stored.display_target,
                ),
                target_digest=stored.target_digest,
                display_target=stored.display_target,
                proposal_kind=EffectProposalKind.WORKSPACE_CHANGE_SET,
                proposal_content_ref=stored.proposal_content_ref,
                proposal_digest=stored.proposal_digest,
                proposal_media_type=_WORKSPACE_MEDIA_TYPE,
                precondition_ref=stored.precondition_ref,
                precondition_digest=stored.precondition_digest,
                effect_class=effect_class,
                policy_snapshot_ref=snapshot.snapshot_ref,
            ),
            policy_snapshot=snapshot,
            actor=self._services.actor,
            idempotency_key=f"workspace-stage:{request.operation_id}",
        )

    async def _cancel_safely(
        self,
        *,
        request: OperationRequest,
        state: EffectStageState,
        reason: str,
    ) -> None:
        if state.status is EffectStageStatus.CANCELLED:
            return
        try:
            await self._services.stager.decide(
                scope=self._services.scope,
                stage_id=state.stage_id,
                revision=state.current_revision.revision,
                decision=EffectDecisionKind.CANCEL,
                proposal_digest=state.current_revision.proposal_digest,
                target_digest=state.target_digest,
                actor=self._services.actor,
                idempotency_key=f"{reason}:{request.operation_id}",
            )
        except Exception:
            # The caller still gets a failed operation. A competing revision or
            # decision remains protected by A5's exact decision/revision fold.
            return

    @staticmethod
    def _policy_snapshot(
        *,
        request: OperationRequest,
        effect_class: EffectClass,
        grant: WorkspaceGrantBinding,
    ) -> EffectPolicySnapshot:
        policy = OperationContext.require().policy_snapshot
        kind = (
            ToolUsePolicyKind.DESTRUCTIVE
            if effect_class is EffectClass.EXTERNAL_DESTRUCTIVE
            else ToolUsePolicyKind.WRITE
        )
        mode = policy.mode_for_kind(kind)
        mapped = {
            ToolUsePolicyMode.AUTO: EffectPolicy.AUTO,
            ToolUsePolicyMode.ASK: EffectPolicy.ASK,
            ToolUsePolicyMode.REQUIRE: EffectPolicy.REQUIRE,
            ToolUsePolicyMode.BLOCK: EffectPolicy.BLOCK,
        }[mode]
        return EffectPolicySnapshot(
            snapshot_ref=(
                f"policy://runs/{request.run_id}/workspace/{request.operation_id}"
            ),
            descriptor_known=True,
            # The grant gate already rejected read-only and missing authority.
            # A writable grant is a capability boundary, not an approval
            # posture: leaving this unset allows only an explicit reversible
            # user AUTO policy to take A4's tightly-scoped allow-always lane.
            grant_policy=None,
            user_policy=mapped,
            allow_always=(
                mode is ToolUsePolicyMode.AUTO
                and effect_class is EffectClass.EXTERNAL_REVERSIBLE
            ),
        )


def _request_arguments(request: OperationRequest) -> dict[str, object]:
    stored = OperationContext.require().arguments.get(request.canonical_args_ref)
    if stored is None or stored[0] != request.args_digest:
        raise RuntimeError("workspace operation arguments are unavailable")
    decoded = json.loads(stored[1])
    if not isinstance(decoded, dict):
        raise RuntimeError("workspace operation arguments are invalid")
    return {str(key): value for key, value in decoded.items()}


def _operation_paths(op: str, arguments: dict[str, object]) -> tuple[str, ...]:
    if op == "move":
        return (
            normalize_virtual_path(_required_text(arguments, "source_virtual_path")),
            normalize_virtual_path(
                _required_text(arguments, "destination_virtual_path")
            ),
        )
    return (normalize_virtual_path(_required_text(arguments, "virtual_path")),)


def _bound_paths(
    op: str,
    arguments: dict[str, object],
    mutation: WorkspaceMutationResult,
) -> tuple[str, ...]:
    paths = _operation_paths(op, arguments)
    if mutation.entry is None:
        return paths
    return paths


def _required_text(
    arguments: dict[str, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise RuntimeError(f"workspace argument {key} is invalid")
    return value


def staged_disposition_message(disposition: OperationDisposition) -> str:
    """Return one honest Deep Agents result string for a gateway disposition."""

    if disposition.outcome is OperationOutcome.STAGED:
        return _STAGED_SUMMARY
    return disposition.agent_summary


__all__ = (
    "WorkspaceGatewayServices",
    "WorkspaceGrantBinding",
    "WorkspaceGrantGate",
    "WorkspaceOperationAdapter",
    "WorkspaceProposalStorePort",
    "WorkspaceStoredProposal",
    "staged_disposition_message",
)
