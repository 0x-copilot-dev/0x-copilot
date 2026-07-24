"""Non-authoritative shadow probes for existing operation seams."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.actions.classifier import ACTION_CLASSIFIER
from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
)
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
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
    ModelArtifactContentPart,
    OperationClassification,
    OperationGatewayMode,
    OperationRequest,
    OperationResultSummary,
)
from agent_runtime.capabilities.operations.disposition import (
    PresentationDispositionPolicy,
)
from agent_runtime.capabilities.tools.builtin.publish_artifact import (
    iter_artifact_content_parts,
)
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    OperationClassifiedPayload,
    OperationCompletedPayload,
    OperationFailedPayload,
    OperationOutcome,
    OperationRequestedPayload,
    PresentationDecision,
    Producer,
)

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)
_CLASSIFIER = OperationClassifier(descriptors=DEFAULT_OPERATION_DESCRIPTORS)


class OperationShadowProbe:
    """Observe one legacy invocation without becoming its source of truth."""

    @classmethod
    async def invoke_legacy(
        cls,
        *,
        capability: str,
        op: str,
        arguments: Mapping[str, object],
        legacy: Callable[[], Awaitable[_T]],
        producer: Producer | None = None,
        legacy_class: str | None = None,
        legacy_disposition: PresentationDecision | None = None,
    ) -> _T:
        context = OperationContext.active()
        if context is None or context.mode is not OperationGatewayMode.SHADOW:
            return await legacy()

        request = cls._request(
            capability=capability,
            op=op,
            arguments=arguments,
            producer=producer or OperationContext.current_producer(),
        )
        classification: OperationClassification | None = None
        if request is not None:
            try:
                classification = cls._classification(request)
            except Exception:
                _LOGGER.debug("operation_gateway.shadow_classification_failed")
        started = time.perf_counter()
        if request is not None and classification is not None:
            await cls._observe_safely(
                cls._emit_start(request=request, classification=classification)
            )
            cls._compare_classification_safely(
                request=request,
                classification=classification,
                legacy_class=legacy_class,
            )

        try:
            if request is None:
                return await legacy()
            with OperationContext.operation_scope(request.operation_id):
                result = await legacy()
        except asyncio.CancelledError:
            if request is not None and classification is not None:
                await cls._observe_safely(
                    cls._emit_completed(
                        request=request,
                        outcome=OperationOutcome.CANCELLED,
                        started=started,
                    )
                )
            raise
        except BaseException:
            if request is not None:
                await cls._observe_safely(cls._emit_failed(request))
            raise

        if request is not None and classification is not None:
            await cls._observe_safely(
                cls._emit_completed(
                    request=request,
                    outcome=OperationOutcome.SUCCEEDED,
                    started=started,
                )
            )
            cls._observe_disposition_safely(
                request=request,
                classification=classification,
                legacy_disposition=legacy_disposition,
            )
        # Exact object identity is preserved for mutable/domain results, and
        # bytes/strings are returned without serialization or copying.
        return result

    @classmethod
    async def observe_model_result(cls, result: object) -> None:
        """Record explicit typed artifact parts only; never parse prose/fences."""

        context = OperationContext.active()
        if context is None or context.mode is not OperationGatewayMode.SHADOW:
            return
        try:
            for part in cls._artifact_parts(result):
                request = cls._request(
                    capability="model",
                    op="artifact_content_part",
                    arguments={
                        "content_ref": part.content_ref,
                        "intent": part.intent.model_dump(mode="json"),
                    },
                    producer=OperationContext.current_producer(),
                    artifact_intent=part.intent,
                )
                if request is None:
                    continue
                classification = cls._classification(request)
                await cls._observe_safely(
                    cls._emit_start(request=request, classification=classification)
                )
                await cls._observe_safely(
                    cls._emit_completed(
                        request=request,
                        outcome=OperationOutcome.SUCCEEDED,
                        started=time.perf_counter(),
                    )
                )
        except asyncio.CancelledError:
            if cls._task_is_cancelling():
                raise
            _LOGGER.debug("operation_gateway.shadow_model_observation_failed")
        except Exception:
            _LOGGER.debug("operation_gateway.shadow_model_observation_failed")

    @classmethod
    def _request(
        cls,
        *,
        capability: str,
        op: str,
        arguments: Mapping[str, object],
        producer: Producer,
        artifact_intent: ArtifactIntent | None = None,
    ) -> OperationRequest | None:
        try:
            return OperationRequestFactory.create(
                capability=capability,
                op=op,
                arguments=arguments,
                producer=producer,
                artifact_intent=artifact_intent,
            )
        except Exception:
            _LOGGER.debug("operation_gateway.shadow_request_failed")
            return None

    @staticmethod
    def _classification(request: OperationRequest) -> OperationClassification:
        return _CLASSIFIER.classify(
            request,
            annotations=McpToolAnnotationsRegistry.get(request.capability, request.op),
        )

    @staticmethod
    def legacy_mcp_effect_class(capability: str, op: str) -> str:
        """Map the existing C1 verdict onto the v2.1 effect axis for telemetry."""

        try:
            classified = ACTION_CLASSIFIER.classify(
                server=capability,
                tool=op,
                annotations=McpToolAnnotationsRegistry.get(capability, op),
            )
            if classified.catalog_kind is CatalogActionKind.DESTRUCTIVE:
                return "external_destructive"
            if classified.action_class is ActionClass.READ:
                return "none"
            return "external_reversible"
        except Exception:
            return "unknown"

    @classmethod
    async def _emit_start(
        cls,
        *,
        request: OperationRequest,
        classification: OperationClassification,
    ) -> None:
        context = OperationContext.require()
        requested = OperationRequestedPayload(
            v=1,
            operation_id=request.operation_id,
            producer=request.producer,
            capability=request.capability,
            op=request.op,
            args_digest=request.args_digest,
            parent_operation_id=request.parent_operation_id,
        )
        classified = OperationClassifiedPayload(
            v=1,
            operation_id=request.operation_id,
            effect_class=classification.effect_class,
            basis=classification.basis,
            confidence=classification.confidence,
        )
        await context.ledger_emitter.emit(
            LedgerEventType.OPERATION_REQUESTED,
            requested.model_dump(mode="json", exclude_none=True),
        )
        await context.ledger_emitter.emit(
            LedgerEventType.OPERATION_CLASSIFIED,
            classified.model_dump(mode="json"),
        )
        metric_capability, metric_op = DEFAULT_OPERATION_DESCRIPTORS.metric_key(
            request.capability, request.op
        )
        context.metrics.requested(
            capability=metric_capability,
            op=metric_op,
            effect_class=classification.effect_class.value,
        )
        if request.artifact_intent is not None:
            context.metrics.artifact_intent(
                capability=metric_capability,
                op=metric_op,
            )

    @staticmethod
    async def _emit_completed(
        *,
        request: OperationRequest,
        outcome: OperationOutcome,
        started: float,
    ) -> None:
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        payload = OperationCompletedPayload(
            v=1,
            operation_id=request.operation_id,
            outcome=outcome,
            latency_ms=latency_ms,
        )
        context = OperationContext.require()
        await context.ledger_emitter.emit(
            LedgerEventType.OPERATION_COMPLETED,
            payload.model_dump(mode="json", exclude_none=True),
        )
        classification = OperationShadowProbe._classification(request)
        metric_capability, metric_op = DEFAULT_OPERATION_DESCRIPTORS.metric_key(
            request.capability, request.op
        )
        context.metrics.completed(
            capability=metric_capability,
            op=metric_op,
            effect_class=classification.effect_class.value,
            outcome=outcome.value,
            latency_ms=latency_ms,
        )

    @staticmethod
    async def _emit_failed(request: OperationRequest) -> None:
        payload = OperationFailedPayload(
            v=1,
            operation_id=request.operation_id,
            failure_code="legacy_operation_failed",
            retryable=False,
        )
        context = OperationContext.require()
        await context.ledger_emitter.emit(
            LedgerEventType.OPERATION_FAILED,
            payload.model_dump(mode="json"),
        )

    @staticmethod
    async def _observe_safely(awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except asyncio.CancelledError:
            if OperationShadowProbe._task_is_cancelling():
                raise
            _LOGGER.debug("operation_gateway.shadow_observation_failed")
        except Exception:
            _LOGGER.debug("operation_gateway.shadow_observation_failed")

    @staticmethod
    def _task_is_cancelling() -> bool:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            return False
        return task is not None and task.cancelling() > 0

    @staticmethod
    def _compare_classification_safely(
        *,
        request: OperationRequest,
        classification: OperationClassification,
        legacy_class: str | None,
    ) -> None:
        if legacy_class is None or legacy_class == classification.effect_class.value:
            return
        try:
            metric_capability, metric_op = DEFAULT_OPERATION_DESCRIPTORS.metric_key(
                request.capability, request.op
            )
            OperationContext.require().metrics.classification_mismatch(
                capability=metric_capability,
                op=metric_op,
                legacy_class=legacy_class,
                gateway_class=classification.effect_class.value,
            )
        except Exception:
            _LOGGER.debug("operation_gateway.shadow_classification_metric_failed")

    @staticmethod
    def _observe_disposition_safely(
        *,
        request: OperationRequest,
        classification: OperationClassification,
        legacy_disposition: PresentationDecision | None,
    ) -> None:
        try:
            entry = DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(
                request.capability, request.op
            )
            descriptor = (
                entry.descriptor
                if entry is not None
                else DEFAULT_OPERATION_DESCRIPTORS.safe_default(
                    capability=request.capability, op=request.op
                ).descriptor
            ).model_copy(update={"effect_class": classification.effect_class})
            predicted = PresentationDispositionPolicy.decide(
                request,
                descriptor,
                OperationResultSummary(
                    result_ref=None,
                    safe_summary="Legacy result observed.",
                ),
            )
            if legacy_disposition is not None and predicted is not legacy_disposition:
                metric_capability, metric_op = DEFAULT_OPERATION_DESCRIPTORS.metric_key(
                    request.capability, request.op
                )
                OperationContext.require().metrics.disposition_mismatch(
                    capability=metric_capability,
                    op=metric_op,
                    legacy_disposition=legacy_disposition.value,
                    gateway_disposition=predicted.value,
                )
        except Exception:
            _LOGGER.debug("operation_gateway.shadow_disposition_failed")

    @staticmethod
    def _artifact_parts(result: object) -> tuple[ModelArtifactContentPart, ...]:
        # A3's dark-path observer remains ref-only. Inline B1 content is
        # intentionally ignored until the publication flag is enabled by the
        # run handler, preserving the pre-B1 shadow behavior byte-for-byte.
        return tuple(
            ModelArtifactContentPart.model_validate(part.model_dump(mode="json"))
            for part in iter_artifact_content_parts(result)
            if part.content_ref is not None
        )


def wrap_model_tool_for_shadow(
    tool: object,
    *,
    capability: str = "builtin",
) -> object:
    """Return a schema-equivalent wrapper only while a shadow context is active."""

    context = OperationContext.active()
    if context is None or context.mode is not OperationGatewayMode.SHADOW:
        return tool
    ainvoke = getattr(tool, "ainvoke", None)
    name = str(getattr(tool, "name", "")).strip()
    if not name or not callable(ainvoke):
        return tool

    async def _invoke(
        config: RunnableConfig,
        **kwargs: Any,
    ) -> object:
        return await OperationShadowProbe.invoke_legacy(
            capability=capability,
            op=name,
            arguments=kwargs,
            legacy=lambda: ainvoke(kwargs, config=config),
        )

    return StructuredTool.from_function(
        coroutine=_invoke,
        name=name,
        description=str(getattr(tool, "description", "")),
        args_schema=getattr(tool, "args_schema", None),
        return_direct=bool(getattr(tool, "return_direct", False)),
        response_format=getattr(tool, "response_format", "content"),
        callbacks=getattr(tool, "callbacks", None),
        tags=getattr(tool, "tags", None),
        metadata=getattr(tool, "metadata", None),
        handle_tool_error=getattr(tool, "handle_tool_error", False),
        handle_validation_error=getattr(tool, "handle_validation_error", False),
    )


__all__ = ("OperationShadowProbe", "wrap_model_tool_for_shadow")
