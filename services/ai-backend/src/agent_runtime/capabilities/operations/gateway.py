"""Deterministic Universal Operation Gateway."""

from __future__ import annotations

import asyncio
import json
import time

from agent_runtime.artifacts.contracts import (
    ArtifactCreateRequest,
    ArtifactPromotionRequest,
    ArtifactProvenance,
)
from agent_runtime.artifacts.errors import ArtifactError
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    _GatewayPresentationContext,
)
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    ArtifactPublicationSource,
    OperationAdapter,
    OperationClassification,
    OperationDescriptor,
    OperationDisposition,
    OperationGateResolver,
    OperationGatewayMode,
    OperationPresentationOutcome,
    OperationRawResult,
    OperationRequest,
    OperationResultSummary,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.disposition import (
    PresentationDispositionPolicy,
)
from agent_runtime.capabilities.operations.errors import (
    OperationArgumentsDigestMismatchError,
    OperationArgumentsMissingError,
    OperationGatewayError,
    OperationGatewayErrorCode,
    OperationEnforcementNotReadyError,
    OperationIdentityMismatchError,
)
from agent_runtime.surfaces_v2.canonical_json import (
    CanonicalJsonError,
    canonical_json_bytes,
    sha256_hex,
)
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    ArtifactAuthor,
    LedgerEventType,
    OperationClassifiedPayload,
    OperationCompletedPayload,
    OperationFailedPayload,
    OperationOutcome,
    OperationRequestedPayload,
)

__operation_boundary__ = "presentation"


class FailClosedGateResolver:
    """Default resolver: operations with declared gates stay blocked."""

    async def resolve(
        self,
        *,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        classification: OperationClassification,
    ) -> GateResolution:
        del request, classification
        if descriptor.required_gate_kinds:
            gate = descriptor.required_gate_kinds[0]
            return GateResolution(
                allowed=False,
                gate_kind=gate,
                safe_summary=f"Needs {gate.value}; no external change was made.",
            )
        return GateResolution(allowed=True)


