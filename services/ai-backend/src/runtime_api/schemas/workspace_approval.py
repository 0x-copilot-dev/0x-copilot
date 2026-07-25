"""Strict public C3 workspace approval request and receipt schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_runtime.effects.contracts import EffectStageState, EffectStageStatus
from agent_runtime.effects.errors import EffectStageMalformedEvent
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
)

__all__ = [
    "WorkspaceApprovalDecisionReceipt",
    "WorkspaceApprovalDecisionRequest",
]

_SHA256 = r"^[a-f0-9]{64}$"


class WorkspaceApprovalDecisionRequest(BaseModel):
    """Untrusted stage snapshot submitted by a desktop approval host.

    The server validates this snapshot against the canonical current revision
    before appending a decision.  It never returns these fields by echoing the
    request; the receipt is projected from the persisted ledger fold.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: PositiveInt
    decision: Literal["approve", "reject"]
    proposal_digest: str = Field(pattern=_SHA256)
    target_digest: str = Field(pattern=_SHA256)


class WorkspaceApprovalDecisionReceipt(BaseModel):
    """Safe, exact approval evidence for the desktop main process.

    It intentionally contains no target reference, proposal content reference,
    path, prepared reference, permit, or executable command.  All values are
    copied from the canonical post-decision effect-stage state.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_id: str = Field(min_length=1, max_length=128)
    revision: PositiveInt
    decision_ledger_id: str = Field(min_length=1, max_length=128)
    proposal_digest: str = Field(pattern=_SHA256)
    target_digest: str = Field(pattern=_SHA256)
    decision: Literal["approve", "reject"]
    status: Literal["approved", "rejected"]

    @classmethod
    def from_state(cls, state: EffectStageState) -> "WorkspaceApprovalDecisionReceipt":
        """Project a receipt only if the folded decision is exact and terminal."""

        recorded = state.decision
        status_by_decision = {
            EffectDecisionKind.APPROVE: EffectStageStatus.APPROVED,
            EffectDecisionKind.REJECT: EffectStageStatus.REJECTED,
        }
        if (
            state.executor is not EffectExecutorKind.WORKSPACE
            or recorded is None
            or recorded.revision != state.current_revision.revision
            or recorded.proposal_digest != state.current_revision.proposal_digest
            or recorded.target_digest != state.target_digest
            or recorded.decision not in status_by_decision
            or state.status is not status_by_decision[recorded.decision]
        ):
            raise EffectStageMalformedEvent(
                "The workspace approval record could not be verified."
            )
        return cls(
            stage_id=state.stage_id,
            revision=recorded.revision,
            decision_ledger_id=recorded.ledger_id,
            proposal_digest=recorded.proposal_digest,
            target_digest=recorded.target_digest,
            decision=recorded.decision.value,
            status=state.status.value,
        )
