from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from agent_runtime.artifacts.contracts import ArtifactPromotionRequest
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    ArtifactIntent,
    GateResolution,
    OperationAdapter,
    OperationGatewayMode,
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorEntry,
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.errors import (
    OperationArgumentsDigestMismatchError,
    OperationEnforcementNotReadyError,
    OperationIdempotencyConflictError,
    OperationIdentityMismatchError,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.surfaces_v2.canonical_json import sha256_hex
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactIdCodec,
    EffectStageIdCodec,
    ProposalUriCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactKind,
    ArtifactPresentationPreference,
    EffectClass,
    GateKind,
    LedgerEventType,
    OperationOutcome,
)
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    AllowingGate,
    BoundContextMixin,
    RecordingAdapter,
    RecordingArtifactService,
    RecordingEmitter,
    RecordingMetrics,
    effect_descriptor_values,
)


def _registry(effect: EffectClass) -> OperationDescriptorRegistry:
    return OperationDescriptorRegistry(
        (
            OperationDescriptorEntry(
                descriptor=effect_descriptor_values(effect),
                descriptor_version="test-v1",
                display_name=f"{effect.value} test",
                timeout_ms=1_000,
            ),
        ),
        action_catalog=None,
    )


def _proposal() -> ProposedEffect:
    stage_id = EffectStageIdCodec.format(uuid4())
    return ProposedEffect(
        stage_id=stage_id,
        proposal_ref=ProposalUriCodec.format(stage_id, 1),
        safe_summary="Prepared safely.",
        activity_ref="activity://proposal-1",
        artifact_source_ref="payload://proposal-source",
    )


@dataclass
class BlockingGate:
    async def resolve(self, **_kwargs: object) -> GateResolution:
        return GateResolution(
            allowed=False,
            gate_kind=GateKind.POLICY,
            safe_summary="Needs approval; no external change was made.",
        )


@dataclass
class ProposalOnlyAdapter:
    proposal: ProposedEffect
    calls: int = 0

    async def build_proposal(self, _request: object) -> ProposedEffect:
        self.calls += 1
        return self.proposal


