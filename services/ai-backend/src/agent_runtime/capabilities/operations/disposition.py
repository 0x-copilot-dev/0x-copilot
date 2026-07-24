"""Deterministic presentation disposition, independent of execution."""

from __future__ import annotations

from agent_runtime.capabilities.operations.contracts import (
    OperationDescriptor,
    OperationRequest,
    OperationResultSummary,
    PresentationPlan,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactPresentationPreference,
    EffectClass,
    PresentationDecision,
)


class PresentationDispositionPolicy:
    """Pure table policy; Markdown and raw result text are never inspected."""

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
        # Safety has highest precedence: external/unknown operations are stage
        # subjects.  The canvas decision predicts that future subject; A3 shadow
        # mode never creates or mounts it.
        if descriptor.effect_class in {
            EffectClass.EXTERNAL_REVERSIBLE,
            EffectClass.EXTERNAL_DESTRUCTIVE,
            EffectClass.UNKNOWN,
        }:
            return PresentationPlan(
                decision=PresentationDecision.CANVAS,
                basis="external_effect_stage_subject",
            )

        intent = request.artifact_intent
        if intent is not None:
            preference = intent.presentation_preference
            if preference is ArtifactPresentationPreference.CANVAS:
                decision = PresentationDecision.CANVAS
            elif preference is ArtifactPresentationPreference.CHAT_CARD:
                decision = PresentationDecision.CHAT_CARD
            elif preference is ArtifactPresentationPreference.NONE:
                decision = PresentationDecision.NONE
            else:
                decision = PresentationDecision.CANVAS
            return PresentationPlan(
                decision=decision,
                basis=f"explicit_artifact_{preference.value}",
            )

        if result_summary.result_ref is not None:
            return PresentationPlan(
                decision=PresentationDecision.ACTIVITY_ONLY,
                basis="referenced_result_activity",
            )
        return PresentationPlan(
            decision=PresentationDecision.NONE,
            basis="no_durable_subject",
        )


__all__ = ("PresentationDispositionPolicy",)
