"""Artifact-revision staging for external draft sends.

This is the narrow convergence adapter for the historical ``DraftService``
send endpoint.  It is intentionally not another send executor: it creates a
standard A4 effect stage and relies on the existing A5 effect command/worker
path after a digest-pinned decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import RuntimeQueuePort
from agent_runtime.artifacts import ArtifactService
from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.backends.artifact_draft_effect import (
    ArtifactDraftRevisionResolver,
    ArtifactDraftRevisionForbidden,
    ArtifactDraftRevisionResolverPort,
    ArtifactDraftSendTarget,
    ArtifactDraftSendTargetStore,
    draft_send_operation_id,
    draft_send_stage_id,
)
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectStageScope,
    EffectStageState,
    ProposedEffect,
)
from agent_runtime.effects.errors import (
    EffectStageIdempotencyConflict,
    EffectStageNotFound,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import JsonObject
from agent_runtime.persistence.ports import DraftEffectSupersessionStorePort
from agent_runtime.persistence.records import DraftEffectSupersession
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
)
from runtime_adapters.artifact_references import ArtifactReferenceRepositoryPort


class ArtifactDraftSendForbidden(PermissionError):
    """The requested principal is not the immutable draft-stage owner.

    This is deliberately distinct from an unmigrated legacy row. The
    DraftService converts it to an opaque denial; callers must never interpret
    it as permission to create a mutable fallback stage.
    """


@runtime_checkable
class ArtifactDraftSendStagerPort(Protocol):
    """Stage a canonical Artifact draft when its v2 binding exists.

    ``None`` is the only migration signal: callers may retain the legacy
    staged-draft flow for an old ``runtime_drafts`` row that has not yet been
    imported by ``ArtifactDraftBackend``. Authorization failures raise
    :class:`ArtifactDraftSendForbidden` and must never fall back.
    """

    async def stage(
        self,
        *,
        org_id: str,
        user_id: str,
        run: object,
        draft_id: str,
        target_connector: str,
        target_op: str,
        target_metadata: JsonObject,
    ) -> EffectStageState | None:
        """Create/replay an Artifact stage; ``None`` only means unmigrated.

        Raises :class:`ArtifactDraftSendForbidden` for an unowned run scope.
        """


@dataclass(frozen=True)
class ArtifactDraftSendStager(ArtifactDraftSendStagerPort):
    """Bind an external send to a scoped immutable Artifact revision.

    This adapter deliberately owns neither an MCP client nor a generic effect
    coordinator.  Its only capabilities are resolving a revision, retaining a
    small non-body target descriptor, and staging through the existing ledger
    and command outbox ports.
    """

    artifacts: ArtifactService
    event_producer: RuntimeEventProducer
    queue: RuntimeQueuePort
    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort
    supersessions: DraftEffectSupersessionStorePort
    revisions: ArtifactDraftRevisionResolverPort | None = None

    async def stage(
        self,
        *,
        org_id: str,
        user_id: str,
        run: object,
        draft_id: str,
        target_connector: str,
        target_op: str,
        target_metadata: JsonObject,
    ) -> EffectStageState | None:
        run_id = _required_text(run, "run_id")
        conversation_id = _required_text(run, "conversation_id")
        run_org_id = _required_text(run, "org_id")
        run_user_id = _required_text(run, "user_id")
        if run_org_id != org_id or run_user_id != user_id:
            raise ArtifactDraftSendForbidden()

        revision_resolver = self.revisions or ArtifactDraftRevisionResolver(
            artifacts=self.artifacts
        )
        try:
            revision = await revision_resolver.resolve(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                run_id=run_id,
                draft_id=draft_id,
            )
        except ArtifactDraftRevisionForbidden as exc:
            raise ArtifactDraftSendForbidden() from exc
        if revision is None:
            return None

        # ``op`` chooses the target operation; it must not be carried onward as
        # connector metadata.  The remaining values are non-body metadata and
        # live behind the target's digest-pinned reference.
        target = ArtifactDraftSendTarget(
            connector=target_connector,
            op=target_op,
            title=revision.title,
            target_metadata=_without_operation_selector(target_metadata),
        )
        targets = ArtifactDraftSendTargetStore(
            blobs=self.blobs,
            references=self.references,
            org_id=org_id,
            user_id=user_id,
        )
        target_ref = await targets.persist(target=target)
        owner_ref = f"principal://users/{user_id}"
        scope = EffectStageScope(run_id=run_id, owner_ref=owner_ref)
        execution_scope = EffectExecutionScope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            owner_ref=owner_ref,
        )
        policy = EffectPolicySnapshot(
            # Legacy draft-send was always human-approved.  Until the regular
            # connector descriptor/policy snapshot is available here, preserve
            # that conservative posture rather than inventing auto-approval.
            snapshot_ref=f"policy://runs/{run_id}/draft-send/v1",
            descriptor_known=False,
            deployment_policy=EffectPolicy.ASK,
        )
        display_target = f"{target.op} on {target.connector}"
        stage_id = draft_send_stage_id(artifact=revision, target_digest=target.digest)
        supersession = DraftEffectSupersession(
            org_id=org_id,
            user_id=user_id,
            draft_id=draft_id,
            stage_id=stage_id,
            host_run_id=run_id,
            artifact_id=revision.artifact_id,
            proposal_digest=revision.content_digest,
            target_digest=target.digest,
        )
        effect_stager = EffectStager(
            ledger=RuntimeEffectLedger(
                event_producer=self.event_producer,
                run=run,  # type: ignore[arg-type]
                owner_ref=owner_ref,
            ),
            outbox=RuntimeEffectCommitOutbox(queue=self.queue, scope=execution_scope),
            stage_ids=_FixedStageId(stage_id),
        )
        try:
            existing = await effect_stager.get_state(scope=scope, stage_id=stage_id)
        except EffectStageNotFound:
            existing = None
        if existing is not None:
            if _matches_exact_artifact_stage(
                existing, revision, target_ref, target.digest
            ):
                # Complete a retry after a prior process wrote the stage but
                # died before recording its global draft supersession. This is
                # still safe: the exact stage facts were re-folded above.
                await self.supersessions.record_effect_supersession(supersession)
                return existing
            raise EffectStageIdempotencyConflict()
        # Persist the owner-scoped correlation before exposing ``effect.staged``.
        # If a crash follows, stale v1 approvals fail closed; retrying stages the
        # same deterministic revision rather than ever reviving mutable bytes.
        await self.supersessions.record_effect_supersession(supersession)
        try:
            stage = await effect_stager.stage(
                scope=scope,
                proposed_effect=ProposedEffect(
                    operation_id=draft_send_operation_id(
                        artifact=revision, target_digest=target.digest
                    ),
                    executor=EffectExecutorKind.MCP,
                    target=EffectTarget(
                        executor=EffectExecutorKind.MCP,
                        capability=target.connector,
                        op=target.op,
                        target_ref=target_ref,
                        display_label=display_target,
                    ),
                    target_digest=target.digest,
                    display_target=display_target,
                    proposal_kind=EffectProposalKind.ARTIFACT_REVISION,
                    proposal_content_ref=revision.content_ref,
                    proposal_digest=revision.content_digest,
                    proposal_media_type=revision.media_type,
                    effect_class=EffectClass.UNKNOWN,
                    policy_snapshot_ref=policy.snapshot_ref,
                ),
                policy_snapshot=policy,
                actor=EffectActorIdentity(
                    actor=EffectActor.USER,
                    principal_ref=owner_ref,
                ),
                idempotency_key=(
                    f"artifact-draft-send:{revision.content_digest}:{target.digest}"
                ),
            )
        except EffectStageIdempotencyConflict:
            # A concurrent identical request may have won after the lookup. A
            # re-read is a replay only when every immutable fact still matches.
            existing = await effect_stager.get_state(scope=scope, stage_id=stage_id)
            if not _matches_exact_artifact_stage(
                existing, revision, target_ref, target.digest
            ):
                raise
            return existing
        return stage


def _without_operation_selector(target_metadata: JsonObject) -> JsonObject:
    """Copy target metadata while keeping the target operation out of its body."""

    return {key: value for key, value in target_metadata.items() if key != "op"}


def _required_text(value: object, name: str) -> str:
    candidate = getattr(value, name, None)
    if not isinstance(candidate, str) or not candidate:
        raise ValueError(f"draft-send run is missing {name}")
    return candidate


def _matches_exact_artifact_stage(
    state: EffectStageState,
    revision: object,
    target_ref: str,
    target_digest: str,
) -> bool:
    """Whether a replayed stage names exactly the requested immutable facts."""

    return (
        state.target.target_ref == target_ref
        and state.target_digest == target_digest
        and state.current_revision.proposal_content_ref
        == getattr(revision, "content_ref", None)
        and state.current_revision.proposal_digest
        == getattr(revision, "content_digest", None)
    )


@dataclass(frozen=True)
class _FixedStageId:
    """A stable stage id makes duplicate HTTP retries semantic replays."""

    value: str

    def new_stage_id(self) -> str:
        return self.value


__all__ = [
    "ArtifactDraftSendForbidden",
    "ArtifactDraftSendStager",
    "ArtifactDraftSendStagerPort",
]