class OperationGateway:
    """Normalize, classify, gate, execute-or-prepare, dispose, and ledger."""

    __slots__ = ("_classifier", "_descriptors", "_gates", "_presentation")

    def __init__(
        self,
        *,
        descriptors: OperationDescriptorRegistry,
        classifier: OperationClassifier | None = None,
        gates: OperationGateResolver | None = None,
        presentation: type[
            PresentationDispositionPolicy
        ] = PresentationDispositionPolicy,
    ) -> None:
        self._descriptors = descriptors
        self._classifier = classifier or OperationClassifier(descriptors=descriptors)
        self._gates = gates or FailClosedGateResolver()
        self._presentation = presentation

    async def invoke(
        self,
        request: OperationRequest,
        adapter: OperationAdapter,
    ) -> OperationDisposition:
        """Run once per ``operation_id``/digest; never expose an apply seam."""

        context = OperationContext.require()
        if (
            context.mode is OperationGatewayMode.ENFORCE
            and not context.canonical_arguments_durable
        ):
            raise OperationEnforcementNotReadyError
        return await context.invocations.invoke_once(
            operation_id=request.operation_id,
            args_digest=request.args_digest,
            invoke=lambda: self._invoke_validated(request, adapter),
        )

    async def _invoke_validated(
        self,
        request: OperationRequest,
        adapter: OperationAdapter,
    ) -> OperationDisposition:
        self._validate_request(request)
        return await self._invoke_once(request, adapter)

    async def _invoke_once(
        self,
        request: OperationRequest,
        adapter: OperationAdapter,
    ) -> OperationDisposition:
        context = OperationContext.require()
        started = time.perf_counter()
        descriptor_entry = self._descriptors.resolve_entry(
            request.capability, request.op
        )
        resolved_descriptor = self._descriptors.resolve(request.capability, request.op)
        descriptor = (
            descriptor_entry.descriptor
            if descriptor_entry is not None
            else resolved_descriptor
            if resolved_descriptor is not None
            else self._descriptors.safe_default(
                capability=request.capability,
                op=request.op,
            ).descriptor
        )
        annotations = McpToolAnnotationsRegistry.get(request.capability, request.op)
        classification = self._classifier.classify(
            request,
            annotations=annotations,
        )
        metric_capability, metric_op = self._descriptors.metric_key(
            request.capability, request.op
        )
        effective_descriptor = descriptor.model_copy(
            update={"effect_class": classification.effect_class}
        )
        await self._emit_requested(request)
        await self._emit_classified(request, classification)
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

        try:
            gate = await self._gates.resolve(
                request=request,
                descriptor=effective_descriptor,
                classification=classification,
            )
            if not gate.allowed:
                disposition = OperationDisposition(
                    operation_id=request.operation_id,
                    outcome=OperationOutcome.BLOCKED,
                    artifact_ids=(),
                    stage_ids=(),
                    activity_ref=None,
                    agent_summary=gate.safe_summary
                    or "Needs access; no external change was made.",
                    retryable=False,
                )
                await self._complete(
                    request=request,
                    disposition=disposition,
                    result_ref=None,
                    started=started,
                    classification=classification,
                    metric_capability=metric_capability,
                    metric_op=metric_op,
                )
                return disposition

            with OperationContext.operation_scope(request.operation_id):
                if classification.effect_class in {
                    EffectClass.NONE,
                    EffectClass.INTERNAL_REVERSIBLE,
                }:
                    raw_result = await adapter.execute_read(request)
                    proposed = None
                else:
                    context.metrics.stage_required(
                        capability=metric_capability,
                        op=metric_op,
                        effect_class=classification.effect_class.value,
                    )
                    proposed = await adapter.build_proposal(request)
                    raw_result = None

            if (
                raw_result is not None
                and raw_result.result_ref is not None
                and raw_result.result_payload is not None
                and raw_result.latency_ms is not None
            ):
                # Presentation is a generic, best-effort operation outcome
                # hand-off. Transport adapters return neutral facts only; the
                # gateway owns construction of the presentation contract.
                await _GatewayPresentationContext.require().outcome_presenter.present(
                    OperationPresentationOutcome(
                        operation_id=request.operation_id,
                        capability=request.capability,
                        op=request.op,
                        result_ref=raw_result.result_ref,
                        output=raw_result.result_payload,
                        latency_ms=raw_result.latency_ms,
                    )
                )

            result_summary = self._result_summary(
                raw_result=raw_result,
                proposed=proposed,
            )
            # The decision is intentionally computed even though A3 does not
            # mount a second surface.  Shadow comparison uses the same policy.
            self._presentation.plan(
                request,
                effective_descriptor,
                result_summary,
            )
            artifact_ids = await self._persist_artifact(
                request=request,
                adapter=adapter,
                raw_result=raw_result,
                proposed=proposed,
            )
            disposition = self._disposition(
                request=request,
                raw_result=raw_result,
                proposed=proposed,
                artifact_ids=artifact_ids,
                display_name=(
                    descriptor_entry.display_name
                    if descriptor_entry is not None
                    else "connector operation"
                    if resolved_descriptor is not None
                    else "operation"
                ),
            )
            await self._complete(
                request=request,
                disposition=disposition,
                result_ref=result_summary.result_ref,
                started=started,
                classification=classification,
                metric_capability=metric_capability,
                metric_op=metric_op,
            )
            return disposition
        except asyncio.CancelledError:
            cancelled = OperationDisposition(
                operation_id=request.operation_id,
                outcome=OperationOutcome.CANCELLED,
                artifact_ids=(),
                stage_ids=(),
                activity_ref=None,
                agent_summary="Operation was cancelled; no external change was made.",
                retryable=True,
            )
            await self._complete(
                request=request,
                disposition=cancelled,
                result_ref=None,
                started=started,
                classification=classification,
                metric_capability=metric_capability,
                metric_op=metric_op,
            )
            raise
        except Exception as exc:  # safe failure becomes a disposition
            failure_code, retryable = self._safe_failure(exc)
            await self._emit_failed(
                request=request,
                failure_code=failure_code,
                retryable=retryable,
            )
            context.metrics.failed(
                capability=metric_capability,
                op=metric_op,
                effect_class=classification.effect_class.value,
                failure_code=failure_code,
            )
            return OperationDisposition(
                operation_id=request.operation_id,
                outcome=OperationOutcome.FAILED,
                artifact_ids=(),
                stage_ids=(),
                activity_ref=None,
                agent_summary="Operation failed; no external change was made.",
                retryable=retryable,
            )

    @staticmethod
    def _validate_request(request: OperationRequest) -> None:
        context = OperationContext.require()
        if request.run_id != context.identity.run_id:
            raise OperationIdentityMismatchError
        stored = context.arguments.get(request.canonical_args_ref)
        if stored is None:
            raise OperationArgumentsMissingError
        digest, canonical_bytes = stored
        if (
            digest != request.args_digest
            or sha256_hex(canonical_bytes) != request.args_digest
        ):
            raise OperationArgumentsDigestMismatchError
        try:
            decoded = json.loads(canonical_bytes)
            is_canonical_object = (
                isinstance(decoded, dict)
                and canonical_json_bytes(decoded) == canonical_bytes
            )
        except (CanonicalJsonError, json.JSONDecodeError, UnicodeDecodeError):
            is_canonical_object = False
        if not is_canonical_object:
            raise OperationArgumentsDigestMismatchError

    @staticmethod
    def _result_summary(
        *,
        raw_result: OperationRawResult | None,
        proposed: ProposedEffect | None,
    ) -> OperationResultSummary:
        if proposed is not None:
            return OperationResultSummary(
                result_ref=proposed.proposal_ref,
                safe_summary=proposed.safe_summary,
                has_artifact_source=proposed.artifact_source_ref is not None,
                is_proposal=True,
            )
        if raw_result is None:  # pragma: no cover - branch construction invariant
            raise RuntimeError("operation adapter produced no result")
        return OperationResultSummary(
            result_ref=raw_result.result_ref,
            safe_summary=raw_result.safe_summary,
            has_artifact_source=raw_result.result_ref is not None,
            is_proposal=False,
        )

    @staticmethod
    async def _persist_artifact(
        *,
        request: OperationRequest,
        adapter: OperationAdapter,
        raw_result: OperationRawResult | None,
        proposed: ProposedEffect | None,
    ) -> tuple[str, ...]:
        intent = request.artifact_intent
        if intent is None:
            return ()
        context = OperationContext.require()
        service = _GatewayPresentationContext.require().artifact_service
        if service is None:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ARTIFACT_FAILED,
                "Artifact service is unavailable.",
                retryable=True,
            )
        publication = await OperationGateway._artifact_publication(adapter, request)
        if publication is not None:
            mutation = await OperationGateway._publish_authored_content(
                request=request,
                service=service,
                publication=publication,
            )
            return (mutation.record.artifact.artifact_id,)
        source_ref = (
            raw_result.result_ref
            if raw_result is not None
            else proposed.artifact_source_ref
            if proposed is not None
            else None
        )
        if source_ref is None:
            raise OperationGatewayError(
                OperationGatewayErrorCode.ARTIFACT_FAILED,
                "Artifact source is unavailable.",
            )
        mutation = await service.promote_source(
            org_id=context.identity.org_id,
            user_id=context.identity.user_id,
            request=ArtifactPromotionRequest(
                run_id=context.identity.run_id,
                source_ref=source_ref,
                kind=intent.kind,
                title=intent.title,
                media_type=intent.media_type,
                suggested_filename=intent.suggested_filename,
                idempotency_key=request.operation_id,
            ),
        )
        artifact_id = mutation.record.artifact.artifact_id
        return (artifact_id,)

    @staticmethod
    async def _artifact_publication(
        adapter: OperationAdapter,
        request: OperationRequest,
    ) -> ArtifactPublicationSource | None:
        publisher = getattr(adapter, "artifact_publication", None)
        if not callable(publisher):
            return None
        result = await publisher(request)
        if result is not None and not isinstance(result, ArtifactPublicationSource):
            raise OperationGatewayError(
                OperationGatewayErrorCode.ADAPTER_FAILED,
                "Artifact publication adapter returned an invalid source.",
            )
        return result

    @staticmethod
    async def _publish_authored_content(
        *,
        request: OperationRequest,
        service: object,
        publication: ArtifactPublicationSource,
    ) -> object:
        intent = request.artifact_intent
        if intent is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("artifact publication requires artifact intent")
        context = OperationContext.require()
        author = (
            ArtifactAuthor.SUBAGENT
            if request.producer.value == "subagent"
            else ArtifactAuthor.MODEL
            if request.producer.value == "model"
            else ArtifactAuthor.SYSTEM
        )
        create_request = ArtifactCreateRequest(
            run_id=context.identity.run_id,
            kind=intent.kind,
            title=intent.title or "Untitled artifact",
            media_type=intent.media_type or "application/octet-stream",
            suggested_filename=intent.suggested_filename,
            presentation_preference=intent.presentation_preference,
            idempotency_key=request.operation_id,
        )
        if publication.content is not None:
            return await service.publish_from_bytes(
                org_id=context.identity.org_id,
                user_id=context.identity.user_id,
                request=create_request,
                provenance=ArtifactProvenance(
                    author=author,
                    source_ref=publication.source_ref,
                ),
                content=publication.content,
            )
        assert publication.content_ref is not None
        return await service.publish_from_source(
            org_id=context.identity.org_id,
            user_id=context.identity.user_id,
            request=create_request,
            provenance=ArtifactProvenance(
                author=author,
                source_ref=publication.source_ref or publication.content_ref,
            ),
            source_ref=publication.content_ref,
        )

    @staticmethod
    def _disposition(
        *,
        request: OperationRequest,
        raw_result: OperationRawResult | None,
        proposed: ProposedEffect | None,
        artifact_ids: tuple[str, ...],
        display_name: str,
    ) -> OperationDisposition:
        if proposed is not None:
            summary = f"Proposed {display_name}; waiting for review (stage_id={proposed.stage_id})."
            return OperationDisposition(
                operation_id=request.operation_id,
                outcome=OperationOutcome.STAGED,
                artifact_ids=artifact_ids,
                stage_ids=(proposed.stage_id,),
                activity_ref=proposed.activity_ref,
                agent_summary=summary,
                retryable=False,
            )
        if artifact_ids:
            intent = request.artifact_intent
            title = intent.title if intent is not None and intent.title else "Untitled"
            kind = intent.kind.value if intent is not None else "file"
            summary = (
                f"Created {kind} artifact '{title}' (artifact_id={artifact_ids[0]})."
            )
        else:
            summary = f"Completed {display_name}."
        return OperationDisposition(
            operation_id=request.operation_id,
            outcome=OperationOutcome.SUCCEEDED,
            artifact_ids=artifact_ids,
            stage_ids=(),
            activity_ref=raw_result.activity_ref if raw_result is not None else None,
            agent_summary=summary,
            retryable=False,
        )

    @staticmethod
    async def _emit_requested(request: OperationRequest) -> None:
        payload = OperationRequestedPayload(
            v=1,
            operation_id=request.operation_id,
            producer=request.producer,
            capability=request.capability,
            op=request.op,
            args_digest=request.args_digest,
            parent_operation_id=request.parent_operation_id,
        )
        await _GatewayPresentationContext.require().ledger_emitter.emit(
            LedgerEventType.OPERATION_REQUESTED,
            payload.model_dump(mode="json", exclude_none=True),
        )

    @staticmethod
    async def _emit_classified(
        request: OperationRequest, classification: OperationClassification
    ) -> None:
        payload = OperationClassifiedPayload(
            v=1,
            operation_id=request.operation_id,
            effect_class=classification.effect_class,
            basis=classification.basis,
            confidence=classification.confidence,
        )
        await _GatewayPresentationContext.require().ledger_emitter.emit(
            LedgerEventType.OPERATION_CLASSIFIED,
            payload.model_dump(mode="json"),
        )

    @staticmethod
    async def _emit_failed(
        *,
        request: OperationRequest,
        failure_code: str,
        retryable: bool,
    ) -> None:
        payload = OperationFailedPayload(
            v=1,
            operation_id=request.operation_id,
            failure_code=failure_code,
            retryable=retryable,
        )
        await _GatewayPresentationContext.require().ledger_emitter.emit(
            LedgerEventType.OPERATION_FAILED,
            payload.model_dump(mode="json"),
        )

    @staticmethod
    async def _complete(
        *,
        request: OperationRequest,
        disposition: OperationDisposition,
        result_ref: str | None,
        started: float,
        classification: OperationClassification,
        metric_capability: str,
        metric_op: str,
    ) -> None:
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        payload = OperationCompletedPayload(
            v=1,
            operation_id=request.operation_id,
            outcome=disposition.outcome,
            result_ref=result_ref,
            latency_ms=latency_ms,
        )
        context = OperationContext.require()
        await _GatewayPresentationContext.require().ledger_emitter.emit(
            LedgerEventType.OPERATION_COMPLETED,
            payload.model_dump(mode="json", exclude_none=True),
        )
        context.metrics.completed(
            capability=metric_capability,
            op=metric_op,
            effect_class=classification.effect_class.value,
            outcome=disposition.outcome.value,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, bool]:
        if isinstance(exc, OperationGatewayError):
            return exc.code.value, exc.retryable
        if isinstance(exc, ArtifactError):
            return exc.code.value, exc.retryable
        return OperationGatewayErrorCode.ADAPTER_FAILED.value, False


__all__ = ("FailClosedGateResolver", "OperationGateway")
