"""Pure classifier wrapper over the existing C1 action catalog."""

from __future__ import annotations

from agent_runtime.capabilities.actions.classifier import (
    ACTION_CLASSIFIER,
    ActionClassifier,
)
from agent_runtime.capabilities.actions.contracts import (
    CatalogActionKind,
    ClassificationBasis,
)
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotations
from agent_runtime.capabilities.operations.contracts import (
    OperationClassification,
    OperationRequest,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    OperationClassificationBasis,
)


class OperationClassifier:
    """Descriptor-first effect classifier.

    The existing C1 :class:`ActionClassifier` remains the only curated
    connector catalog.  Annotation-only ``readOnlyHint`` cannot loosen an
    unknown operation into a read; destructive hints may tighten it.
    """

    __slots__ = ("_descriptors", "_legacy")

    def __init__(
        self,
        *,
        descriptors: OperationDescriptorRegistry,
        legacy: ActionClassifier = ACTION_CLASSIFIER,
    ) -> None:
        self._descriptors = descriptors
        self._legacy = legacy

    def classify(
        self,
        request: OperationRequest,
        *,
        annotations: McpToolAnnotations | None = None,
    ) -> OperationClassification:
        """Return a total classification without exposing argument values."""

        entry = self._descriptors.resolve_entry(request.capability, request.op)
        if entry is not None:
            return OperationClassification(
                effect_class=entry.descriptor.effect_class,
                basis=OperationClassificationBasis.DESCRIPTOR,
                confidence=1.0,
                descriptor_version=entry.descriptor_version,
                reasons=("product_descriptor_exact",),
            )

        try:
            legacy = self._legacy.classify(
                server=request.capability,
                tool=request.op,
                annotations=annotations,
            )
        except Exception:  # pragma: no cover - C1 is total; defensive fail-closed
            return self._unknown("classifier_error")

        if legacy.basis is ClassificationBasis.CATALOG:
            if legacy.catalog_kind is CatalogActionKind.READ:
                effect = EffectClass.NONE
                reason = "curated_catalog_read"
            elif legacy.catalog_kind is CatalogActionKind.DESTRUCTIVE:
                effect = EffectClass.EXTERNAL_DESTRUCTIVE
                reason = "curated_catalog_destructive"
            else:
                effect = EffectClass.EXTERNAL_REVERSIBLE
                reason = "curated_catalog_write"
            return OperationClassification(
                effect_class=effect,
                basis=OperationClassificationBasis.CATALOG,
                confidence=1.0,
                descriptor_version="c1-action-catalog-v1",
                reasons=(reason,),
            )

        if annotations is not None and annotations.destructive_hint is True:
            return OperationClassification(
                effect_class=EffectClass.EXTERNAL_DESTRUCTIVE,
                basis=OperationClassificationBasis.PROVIDER_ANNOTATION,
                confidence=0.75,
                descriptor_version="mcp-annotation-v1",
                reasons=("provider_annotation_tightened_destructive",),
            )
        if annotations is not None and annotations.read_only_hint is False:
            return OperationClassification(
                effect_class=EffectClass.EXTERNAL_REVERSIBLE,
                basis=OperationClassificationBasis.PROVIDER_ANNOTATION,
                confidence=0.75,
                descriptor_version="mcp-annotation-v1",
                reasons=("provider_annotation_tightened_write",),
            )

        # ``readOnlyHint`` is not authoritative and therefore cannot loosen the
        # safe default.  This intentionally differs from C1's legacy display
        # classification; shadow mismatch metrics make that divergence visible.
        if legacy.basis is ClassificationBasis.ANNOTATION:
            return self._unknown("provider_annotation_cannot_loosen")
        return self._unknown("safe_default_unknown")

    @staticmethod
    def _unknown(reason: str) -> OperationClassification:
        return OperationClassification(
            effect_class=EffectClass.UNKNOWN,
            basis=OperationClassificationBasis.DEFAULT,
            confidence=0.0,
            descriptor_version="safe-default-v1",
            reasons=(reason,),
        )


__all__ = ("OperationClassifier",)
