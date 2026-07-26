"""Worker-owned A4 proposal adapter for the D2 row-set compatibility tool.

The model-visible tool supplies only :class:`RowSetEffectProposalPort`.  This
composition adapter binds that narrow seam to the already-constructed A4
``EffectStager`` and the durable canonical-argument store used by the
Operation Gateway.  It has no executor registry, connector client, transport,
or apply method.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.capabilities.actions.classifier import ACTION_CLASSIFIER
from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
    ClassificationBasis,
    ClassifiedAction,
)
from agent_runtime.capabilities.actions.policy import (
    ConnectorWritePolicyOverrides,
    EffectiveActionPolicyResolver,
)
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.mcp.operation_adapter import (
    McpOperationArgumentStorePort,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
    RowSetEffectProposalPort,
    RowSetProposalReceipt,
    reviewed_rowset_target,
)
from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectPolicySnapshot,
    EffectStageScope,
    ProposedEffect,
)
from agent_runtime.effects.errors import EffectStageError
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
)
from agent_runtime.surfaces_v2.rowset import (
    RowsetValidationError,
    RowsetValidator,
)
from agent_runtime.surfaces_v2.staging import StagedWriteError
from agent_runtime.capabilities.tools.permissions import ToolUsePolicyMode

_ROW_SET_MEDIA_TYPE = "application/vnd.0xcopilot.row-set+json"


@dataclass(frozen=True)
class RuntimeRowSetEffectProposalPort(RowSetEffectProposalPort):
    """Persist and stage one exact generic row-set proposal.

    Canonical proposal bytes are read from the bound Operation Gateway
    argument cache, verified against the typed proposal, persisted before the
    stage event, then referenced by the A4 stage.  Approval and execution
    remain separate A4/A5 concerns.
    """

    stager: EffectStager
    scope: EffectStageScope
    actor: EffectActorIdentity
    argument_store: McpOperationArgumentStorePort
    connector_overrides: ConnectorWritePolicyOverrides = field(
        default_factory=ConnectorWritePolicyOverrides
    )

    async def stage_row_set(
        self,
        *,
        proposal: RowSetEffectProposal,
        operation_id: str | None,
    ) -> RowSetProposalReceipt:
        if operation_id is None:
            raise StagedWriteError("The row-set proposal is missing its operation id.")
        target = reviewed_rowset_target(
            target_connector=proposal.target_connector,
            target_op=proposal.target_op,
        )
        if target is None:
            raise StagedWriteError(
                "This row-set target is not enabled for staged execution."
            )
        try:
            RowsetValidator.validate(
                rows=proposal.rows,
                agent_holds=proposal.agent_holds,
            )
        except RowsetValidationError as exc:
            raise StagedWriteError(exc.safe_message) from exc

        context = OperationContext.active()
        if context is None:
            raise StagedWriteError("The row-set proposal has no bound run context.")
        if (
            context.identity.run_id != self.scope.run_id
            or self.scope.owner_ref != f"principal://users/{context.identity.user_id}"
        ):
            raise StagedWriteError("The row-set proposal scope is invalid.")

        content_ref = OperationArgsRefCodec.format(operation_id)
        canonical_bytes = canonical_json_bytes(proposal.model_dump(mode="json"))
        proposal_digest = sha256_hex(canonical_bytes)
        stored = context.arguments.get(content_ref)
        if stored != (proposal_digest, canonical_bytes):
            raise StagedWriteError("The row-set proposal content changed.")
        try:
            await self.argument_store.persist(
                ref=content_ref,
                digest=proposal_digest,
                canonical_bytes=canonical_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - no stage may outlive its body.
            raise StagedWriteError(
                "The row-set proposal could not be stored safely."
            ) from exc

        classified = ACTION_CLASSIFIER.classify(
            server=target[0],
            tool=target[1],
            annotations=McpToolAnnotationsRegistry.get(
                target[0],
                target[1],
            ),
        )
        effect_class = _effect_class(classified)
        effective_policy = EffectiveActionPolicyResolver(
            snapshot=context.policy_snapshot,
            overrides=self.connector_overrides,
        ).resolve(classified)
        policy = _effect_policy(effective_policy.mode)
        target_digest = sha256_hex(
            canonical_json_bytes(
                {
                    "capability": classified.connector,
                    "op": classified.op,
                }
            )
        )
        display_target = f"{classified.op} on {classified.connector}"
        snapshot = EffectPolicySnapshot(
            snapshot_ref=(f"policy://runs/{self.scope.run_id}/rowsets/{operation_id}"),
            descriptor_known=classified.basis is ClassificationBasis.CATALOG,
            user_policy=policy,
            allow_always=effective_policy.bypass,
        )
        try:
            state = await self.stager.stage(
                scope=self.scope,
                proposed_effect=ProposedEffect(
                    operation_id=operation_id,
                    executor=EffectExecutorKind.BUILTIN,
                    target=EffectTarget(
                        executor=EffectExecutorKind.BUILTIN,
                        capability=classified.connector,
                        op=classified.op,
                        target_ref=f"rowset-target://{target_digest}",
                        display_label=display_target,
                    ),
                    target_digest=target_digest,
                    display_target=display_target,
                    proposal_kind=EffectProposalKind.ROW_SET,
                    proposal_content_ref=content_ref,
                    proposal_digest=proposal_digest,
                    proposal_media_type=_ROW_SET_MEDIA_TYPE,
                    effect_class=effect_class,
                    policy_snapshot_ref=snapshot.snapshot_ref,
                    agent_hold=bool(proposal.agent_holds),
                ),
                policy_snapshot=snapshot,
                actor=self.actor,
                idempotency_key=f"rowset-stage:{operation_id}",
            )
        except EffectStageError as exc:
            raise StagedWriteError(exc.safe_message) from exc

        revision = state.current_revision
        return RowSetProposalReceipt(
            stage_id=state.stage_id,
            proposal_ref=revision.proposal_ref,
            # Until the presentation projector assigns a dedicated surface id,
            # the canonical proposal subject is the stable openable reference.
            surface_id=revision.proposal_ref,
            rows_staged=len(proposal.rows),
            rows_pre_held=len(proposal.agent_holds),
            status=state.status.value,
        )


def _effect_class(classified: ClassifiedAction) -> EffectClass:
    if (
        classified.basis is ClassificationBasis.CATALOG
        and classified.catalog_kind is CatalogActionKind.READ
    ):
        raise StagedWriteError("A read-only operation cannot be staged as a row-set.")
    if classified.catalog_kind is CatalogActionKind.DESTRUCTIVE:
        return EffectClass.EXTERNAL_DESTRUCTIVE
    if (
        classified.basis is ClassificationBasis.CATALOG
        and classified.action_class is ActionClass.WRITE
    ):
        return EffectClass.EXTERNAL_REVERSIBLE
    if (
        classified.basis is ClassificationBasis.ANNOTATION
        and classified.action_class is ActionClass.WRITE
    ):
        return EffectClass.EXTERNAL_DESTRUCTIVE
    return EffectClass.UNKNOWN


def _effect_policy(mode: ToolUsePolicyMode) -> EffectPolicy:
    return {
        ToolUsePolicyMode.AUTO: EffectPolicy.AUTO,
        ToolUsePolicyMode.ASK: EffectPolicy.ASK,
        ToolUsePolicyMode.REQUIRE: EffectPolicy.REQUIRE,
        ToolUsePolicyMode.BLOCK: EffectPolicy.BLOCK,
    }[mode]


__all__ = ["RuntimeRowSetEffectProposalPort"]
