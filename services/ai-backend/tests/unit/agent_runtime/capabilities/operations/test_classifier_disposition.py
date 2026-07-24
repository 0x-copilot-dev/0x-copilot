from __future__ import annotations

import pytest

from agent_runtime.capabilities.mcp.annotations import McpToolAnnotations
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    ArtifactIntent,
    OperationResultSummary,
)
from agent_runtime.capabilities.operations.disposition import (
    PresentationDispositionPolicy,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactKind,
    ArtifactPresentationPreference,
    EffectClass,
    OperationClassificationBasis,
    PresentationDecision,
)
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
)


class TestDescriptorFirstClassifier(BoundContextMixin):
    @pytest.fixture(autouse=True)
    def _bound_context(self):
        token = self.bind()
        try:
            yield
        finally:
            OperationContext.unbind(token)

    @property
    def classifier(self) -> OperationClassifier:
        return OperationClassifier(descriptors=DEFAULT_OPERATION_DESCRIPTORS)

    def _request(self, capability: str, op: str):
        return OperationRequestFactory.create(
            capability=capability,
            op=op,
            arguments={"untrusted": "never-in-reason"},
        )

    def test_exact_product_descriptor_wins_over_destructive_hint(self) -> None:
        result = self.classifier.classify(
            self._request("workspace", "write"),
            annotations=McpToolAnnotations(destructive_hint=True),
        )

        assert result.effect_class is EffectClass.EXTERNAL_REVERSIBLE
        assert result.basis is OperationClassificationBasis.DESCRIPTOR
        assert result.reasons == ("product_descriptor_exact",)

    def test_curated_catalog_read_wins_over_contradicting_annotation(self) -> None:
        result = self.classifier.classify(
            self._request("github", "get_issue"),
            annotations=McpToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
            ),
        )

        assert result.effect_class is EffectClass.NONE
        assert result.basis is OperationClassificationBasis.CATALOG
        assert result.reasons == ("curated_catalog_read",)

    @pytest.mark.parametrize(
        ("annotations", "expected", "reason"),
        [
            (
                McpToolAnnotations(read_only_hint=True),
                EffectClass.UNKNOWN,
                "provider_annotation_cannot_loosen",
            ),
            (
                McpToolAnnotations(read_only_hint=False),
                EffectClass.EXTERNAL_REVERSIBLE,
                "provider_annotation_tightened_write",
            ),
            (
                McpToolAnnotations(destructive_hint=True),
                EffectClass.EXTERNAL_DESTRUCTIVE,
                "provider_annotation_tightened_destructive",
            ),
            (None, EffectClass.UNKNOWN, "safe_default_unknown"),
        ],
    )
    def test_unknown_operation_only_tightens(
        self,
        annotations: McpToolAnnotations | None,
        expected: EffectClass,
        reason: str,
    ) -> None:
        result = self.classifier.classify(
            self._request("external-provider", "new_operation"),
            annotations=annotations,
        )

        assert result.effect_class is expected
        assert result.reasons == (reason,)
        assert "untrusted" not in repr(result)
        assert "never-in-reason" not in repr(result)


class TestPresentationDisposition(BoundContextMixin):
    @pytest.fixture(autouse=True)
    def _bound_context(self):
        token = self.bind()
        try:
            yield
        finally:
            OperationContext.unbind(token)

    def _plan(
        self,
        *,
        effect: EffectClass,
        preference: ArtifactPresentationPreference | None = None,
        result_ref: str | None = "payload://result",
    ) -> PresentationDecision:
        intent = (
            ArtifactIntent(
                kind=ArtifactKind.DOCUMENT,
                title="Notes",
                presentation_preference=preference,
            )
            if preference is not None
            else None
        )
        request = OperationRequestFactory.create(
            capability="workspace",
            op="read",
            arguments={},
            artifact_intent=intent,
        )
        descriptor = DEFAULT_OPERATION_DESCRIPTORS.resolve("workspace", "read")
        assert descriptor is not None
        descriptor = descriptor.model_copy(update={"effect_class": effect})
        return PresentationDispositionPolicy.decide(
            request,
            descriptor,
            OperationResultSummary(
                result_ref=result_ref,
                safe_summary="bounded",
            ),
        )

    @pytest.mark.parametrize(
        "effect",
        [
            EffectClass.EXTERNAL_REVERSIBLE,
            EffectClass.EXTERNAL_DESTRUCTIVE,
            EffectClass.UNKNOWN,
        ],
    )
    def test_external_or_unknown_always_predicts_stage_canvas(
        self, effect: EffectClass
    ) -> None:
        assert (
            self._plan(
                effect=effect,
                preference=ArtifactPresentationPreference.NONE,
                result_ref=None,
            )
            is PresentationDecision.CANVAS
        )

    @pytest.mark.parametrize(
        ("preference", "expected"),
        [
            (
                ArtifactPresentationPreference.AUTO,
                PresentationDecision.CANVAS,
            ),
            (
                ArtifactPresentationPreference.CANVAS,
                PresentationDecision.CANVAS,
            ),
            (
                ArtifactPresentationPreference.CHAT_CARD,
                PresentationDecision.CHAT_CARD,
            ),
            (
                ArtifactPresentationPreference.NONE,
                PresentationDecision.NONE,
            ),
        ],
    )
    def test_explicit_intent_controls_non_external_presentation(
        self,
        preference: ArtifactPresentationPreference,
        expected: PresentationDecision,
    ) -> None:
        assert (
            self._plan(
                effect=EffectClass.NONE,
                preference=preference,
            )
            is expected
        )

    def test_no_intent_uses_only_reference_presence_not_result_text(self) -> None:
        assert (
            self._plan(
                effect=EffectClass.NONE,
                result_ref="payload://result",
            )
            is PresentationDecision.ACTIVITY_ONLY
        )
        assert (
            self._plan(
                effect=EffectClass.NONE,
                result_ref=None,
            )
            is PresentationDecision.NONE
        )

    def test_effect_intent_reference_matrix_is_exhaustive(self) -> None:
        preference_results = {
            ArtifactPresentationPreference.AUTO: PresentationDecision.CANVAS,
            ArtifactPresentationPreference.CANVAS: PresentationDecision.CANVAS,
            ArtifactPresentationPreference.CHAT_CARD: (PresentationDecision.CHAT_CARD),
            ArtifactPresentationPreference.NONE: PresentationDecision.NONE,
        }
        external = {
            EffectClass.EXTERNAL_REVERSIBLE,
            EffectClass.EXTERNAL_DESTRUCTIVE,
            EffectClass.UNKNOWN,
        }

        visited: set[
            tuple[
                EffectClass,
                ArtifactPresentationPreference | None,
                bool,
            ]
        ] = set()
        for effect in EffectClass:
            for preference in (
                None,
                *tuple(ArtifactPresentationPreference),
            ):
                for has_ref in (False, True):
                    visited.add((effect, preference, has_ref))
                    actual = self._plan(
                        effect=effect,
                        preference=preference,
                        result_ref=("payload://result" if has_ref else None),
                    )
                    if effect in external:
                        expected = PresentationDecision.CANVAS
                    elif preference is not None:
                        expected = preference_results[preference]
                    elif has_ref:
                        expected = PresentationDecision.ACTIVITY_ONLY
                    else:
                        expected = PresentationDecision.NONE
                    assert actual is expected

        assert len(visited) == (
            len(tuple(EffectClass))
            * (len(tuple(ArtifactPresentationPreference)) + 1)
            * 2
        )
