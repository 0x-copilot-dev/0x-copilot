"""Deterministic, fail-closed presentation policy (PRD-B3 D1).

The policy deliberately has no model dependency and does not inspect result
contents.  It answers the narrower, auditable question *where should a known
subject appear?* after execution/persistence has already established that the
subject exists.  The same decision table is mirrored by the client lifecycle
projection for replay and loading-state behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactKind,
    ArtifactPresentationPreference,
    EffectClass,
    PresentationDecision,
    SurfaceSubjectType,
)


@dataclass(frozen=True, slots=True)
class PresentationPolicyInput:
    """Safe facts available at the presentation boundary.

    ``artifact_kind``/``artifact_size`` originate from the repository, effect
    and gate facts from their typed ledger records, and ``selected_for_review``
    from an explicit user/capability descriptor decision.  No caller-controlled
    title, body, path, or raw operation argument affects the decision.
    """

    explicit_preference: ArtifactPresentationPreference | None = None
    effect_class: EffectClass = EffectClass.NONE
    artifact_kind: ArtifactKind | None = None
    artifact_size: int | None = None
    renderer_supported: bool = False
    selected_for_review: bool = False
    has_presentable_subject: bool = True
    has_stage: bool = False
    has_gate: bool = False
    is_receipt: bool = False
    has_meaningful_receipt_facts: bool = False
    current_canvas_has_subject: bool = False


@dataclass(frozen=True, slots=True)
class PresentationPolicyDecision:
    """A closed presentation result suitable for ledgering and replay."""

    decision: PresentationDecision
    subject_type: SurfaceSubjectType | None
    renderer_hint: str | None
    basis: str
    priority: int


class PresentationPolicy:
    """Exhaustive table policy for Studio/Focus presentation.

    The branch order is part of the contract: safety/stages dominate every
    preference, explicit ``none`` is never silently upgraded, and a receipt
    never displaces work already under review.
    """

    _CANVAS_ARTIFACT_KINDS = frozenset(
        {ArtifactKind.CODE, ArtifactKind.DOCUMENT, ArtifactKind.DATASET}
    )
    _FILE_CANVAS_MAX_BYTES = 512 * 1024

    @classmethod
    def decide(cls, facts: PresentationPolicyInput) -> PresentationPolicyDecision:
        # External, destructive, and unknown work must be represented by a
        # reviewable stage, never a result-shaped canvas inferred from output.
        if facts.has_stage or facts.effect_class in {
            EffectClass.EXTERNAL_REVERSIBLE,
            EffectClass.EXTERNAL_DESTRUCTIVE,
            EffectClass.UNKNOWN,
        }:
            return PresentationPolicyDecision(
                decision=PresentationDecision.CANVAS,
                subject_type=SurfaceSubjectType.STAGE,
                renderer_hint="effect-stage",
                basis="stage_required",
                priority=100,
            )

        # A gate is a compact parked card unless a stage/artifact is already
        # reviewable.  This prevents auth from creating a phantom canvas tab.
        if facts.has_gate:
            return PresentationPolicyDecision(
                decision=PresentationDecision.CHAT_CARD,
                subject_type=SurfaceSubjectType.GATE,
                renderer_hint="gate-card",
                basis="gate_parked",
                priority=90,
            )

        # Receipts belong to the rail/summary by default.  They only become a
        # canvas candidate when nothing better exists and the receipt has facts.
        if facts.is_receipt:
            if (
                facts.has_meaningful_receipt_facts
                and not facts.current_canvas_has_subject
            ):
                return PresentationPolicyDecision(
                    decision=PresentationDecision.CANVAS,
                    subject_type=SurfaceSubjectType.RECEIPT,
                    renderer_hint="receipt",
                    basis="meaningful_receipt_without_subject",
                    priority=10,
                )
            return PresentationPolicyDecision(
                decision=PresentationDecision.NONE,
                subject_type=SurfaceSubjectType.RECEIPT,
                renderer_hint="receipt",
                basis="receipt_rail_only",
                priority=0,
            )

        preference = facts.explicit_preference
        if preference is ArtifactPresentationPreference.NONE:
            return PresentationPolicyDecision(
                decision=PresentationDecision.NONE,
                subject_type=SurfaceSubjectType.ARTIFACT
                if facts.artifact_kind is not None
                else None,
                renderer_hint=None,
                basis="explicit_artifact_none",
                priority=0,
            )

        if facts.artifact_kind is not None:
            renderer_hint = f"artifact-{facts.artifact_kind.value}"
            # Explicit canvas is a request, not an authority.  Unsupported
            # renderers degrade honestly to a compact raw/file card.
            if preference is ArtifactPresentationPreference.CANVAS:
                return PresentationPolicyDecision(
                    decision=(
                        PresentationDecision.CANVAS
                        if facts.renderer_supported
                        else PresentationDecision.CHAT_CARD
                    ),
                    subject_type=SurfaceSubjectType.ARTIFACT,
                    renderer_hint=renderer_hint
                    if facts.renderer_supported
                    else "artifact-raw",
                    basis=(
                        "explicit_artifact_canvas"
                        if facts.renderer_supported
                        else "unsupported_artifact_canvas_downgraded"
                    ),
                    priority=80,
                )
            if preference is ArtifactPresentationPreference.CHAT_CARD:
                return PresentationPolicyDecision(
                    decision=PresentationDecision.CHAT_CARD,
                    subject_type=SurfaceSubjectType.ARTIFACT,
                    renderer_hint=renderer_hint,
                    basis="explicit_artifact_chat_card",
                    priority=40,
                )

            # ``auto`` uses only repository facts.  Code/docs/datasets have
            # fixed safe renderers; generic files remain a card for large/raw
            # content and a metadata canvas for bounded safe files.
            if (
                facts.renderer_supported
                and facts.artifact_kind in cls._CANVAS_ARTIFACT_KINDS
            ):
                return PresentationPolicyDecision(
                    decision=PresentationDecision.CANVAS,
                    subject_type=SurfaceSubjectType.ARTIFACT,
                    renderer_hint=renderer_hint,
                    basis="durable_supported_artifact_auto",
                    priority=70,
                )
            if (
                facts.renderer_supported
                and facts.artifact_kind is ArtifactKind.FILE
                and facts.artifact_size is not None
                and facts.artifact_size <= cls._FILE_CANVAS_MAX_BYTES
            ):
                return PresentationPolicyDecision(
                    decision=PresentationDecision.CANVAS,
                    subject_type=SurfaceSubjectType.ARTIFACT,
                    renderer_hint=renderer_hint,
                    basis="bounded_file_artifact_auto",
                    priority=50,
                )
            return PresentationPolicyDecision(
                decision=PresentationDecision.CHAT_CARD,
                subject_type=SurfaceSubjectType.ARTIFACT,
                renderer_hint="artifact-raw",
                basis="artifact_raw_or_large_auto",
                priority=30,
            )

        if facts.selected_for_review:
            return PresentationPolicyDecision(
                decision=PresentationDecision.CANVAS,
                subject_type=SurfaceSubjectType.RECORD,
                renderer_hint="record-or-table",
                basis="explicit_revisitable_result",
                priority=60,
            )

        # A caller may establish that there is no durable result at all.  In
        # that case there is no subject to represent, even as activity.  This
        # is distinct from a scalar result, which remains auditable activity.
        if not facts.has_presentable_subject:
            return PresentationPolicyDecision(
                decision=PresentationDecision.NONE,
                subject_type=None,
                renderer_hint=None,
                basis="no_presentable_subject",
                priority=0,
            )

        # Scalar/transient results remain auditable activity.  No mapping or
        # result shape can create a tab through this fallback.
        return PresentationPolicyDecision(
            decision=PresentationDecision.ACTIVITY_ONLY,
            subject_type=None,
            renderer_hint=None,
            basis="transient_result_activity",
            priority=0,
        )


__all__ = (
    "PresentationPolicy",
    "PresentationPolicyDecision",
    "PresentationPolicyInput",
)
