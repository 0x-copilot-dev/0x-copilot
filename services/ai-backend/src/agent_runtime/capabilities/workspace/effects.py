"""C3 workspace adapter from A3 operations to A4 effect stages.

This module can stage and revise immutable workspace proposals, but it cannot
apply one. Host authority remains exclusively behind the C2
``WorkspaceEffectExecutor`` consumed by A5.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    _GatewayPresentationContext,
)
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
    WorkspaceOverlayVersionRef,
    mount_id_for_path,
    normalize_virtual_path,
)
from agent_runtime.capabilities.operations.stage_authority import (
    GatewayStageCapability,
)
from agent_runtime.capabilities.operations.errors import OperationStageCapabilityError
from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.workspace.overlay import (
    _WorkspaceOverlayMutationEngine,
    _WorkspaceOverlayPlan,
)
from agent_runtime.capabilities.workspace.ports import (
    WorkspaceBaseReadPort,
    WorkspaceOverlayStorePort,
)
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
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassBound,
    FilesystemBypassDecision,
    FilesystemBypassTarget,
)
from agent_runtime.surfaces_v2.entities import EffectTarget, OperationDisposition
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
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
# Bypass wording is deliberately NOT "written" / "saved". At this point the
# change is approved and queued for the C2 commit executor, which is still the
# only thing that touches the disk. Saying it is already on disk would be the
# same lie the empty-listing defect told, in the other direction.
_BYPASS_APPROVED_SUMMARY = (
    "Workspace change approved automatically (bypass) and queued to apply."
)
_NO_GRANT = "Workspace access is required; no host change was made."
_READ_ONLY = "This workspace grant is read-only; no host change was made."
_DELETE_FORBIDDEN = (
    "This workspace grant does not allow delete or move; no host change was made."
)

_LOGGER = logging.getLogger(__name__)


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

    stager: EffectStager
    scope: EffectStageScope
    actor: EffectActorIdentity
    proposals: WorkspaceProposalStorePort
    grants: tuple[WorkspaceGrantBinding, ...]
    #: The run's sealed filesystem-bypass decision (PRD-FS-10 §4.3), resolved
    #: once at run-create and read from the persisted ``runtime_context``. The
    #: default is the fail-closed one, so every existing composition — and any
    #: lane that cannot supply it — keeps asking exactly as before.
    bypass: FilesystemBypassDecision = MANUAL_FILESYSTEM_BYPASS

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
        """Deny the operation and record why, in that order of authority.

        The returned :class:`GateResolution` is the decision; the ledger event is
        evidence describing it. Emission is therefore best-effort — the same
        shape ``WorkLedgerEmitter.on_tool_result`` already uses — because a
        failure to *describe* a denial must never be able to change it.

        This is not hypothetical caution. Until PRD-01 the emit raised
        ``ValueError`` on every call (``gate.opened.v2`` was absent from
        ``RuntimeApiEventType``), so an unguarded ``await`` here propagated out of
        the gate and failed the very operation it was denying. Losing evidence
        degrades observability; losing the denial would be a security defect.
        """

        try:
            await _GatewayPresentationContext.require().ledger_emitter.emit(
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
        except Exception:  # noqa: BLE001 — evidence must never alter the decision
            _LOGGER.warning(
                "workspace.gate_emit_failed",
                extra={
                    "metadata": {
                        "gate_id": f"workspace:{request.operation_id}",
                        "operation_id": request.operation_id,
                        "gate_kind": kind.value,
                    }
                },
                exc_info=True,
            )
        return GateResolution(
            allowed=False,
            gate_kind=kind,
            safe_summary=summary,
        )


class WorkspaceOperationAdapter(OperationAdapter):
    """The sole model-mutation gateway for one workspace run.

    This is the only public constructor that owns the private raw overlay
    engine.  It is retained exclusively by worker-owned operation composition
    and invoked by :class:`OperationGateway`; model backends receive only the
    narrow ``WorkspaceOperationPort`` and never this adapter or raw engine.
    """

    def __init__(
        self,
        *,
        services: WorkspaceGatewayServices,
        run_id: str,
        base_read: WorkspaceBaseReadPort,
        overlay_store: WorkspaceOverlayStorePort,
        blob_store: ArtifactBlobStorePort,
    ) -> None:
        self._services = services
        self._mutations = _WorkspaceOverlayMutationEngine(
            run_id=run_id,
            base_read=base_read,
            overlay_store=overlay_store,
            blob_store=blob_store,
        )

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        del request
        raise RuntimeError("workspace reads use the merged read backend")

    async def build_proposal(self, request: OperationRequest) -> GatewayProposedEffect:
        """Refuse the legacy adapter seam; use gateway-issued authority instead."""

        del request
        raise OperationStageCapabilityError()

    async def build_proposal_with_capability(
        self,
        request: OperationRequest,
        capability: GatewayStageCapability,
    ) -> GatewayProposedEffect:
        if not isinstance(capability, GatewayStageCapability):
            raise OperationStageCapabilityError()
        capability._consume_for(request)
        arguments = _request_arguments(request)
        paths = _operation_paths(request.op, arguments)
        # Runtime replay keeps the operation id stable. Repair the binding
        # before compiling a second plan, so a replay neither duplicates nor
        # cancels the already-visible overlay.
        recovered = await self._recover_unbound_projection(
            request=request,
            paths=paths,
        )
        if recovered is not None:
            return recovered
        plan = await self._mutations._plan(op=request.op, arguments=arguments)

        if not _plan_entries(plan, paths):
            prior = next(
                (
                    plan.baseline.entry_at(path)
                    for path in paths
                    if plan.baseline.entry_at(path) is not None
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
            try:
                await self._mutations._project(plan)
            except Exception:
                # The pre-existing stage is already cancelled, so a failed
                # projection leaves the prior model-visible overlay intact and
                # cannot be approved or executed.
                raise
            return GatewayProposedEffect(
                stage_id=state.stage_id,
                proposal_ref=state.current_revision.proposal_ref,
                safe_summary=(
                    "The pending workspace create was cancelled; "
                    "the host was not modified."
                ),
                activity_ref=state.current_revision.proposal_ref,
            )

        entries = _plan_entries(plan, paths)
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
            projected = await self._mutations._project(
                plan,
                stage_id=state.stage_id,
                stage_revision=revision.revision,
            )
        except Exception:
            # Projection is intentionally after durable material+stage writes.
            # A failed optimistic projection therefore never changes the
            # model-visible overlay; cancel the just-created/revised stage so
            # it cannot be approved while not represented in the workspace.
            await self._cancel_safely(
                request=request,
                state=state,
                reason="workspace-project-failed",
            )
            raise
        state = await self._services.stager.bind_projection(
            scope=self._services.scope,
            stage_id=state.stage_id,
            revision=revision.revision,
            projection_ref=WorkspaceOverlayVersionRef.format(
                run_id=request.run_id,
                version=projected.manifest.version,
            ),
            proposal_digest=revision.proposal_digest,
            target_digest=state.target_digest,
            actor=self._services.actor,
            idempotency_key=(
                f"workspace-projection-bind:{request.operation_id}:{revision.revision}"
            ),
        )
        # Strictly after the projection is bound: A4 refuses to approve a stage
        # whose exact revision has no projection row, so ordering this earlier
        # would produce a bypass that reliably failed and silently fell back to
        # asking.
        approved = await self._auto_approve_bypassed(
            request=request,
            state=state,
            effect_class=effect_class,
            grant=grant,
        )
        return GatewayProposedEffect(
            stage_id=state.stage_id,
            proposal_ref=revision.proposal_ref,
            safe_summary=(_BYPASS_APPROVED_SUMMARY if approved else _STAGED_SUMMARY),
            activity_ref=revision.proposal_ref,
            artifact_source_ref=(
                entries[0].content_ref
                if len(entries) == 1 and entries[0].content_ref is not None
                else None
            ),
        )

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
                projection_required=True,
            ),
            policy_snapshot=snapshot,
            actor=self._services.actor,
            idempotency_key=f"workspace-stage:{request.operation_id}",
        )

    async def _recover_unbound_projection(
        self,
        *,
        request: OperationRequest,
        paths: tuple[str, ...],
    ) -> GatewayProposedEffect | None:
        """Finish a previously projected workspace stage on an idempotent retry."""

        manifest = await self._mutations._manifest()
        entries = tuple(manifest.entry_at(path) for path in paths)
        if not entries or any(entry is None for entry in entries):
            return None
        present = tuple(entry for entry in entries if entry is not None)
        stage_ids = {entry.stage_id for entry in present}
        revisions = {entry.stage_revision for entry in present}
        if len(stage_ids) != 1 or len(revisions) != 1:
            return None
        stage_id = next(iter(stage_ids))
        revision = next(iter(revisions))
        if stage_id is None or revision is None:
            return None
        state = await self._services.stager.get_state(
            scope=self._services.scope,
            stage_id=stage_id,
        )
        if (
            state.operation_id != request.operation_id
            or state.target.op != request.op
            or not state.projection_required
            or state.approval_ready
            or state.current_revision.revision != revision
            or state.status
            in {
                EffectStageStatus.APPROVED,
                EffectStageStatus.REJECTED,
                EffectStageStatus.CANCELLED,
            }
        ):
            return None
        current = state.current_revision
        bound = await self._services.stager.bind_projection(
            scope=self._services.scope,
            stage_id=state.stage_id,
            revision=revision,
            projection_ref=WorkspaceOverlayVersionRef.format(
                run_id=request.run_id,
                version=manifest.version,
            ),
            proposal_digest=current.proposal_digest,
            target_digest=state.target_digest,
            actor=self._services.actor,
            idempotency_key=(
                f"workspace-projection-bind:{request.operation_id}:{revision}"
            ),
        )
        return GatewayProposedEffect(
            stage_id=bound.stage_id,
            proposal_ref=bound.current_revision.proposal_ref,
            safe_summary=_STAGED_SUMMARY,
            activity_ref=bound.current_revision.proposal_ref,
            artifact_source_ref=(
                present[0].content_ref
                if len(present) == 1 and present[0].content_ref is not None
                else None
            ),
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

    def _bypass_target(
        self,
        *,
        effect_class: EffectClass,
        grant: WorkspaceGrantBinding,
    ) -> FilesystemBypassTarget:
        """Restate this mutation as the three facts the bypass bound needs.

        Built from the SAME grant binding the gate resolved, so the bound is a
        second, independent statement of the rule rather than a paraphrase of
        the gate's control flow. Today the gate already refuses an ungranted or
        read-only target, which makes two of these facts true whenever we get
        here; that redundancy is the point. If the gate is ever loosened, the
        bound still holds, and the test that pins it fails on the gate change
        instead of on a production incident.
        """

        return FilesystemBypassTarget(
            granted=grant.status == "active",
            writable=grant.mode != "read_only",
            reversible=effect_class is EffectClass.EXTERNAL_REVERSIBLE,
        )

    def _bypass_permits(
        self,
        *,
        effect_class: EffectClass,
        grant: WorkspaceGrantBinding,
    ) -> bool:
        """Whether this run's bypass decision covers THIS mutation."""

        return FilesystemBypassBound.permits(
            self._services.bypass,
            self._bypass_target(effect_class=effect_class, grant=grant),
        )

    def _policy_snapshot(
        self,
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
        # Bypass is the SECOND way this stage can carry ``allow_always``, and it
        # rides the identical A4 lane as the first (an explicit user AUTO write
        # policy): same snapshot field, same eligibility test in
        # ``EffectStagePolicyResolver``, same recorded reason. It adds no new
        # authority — a BLOCK / REQUIRE / ASK policy still wins the
        # most-restrictive fold, because ``allow_always`` only ever competes,
        # it never overrides.
        bypassed = self._bypass_permits(effect_class=effect_class, grant=grant)
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
            # Bypass suspends the ASK *posture* for this run. Mapping it onto
            # the user axis (rather than leaving ASK and relying on
            # allow_always alone) is required: the resolver adds an explicit
            # ``external_reversible_default`` ASK candidate for a non-eligible
            # proposal, and the fold takes the most restrictive candidate.
            user_policy=(
                EffectPolicy.AUTO
                if bypassed and mode is ToolUsePolicyMode.ASK
                else mapped
            ),
            allow_always=(
                bypassed
                or (
                    mode is ToolUsePolicyMode.AUTO
                    and effect_class is EffectClass.EXTERNAL_REVERSIBLE
                )
            ),
        )

    async def _auto_approve_bypassed(
        self,
        *,
        request: OperationRequest,
        state: EffectStageState,
        effect_class: EffectClass,
        grant: WorkspaceGrantBinding,
    ) -> bool:
        """Record the POLICY approval that removes the pause. Returns success.

        This is the whole of "bypass". It does NOT write anything, does not
        touch the host, and does not shortcut a step: it appends the SAME
        ``effect.decision`` ledger row a human click appends, authored by
        ``EffectActor.POLICY``, and lets ``EffectStager.decide`` enqueue the
        SAME C2 commit command. One lane, one ledger, one executor — the only
        difference is who signed.

        A4 re-checks the decision independently: ``EffectStager.decide``
        refuses a POLICY actor unless the folded stage policy is ``AUTO``. So a
        bug in this class's bound cannot approve a held stage; the worst it can
        do is ask when it should not have.

        Failure degrades toward asking. If the decision cannot be recorded the
        stage stays approvable by a human, which is the safe direction, so this
        returns ``False`` and the caller reports the ordinary staged summary.
        """

        if not self._bypass_permits(effect_class=effect_class, grant=grant):
            return False
        revision = state.current_revision
        try:
            await self._services.stager.decide(
                scope=self._services.scope,
                stage_id=state.stage_id,
                revision=revision.revision,
                decision=EffectDecisionKind.APPROVE,
                proposal_digest=revision.proposal_digest,
                target_digest=state.target_digest,
                actor=EffectActorIdentity(
                    actor=EffectActor.POLICY,
                    principal_ref=self._services.scope.owner_ref,
                ),
                idempotency_key=(
                    f"workspace-bypass-approve:{request.operation_id}"
                    f":{revision.revision}"
                ),
            )
        except Exception:
            _LOGGER.warning(
                "workspace.bypass_auto_approve_failed",
                extra={
                    "metadata": {
                        "operation_id": request.operation_id,
                        "stage_id": state.stage_id,
                        "revision": revision.revision,
                    }
                },
                exc_info=True,
            )
            return False
        return True


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


def _plan_entries(
    plan: _WorkspaceOverlayPlan,
    paths: tuple[str, ...],
) -> tuple[OverlayEntry, ...]:
    """Read only the request-local candidate, never a visible overlay write."""

    return tuple(
        entry for path in paths if (entry := plan.candidate.entry_at(path)) is not None
    )


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
