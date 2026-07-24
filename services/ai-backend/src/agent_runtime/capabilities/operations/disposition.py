"""Deterministic presentation disposition, independent of execution."""

from __future__ import annotations

from agent_runtime.capabilities.operations.contracts import (
    OperationDescriptor,
    OperationRequest,
    OperationResultSummary,
    PresentationPlan,
)
from agent_runtime.presentation.policy import (
    PresentationPolicy,
    PresentationPolicyInput,
)
from agent_runtime.surfaces_v2.ledger_models import PresentationDecision


class PresentationDispositionPolicy:
    """A3 compatibility facade over the canonical B3 presentation policy.

    The gateway still exposes its historical ``PresentationPlan`` to shadow
    adapters, but it no longer owns a second decision table.  The B3 policy is
    the source of truth for publication, operation, and replay semantics.
    """

    @classmethod
    def decide(
        cls,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        result_summary: OperationResultSummary,
    ) -> PresentationDecision:
        return cls.plan(request, descriptor, result_summary).decision

    @classmethod
    def plan(
        cls,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        result_summary: OperationResultSummary,
    ) -> PresentationPlan:
        intent = request.artifact_intent
        policy = PresentationPolicy.decide(
            PresentationPolicyInput(
                explicit_preference=(
                    intent.presentation_preference if intent is not None else None
                ),
                effect_class=descriptor.effect_class,
                artifact_kind=intent.kind if intent is not None else None,
                # B1's validated launch kinds have fixed renderer ownership;
                # unsupported media is rejected before reaching the gateway.
                renderer_supported=intent is not None,
                # A result reference proves durability, not a request to open
                # Studio.  The B3 contract permits a result canvas only when
                # the operation descriptor records an explicit review intent;
                # that fact is not part of the legacy A3 request yet.  Keeping
                # this false prevents ordinary reads/scalars from becoming
                # phantom tabs while retaining the existing compatibility API.
                selected_for_review=False,
                # A legacy operation with no result ref has no durable
                # subject.  This remains a fact passed to the canonical B3
                # policy rather than a compatibility-only decision branch.
                has_presentable_subject=result_summary.result_ref is not None,
            )
        )
        return PresentationPlan(
            decision=policy.decision,
            basis=policy.basis,
        )


__all__ = ("PresentationDispositionPolicy",)
