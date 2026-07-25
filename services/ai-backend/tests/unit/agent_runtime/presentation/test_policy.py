"""Exhaustive decision-table coverage for the B3 PresentationPolicy."""

from __future__ import annotations

import pytest

from agent_runtime.presentation.policy import (
    PresentationPolicy,
    PresentationPolicyInput,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactKind,
    ArtifactPresentationPreference,
    EffectClass,
    PresentationDecision,
    SurfaceSubjectType,
)


@pytest.mark.parametrize(
    "effect_class",
    [
        EffectClass.EXTERNAL_REVERSIBLE,
        EffectClass.EXTERNAL_DESTRUCTIVE,
        EffectClass.UNKNOWN,
    ],
)
def test_effects_are_always_reviewable_stages(effect_class: EffectClass) -> None:
    decision = PresentationPolicy.decide(
        PresentationPolicyInput(effect_class=effect_class)
    )
    assert (decision.decision, decision.subject_type, decision.basis) == (
        PresentationDecision.CANVAS,
        SurfaceSubjectType.STAGE,
        "stage_required",
    )


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            PresentationPolicyInput(has_gate=True),
            (PresentationDecision.CHAT_CARD, SurfaceSubjectType.GATE, "gate_parked"),
        ),
        (
            PresentationPolicyInput(
                is_receipt=True,
                has_meaningful_receipt_facts=True,
                current_canvas_has_subject=False,
            ),
            (
                PresentationDecision.CANVAS,
                SurfaceSubjectType.RECEIPT,
                "meaningful_receipt_without_subject",
            ),
        ),
        (
            PresentationPolicyInput(
                is_receipt=True,
                has_meaningful_receipt_facts=True,
                current_canvas_has_subject=True,
            ),
            (
                PresentationDecision.NONE,
                SurfaceSubjectType.RECEIPT,
                "receipt_rail_only",
            ),
        ),
        (
            PresentationPolicyInput(
                explicit_preference=ArtifactPresentationPreference.NONE,
                artifact_kind=ArtifactKind.CODE,
                renderer_supported=True,
            ),
            (
                PresentationDecision.NONE,
                SurfaceSubjectType.ARTIFACT,
                "explicit_artifact_none",
            ),
        ),
        (
            PresentationPolicyInput(
                explicit_preference=ArtifactPresentationPreference.CANVAS,
                artifact_kind=ArtifactKind.CODE,
                renderer_supported=False,
            ),
            (
                PresentationDecision.CHAT_CARD,
                SurfaceSubjectType.ARTIFACT,
                "unsupported_artifact_canvas_downgraded",
            ),
        ),
        (
            PresentationPolicyInput(
                explicit_preference=ArtifactPresentationPreference.CHAT_CARD,
                artifact_kind=ArtifactKind.DOCUMENT,
                renderer_supported=True,
            ),
            (
                PresentationDecision.CHAT_CARD,
                SurfaceSubjectType.ARTIFACT,
                "explicit_artifact_chat_card",
            ),
        ),
        (
            PresentationPolicyInput(
                artifact_kind=ArtifactKind.DATASET,
                renderer_supported=True,
            ),
            (
                PresentationDecision.CANVAS,
                SurfaceSubjectType.ARTIFACT,
                "durable_supported_artifact_auto",
            ),
        ),
        (
            PresentationPolicyInput(
                artifact_kind=ArtifactKind.FILE,
                artifact_size=512 * 1024,
                renderer_supported=True,
            ),
            (
                PresentationDecision.CANVAS,
                SurfaceSubjectType.ARTIFACT,
                "bounded_file_artifact_auto",
            ),
        ),
        (
            PresentationPolicyInput(
                artifact_kind=ArtifactKind.FILE,
                artifact_size=512 * 1024 + 1,
                renderer_supported=True,
            ),
            (
                PresentationDecision.CHAT_CARD,
                SurfaceSubjectType.ARTIFACT,
                "artifact_raw_or_large_auto",
            ),
        ),
        (
            PresentationPolicyInput(selected_for_review=True),
            (
                PresentationDecision.CANVAS,
                SurfaceSubjectType.RECORD,
                "explicit_revisitable_result",
            ),
        ),
        (
            PresentationPolicyInput(),
            (PresentationDecision.ACTIVITY_ONLY, None, "transient_result_activity"),
        ),
        (
            PresentationPolicyInput(has_presentable_subject=False),
            (PresentationDecision.NONE, None, "no_presentable_subject"),
        ),
    ],
)
def test_every_non_effect_presentation_branch_is_explicit(
    facts: PresentationPolicyInput,
    expected: tuple[PresentationDecision, SurfaceSubjectType | None, str],
) -> None:
    decision = PresentationPolicy.decide(facts)
    assert (decision.decision, decision.subject_type, decision.basis) == expected


def test_stage_dominates_explicit_none_and_gate() -> None:
    decision = PresentationPolicy.decide(
        PresentationPolicyInput(
            has_stage=True,
            has_gate=True,
            explicit_preference=ArtifactPresentationPreference.NONE,
            artifact_kind=ArtifactKind.CODE,
        )
    )
    assert decision.decision is PresentationDecision.CANVAS
    assert decision.subject_type is SurfaceSubjectType.STAGE