@dataclass
class CorruptibleArgumentResolver:
    entries: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    def put(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None:
        self.entries[ref] = (digest, canonical_bytes)

    def get(self, ref: str) -> tuple[str, bytes] | None:
        return self.entries.get(ref)

    def corrupt_bytes(self, ref: str) -> None:
        digest, _ = self.entries[ref]
        self.entries[ref] = (digest, b'{"tampered":true}')

    def replace_with_noncanonical_bytes(self, ref: str) -> str:
        noncanonical = b'{ "value": 1 }'
        digest = sha256_hex(noncanonical)
        self.entries[ref] = (digest, noncanonical)
        return digest


class TestOperationGateway(BoundContextMixin):
    @staticmethod
    def _request(
        effect: EffectClass,
        *,
        intent: ArtifactIntent | None = None,
        operation_id: str | None = None,
        arguments: dict[str, object] | None = None,
    ):
        return OperationRequestFactory.create(
            capability="test",
            op=effect.value,
            arguments=arguments or {"value": 1},
            artifact_intent=intent,
            operation_id=operation_id,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "effect",
        [EffectClass.NONE, EffectClass.INTERNAL_REVERSIBLE],
    )
    async def test_non_external_effect_executes_read_only(
        self, effect: EffectClass
    ) -> None:
        emitter = RecordingEmitter()
        metrics = RecordingMetrics()
        token = self.bind(
            emitter=emitter,
            metrics=metrics,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter()
        try:
            result = await OperationGateway(
                descriptors=_registry(effect),
                gates=AllowingGate(),
            ).invoke(self._request(effect), adapter)
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.SUCCEEDED
        assert adapter.read_calls == 1
        assert adapter.proposal_calls == 0
        assert adapter.apply_calls == 0
        assert [event[0] for event in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
            LedgerEventType.OPERATION_COMPLETED,
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "effect",
        [
            EffectClass.EXTERNAL_REVERSIBLE,
            EffectClass.EXTERNAL_DESTRUCTIVE,
            EffectClass.UNKNOWN,
        ],
    )
    async def test_external_and_unknown_only_build_proposal(
        self, effect: EffectClass
    ) -> None:
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = ProposalOnlyAdapter(_proposal())
        try:
            result = await OperationGateway(
                descriptors=_registry(effect),
                gates=AllowingGate(),
            ).invoke(self._request(effect), adapter)  # type: ignore[arg-type]
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.STAGED
        assert result.stage_ids == (adapter.proposal.stage_id,)
        assert adapter.calls == 1
        assert "apply" not in OperationAdapter.__dict__
        assert not hasattr(adapter, "apply")

    @pytest.mark.asyncio
    async def test_explicit_artifact_intent_streams_only_from_source_ref(
        self,
    ) -> None:
        artifact_id = ArtifactIdCodec.format(uuid4())
        artifact_service = RecordingArtifactService(artifact_id)
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            artifact_service=artifact_service,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        intent = ArtifactIntent(
            kind=ArtifactKind.DOCUMENT,
            title="Run notes",
            media_type="text/markdown",
            suggested_filename="notes.md",
            presentation_preference=ArtifactPresentationPreference.CANVAS,
        )
        adapter = RecordingAdapter(
            raw=OperationRawResult(
                result_ref="payload://immutable-source",
                safe_summary="Source ready.",
            )
        )
        try:
            result = await OperationGateway(
                descriptors=_registry(EffectClass.NONE),
                gates=AllowingGate(),
            ).invoke(self._request(EffectClass.NONE, intent=intent), adapter)
        finally:
            OperationContext.unbind(token)

        assert result.artifact_ids == (artifact_id,)
        assert len(artifact_service.calls) == 1
        call = artifact_service.calls[0]
        assert set(call) == {"org_id", "user_id", "request"}
        request = call["request"]
        assert isinstance(request, ArtifactPromotionRequest)
        assert request.source_ref == "payload://immutable-source"
        assert request.kind is ArtifactKind.DOCUMENT
        assert "content" not in repr(call)
        assert "bytes" not in repr(call)

    @pytest.mark.asyncio
    async def test_code_fence_without_typed_intent_never_creates_artifact(
        self,
    ) -> None:
        artifact_service = RecordingArtifactService(ArtifactIdCodec.format(uuid4()))
        token = self.bind(
            artifact_service=artifact_service,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter(
            raw=OperationRawResult(
                result_ref="payload://text-result",
                safe_summary="```python\nprint('still text')\n```",
            )
        )
        try:
            await OperationGateway(
                descriptors=_registry(EffectClass.NONE),
                gates=AllowingGate(),
            ).invoke(self._request(EffectClass.NONE), adapter)
        finally:
            OperationContext.unbind(token)

        assert artifact_service.calls == []

    @pytest.mark.asyncio
    async def test_blocked_gate_does_not_touch_adapter(self) -> None:
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter(proposal=_proposal())
        try:
            result = await OperationGateway(
                descriptors=_registry(EffectClass.EXTERNAL_REVERSIBLE),
                gates=BlockingGate(),
            ).invoke(
                self._request(EffectClass.EXTERNAL_REVERSIBLE),
                adapter,
            )
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.BLOCKED
        assert adapter.read_calls == adapter.proposal_calls == adapter.apply_calls == 0

    @pytest.mark.asyncio
    async def test_adapter_failure_is_safe_and_never_leaks_exception_text(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter(failure=RuntimeError("provider-token=secret-value"))
        try:
            result = await OperationGateway(
                descriptors=_registry(EffectClass.NONE),
                gates=AllowingGate(),
            ).invoke(self._request(EffectClass.NONE), adapter)
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.FAILED
        assert "secret-value" not in result.agent_summary
        assert "secret-value" not in repr(emitter.events)
        assert emitter.events[-1][0] is LedgerEventType.OPERATION_FAILED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fail_on_call", [1, 2, 3])
    async def test_emitter_failure_at_every_gateway_event_is_fail_soft(
        self,
        fail_on_call: int,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        emitter = RecordingEmitter(fail_on_call=fail_on_call)
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter()
        try:
            result = await OperationGateway(
                descriptors=_registry(EffectClass.NONE),
                gates=AllowingGate(),
            ).invoke(self._request(EffectClass.NONE), adapter)
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.SUCCEEDED
        assert adapter.read_calls == 1
        assert emitter.calls == 3
        assert "telemetry-secret-must-not-escape" not in caplog.text

    @pytest.mark.asyncio
    async def test_metric_failure_is_fail_soft_and_not_logged_verbatim(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        metrics = RecordingMetrics(fail=True)
        token = self.bind(
            metrics=metrics,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter()
        try:
            result = await OperationGateway(
                descriptors=_registry(EffectClass.NONE),
                gates=AllowingGate(),
            ).invoke(self._request(EffectClass.NONE), adapter)
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.SUCCEEDED
        assert adapter.read_calls == 1
        assert "metric-secret-must-not-escape" not in caplog.text

    @pytest.mark.asyncio
    async def test_cancellation_is_ledgered_then_rethrown(self) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter(failure=asyncio.CancelledError())
        try:
            with pytest.raises(asyncio.CancelledError):
                await OperationGateway(
                    descriptors=_registry(EffectClass.NONE),
                    gates=AllowingGate(),
                ).invoke(self._request(EffectClass.NONE), adapter)
        finally:
            OperationContext.unbind(token)

        assert emitter.events[-1][0] is LedgerEventType.OPERATION_COMPLETED
        assert emitter.events[-1][1]["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancellation_is_not_replaced_by_telemetry_failure(self) -> None:
        emitter = RecordingEmitter(fail_on_call=3)
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter(failure=asyncio.CancelledError())
        try:
            with pytest.raises(asyncio.CancelledError):
                await OperationGateway(
                    descriptors=_registry(EffectClass.NONE),
                    gates=AllowingGate(),
                ).invoke(self._request(EffectClass.NONE), adapter)
        finally:
            OperationContext.unbind(token)

        assert emitter.calls == 3
        assert [event[0] for event in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
        ]

    @pytest.mark.asyncio
    async def test_same_operation_is_executed_once_and_digest_conflict_fails(
        self,
    ) -> None:
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        adapter = RecordingAdapter()
        gateway = OperationGateway(
            descriptors=_registry(EffectClass.NONE),
            gates=AllowingGate(),
        )
        request = self._request(EffectClass.NONE)
        try:
            first, second = await asyncio.gather(
                gateway.invoke(request, adapter),
                gateway.invoke(request, adapter),
            )
            with pytest.raises(OperationIdempotencyConflictError):
                await gateway.invoke(
                    request.model_copy(update={"args_digest": "0" * 64}),
                    adapter,
                )
        finally:
            OperationContext.unbind(token)

        assert first == second
        assert adapter.read_calls == 1

    @pytest.mark.asyncio
    async def test_request_identity_is_verified_against_bound_run(self) -> None:
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        gateway = OperationGateway(
            descriptors=_registry(EffectClass.NONE),
            gates=AllowingGate(),
        )
        request = self._request(EffectClass.NONE)
        try:
            with pytest.raises(OperationIdentityMismatchError):
                await gateway.invoke(
                    request.model_copy(update={"run_id": "other-run"}),
                    RecordingAdapter(),
                )
        finally:
            OperationContext.unbind(token)

    @pytest.mark.asyncio
    async def test_stored_canonical_bytes_are_verified_against_digest(self) -> None:
        arguments = CorruptibleArgumentResolver()
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
            arguments=arguments,
        )
        adapter = RecordingAdapter()
        request = self._request(EffectClass.NONE)
        arguments.corrupt_bytes(request.canonical_args_ref)
        try:
            with pytest.raises(OperationArgumentsDigestMismatchError):
                await OperationGateway(
                    descriptors=_registry(EffectClass.NONE),
                    gates=AllowingGate(),
                ).invoke(request, adapter)
        finally:
            OperationContext.unbind(token)

        assert adapter.read_calls == 0

    @pytest.mark.asyncio
    async def test_matching_digest_cannot_bless_noncanonical_argument_bytes(
        self,
    ) -> None:
        arguments = CorruptibleArgumentResolver()
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
            arguments=arguments,
        )
        adapter = RecordingAdapter()
        request = self._request(EffectClass.NONE)
        digest = arguments.replace_with_noncanonical_bytes(request.canonical_args_ref)
        try:
            with pytest.raises(OperationArgumentsDigestMismatchError):
                await OperationGateway(
                    descriptors=_registry(EffectClass.NONE),
                    gates=AllowingGate(),
                ).invoke(
                    request.model_copy(update={"args_digest": digest}),
                    adapter,
                )
        finally:
            OperationContext.unbind(token)

        assert adapter.read_calls == 0

    @pytest.mark.asyncio
    async def test_enforce_rejects_run_local_arguments_before_adapter(
        self,
    ) -> None:
        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=False,
        )
        adapter = RecordingAdapter()
        try:
            request = self._request(EffectClass.NONE)
            with pytest.raises(OperationEnforcementNotReadyError):
                await OperationGateway(
                    descriptors=_registry(EffectClass.NONE),
                    gates=AllowingGate(),
                ).invoke(request, adapter)
        finally:
            OperationContext.unbind(token)

        assert adapter.read_calls == adapter.proposal_calls == 0
