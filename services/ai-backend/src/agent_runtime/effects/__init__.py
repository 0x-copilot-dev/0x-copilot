"""Pure staging domain for universal external effects.

This package intentionally contains proposal, policy, fold, and decision logic only.
It has no runtime transport, persistence adapter, queue consumer, or effect executor.
Concrete durability and execution wiring belong to later A4/A5 integration work.
"""

from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectCommitCommand,
    EffectPolicyResolution,
    EffectPolicySnapshot,
    EffectProposalKind,
    EffectRevisionProposal,
    EffectStageRevision,
    EffectStageScope,
    EffectStageState,
    EffectStageStatus,
    ProposedEffect,
)
from agent_runtime.effects.fold import EffectStageFold
from agent_runtime.effects.policy import EffectStagePolicyResolver
from agent_runtime.effects.staging import EffectStager

__all__ = [
    "EffectActorIdentity",
    "EffectCommitCommand",
    "EffectPolicyResolution",
    "EffectPolicySnapshot",
    "EffectProposalKind",
    "EffectRevisionProposal",
    "EffectStageFold",
    "EffectStagePolicyResolver",
    "EffectStageRevision",
    "EffectStageScope",
    "EffectStageState",
    "EffectStageStatus",
    "EffectStager",
    "ProposedEffect",
]
