"""Strict wire contracts for an owner-scoped external effect decision."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_runtime.effects.contracts import EffectStageState, EffectStageStatus
from agent_runtime.effects.errors import EffectStageMalformedEvent
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
)

__all__ = ["EffectStageDecisionRequest", "EffectStageDecisionResponse"]

_SHA256 = r"^[a-f0-9]{64}$"


class EffectStageDecisionRequest(BaseModel):
    """Untrusted snapshot that must match the current canonical stage exactly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: PositiveInt
    decision: Literal["approve", "reject"]
    proposal_digest: str = Field(pattern=_SHA256)
    target_digest: str = Field(pattern=_SHA256)


class EffectStageDecisionResponse(BaseModel):
    """Safe terminal decision facts for a standard MCP A4 stage.

    The response deliberately omits target/proposal references and content.
    The queued A5 worker re-folds the ledger and resolves immutable material
    server-side before any connector call.
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
    def from_state(cls, state: EffectStageState) -> "EffectStageDecisionResponse":
        """Project a decision only when the folded MCP stage is exact."""

        recorded = state.decision
        status_by_decision = {
            EffectDecisionKind.APPROVE: EffectStageStatus.APPROVED,
            EffectDecisionKind.REJECT: EffectStageStatus.REJECTED,
        }
        if (
            state.executor is not EffectExecutorKind.MCP
            or recorded is None
            or recorded.revision != state.current_revision.revision
            or recorded.proposal_digest != state.current_revision.proposal_digest
            or recorded.target_digest != state.target_digest
            or recorded.decision not in status_by_decision
            or state.status is not status_by_decision[recorded.decision]
        ):
            raise EffectStageMalformedEvent(
                "The effect approval record could not be verified."
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
