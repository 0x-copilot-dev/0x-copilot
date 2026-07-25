"""Adversarial tests for the gateway-owned desktop browser adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionPlan,
    BrowserApplyOutcome,
    BrowserApplyReceipt,
    BrowserArtifactPayload,
    BrowserPrepareResult,
    BrowserReadRequest,
    BrowserReadResult,
    BrowserUploadArtifact,
)
from agent_runtime.capabilities.browser.operation_adapter import BrowserOperationAdapter
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationGatewayMode,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorEntry,
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.surfaces_v2.canonical_json import sha256_hex
from agent_runtime.surfaces_v2.entities import (
    ArtifactIntent,
    OperationDescriptor,
    OperationRequest,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactKind,
    ArtifactPresentationPreference,
    EffectClass,
    EffectExecutorKind,
    OperationResultKind,
)
from tests.unit.agent_runtime.capabilities.operations.helpers import BoundContextMixin


class _Bridge:
    def __init__(self, *, payload: BrowserArtifactPayload | None = None) -> None:
        self.payload = payload
        self.reads: list[BrowserReadRequest] = []
        self.prepare_calls = 0
        self.apply_calls = 0

    async def execute_read(self, request: BrowserReadRequest) -> BrowserReadResult:
        self.reads.append(request)
        return BrowserReadResult(
            safe_summary="Browser read completed.",
            result_ref=f"payload://browser/{request.operation_id}",
            activity_ref=f"activity://browser/{request.operation_id}",
        )

    async def artifact_payload(
        self, *, operation_id: str
    ) -> BrowserArtifactPayload | None:
        del operation_id
        return self.payload

    async def prepare_action(self, plan: BrowserActionPlan) -> BrowserPrepareResult:
        del plan
        self.prepare_calls += 1
        return BrowserPrepareResult(
            prepared_ref="browser-prepared://session/one",
            observed_precondition_digest="a" * 64,
        )

    async def apply_prepared(self, prepared_ref: str) -> BrowserApplyReceipt:
        del prepared_ref
        self.apply_calls += 1
        return BrowserApplyReceipt(outcome=BrowserApplyOutcome.APPLIED)

    async def reconcile_action(self, prepared_ref: str) -> BrowserApplyReceipt:
        del prepared_ref
        return BrowserApplyReceipt(outcome=BrowserApplyOutcome.INDETERMINATE)


class _Stager:
    def __init__(self) -> None:
        self.requests: list[OperationRequest] = []
        self.plans: list[BrowserActionPlan] = []

    async def stage(
        self, *, request: OperationRequest, plan: BrowserActionPlan
    ) -> ProposedEffect:
        self.requests.append(request)
        self.plans.append(plan)
        return ProposedEffect(
            stage_id="stg_00000000-0000-4000-8000-000000000001",
            proposal_ref="proposal://stg_00000000-0000-4000-8000-000000000001/revisions/1",
            safe_summary="Browser action is waiting for review.",
        )


@dataclass
class _ArtifactService:
    artifact_id: str
    calls: list[dict[str, object]] = field(default_factory=list)

    async def publish_from_bytes(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(
            record=SimpleNamespace(
                artifact=SimpleNamespace(artifact_id=self.artifact_id)
            )
        )


@dataclass
class _AllowingGate:
    async def resolve(self, **_kwargs: object) -> GateResolution:
        return GateResolution(allowed=True)


@dataclass
class _UploadAuthorizer:
    seen: list[tuple[str, ...]] = field(default_factory=list)

    async def authorize(
        self,
        *,
        request: OperationRequest,
        artifact_refs: tuple[str, ...],
    ) -> tuple[BrowserUploadArtifact, ...]:
        del request
        self.seen.append(artifact_refs)
        return tuple(
            BrowserUploadArtifact(
                artifact_ref=reference,
                digest="c" * 64,
                byte_size=42,
                media_type="application/pdf",
                suggested_filename="report.pdf",
            )
            for reference in artifact_refs
        )


def _artifact_intent() -> ArtifactIntent:
    return ArtifactIntent(
        kind=ArtifactKind.FILE,
        title="Downloaded report",
        media_type="application/pdf",
        suggested_filename="report.pdf",
        presentation_preference=ArtifactPresentationPreference.CANVAS,
    )


def _click_args() -> dict[str, object]:
    return {
        "session_ref": "browser-session://ses_123",
        "page_ref": "browser-page://pg_123",
        "origin": "https://example.com",
        "top_level_origin": "https://example.com",
        "element_ref": "e4_2",
        "element_fingerprint": "a" * 64,
        "page_generation": 4,
        "form_fingerprint": "d" * 64,
        "form_payload_digest": "e" * 64,
        "form_action_url": "https://example.com/send",
        "method": "POST",
    }


async def test_read_crosses_the_private_bridge_once_without_a_surface() -> None:
    token = BoundContextMixin.bind()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_snapshot",
            arguments={"depth": 3},
        )
        bridge = _Bridge()
        adapter = BrowserOperationAdapter(bridge=bridge, stager=_Stager())
        result = await adapter.execute_read(request)
    finally:
        OperationContext.unbind(token)

    assert len(bridge.reads) == 1
    assert bridge.reads[0].arguments == {"depth": 3}
    assert result.safe_summary == "Browser read completed."
    assert result.result_ref == f"payload://browser/{request.operation_id}"


async def test_download_is_an_exact_internal_artifact_not_a_host_path() -> None:
    bytes_ = b"browser download bytes"
    payload = BrowserArtifactPayload(
        content=bytes_,
        digest=sha256_hex(bytes_),
        byte_size=len(bytes_),
        media_type="application/pdf",
        suggested_filename="report.pdf",
        source_ref="payload://browser/download-1",
    )
    token = BoundContextMixin.bind()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_download",
            arguments={"ref": "e2_1"},
            artifact_intent=_artifact_intent(),
        )
        bridge = _Bridge(payload=payload)
        adapter = BrowserOperationAdapter(bridge=bridge, stager=_Stager())
        await adapter.execute_read(request)
        published = await adapter.artifact_publication(request)
    finally:
        OperationContext.unbind(token)

    assert published is not None
    assert published.content == bytes_
    assert published.content_ref is None
    assert published.source_ref == "payload://browser/download-1"
    assert "Users" not in repr(payload)


async def test_gateway_publishes_download_bytes_with_browser_provenance() -> None:
    bytes_ = b"browser download bytes"
    payload = BrowserArtifactPayload(
        content=bytes_,
        digest=sha256_hex(bytes_),
        byte_size=len(bytes_),
        media_type="application/pdf",
        suggested_filename="report.pdf",
        source_ref="payload://browser/download-1",
    )
    artifact_service = _ArtifactService(artifact_id=f"art_{uuid4()}")
    token = BoundContextMixin.bind(
        artifact_service=artifact_service,
        mode=OperationGatewayMode.ENFORCE,
        durable_arguments=True,
    )
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_download",
            arguments={"ref": "e2_1"},
            artifact_intent=_artifact_intent(),
        )
        adapter = BrowserOperationAdapter(
            bridge=_Bridge(payload=payload),
            stager=_Stager(),
        )
        registry = OperationDescriptorRegistry(
            (
                OperationDescriptorEntry(
                    descriptor=OperationDescriptor(
                        capability="desktop-browser",
                        op="browser_download",
                        executor=EffectExecutorKind.BROWSER,
                        effect_class=EffectClass.INTERNAL_REVERSIBLE,
                        result_kind=OperationResultKind.ARTIFACT_AND_ACTIVITY,
                        supports_prepare=False,
                        supports_reconcile=False,
                        required_gate_kinds=(),
                        max_inline_result_bytes=0,
                    ),
                    descriptor_version="test-v1",
                    display_name="browser download",
                    timeout_ms=1_000,
                ),
            ),
            action_catalog=None,
        )
        result = await OperationGateway(
            descriptors=registry,
            gates=_AllowingGate(),
        ).invoke(request, adapter)
    finally:
        OperationContext.unbind(token)

    assert result.artifact_ids == (artifact_service.artifact_id,)
    assert len(artifact_service.calls) == 1
    call = artifact_service.calls[0]
    assert call["content"] == bytes_
    assert call["provenance"].source_ref == "payload://browser/download-1"
    assert "path" not in repr(call)


async def test_unknown_click_stages_exact_plan_with_zero_browser_dispatch() -> None:
    token = BoundContextMixin.bind()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_click",
            arguments=_click_args(),
        )
        bridge = _Bridge()
        stager = _Stager()
        adapter = BrowserOperationAdapter(bridge=bridge, stager=stager)
        proposal = await adapter.build_proposal(request)
    finally:
        OperationContext.unbind(token)

    assert proposal.stage_id.startswith("stg_")
    assert bridge.reads == []
    assert bridge.prepare_calls == 0
    assert bridge.apply_calls == 0
    plan = stager.plans[0]
    assert plan.action_kind.value == "click"
    assert plan.origin == "https://example.com"
    assert plan.fields_digest == request.args_digest
    assert plan.canonical_fields_ref == request.canonical_args_ref
    assert plan.form_payload_digest == "e" * 64
    assert "POST" in plan.method


async def test_submit_without_form_payload_digest_never_stages_or_dispatches() -> None:
    args = _click_args()
    args.pop("form_payload_digest")
    token = BoundContextMixin.bind()
    bridge = _Bridge()
    stager = _Stager()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_submit",
            arguments=args,
        )
        with pytest.raises(ValueError, match="form identity"):
            await BrowserOperationAdapter(
                bridge=bridge,
                stager=stager,
            ).build_proposal(request)
    finally:
        OperationContext.unbind(token)

    assert stager.plans == []
    assert bridge.reads == []
    assert bridge.prepare_calls == 0
    assert bridge.apply_calls == 0


async def test_upload_submit_requires_authorized_artifact_revisions() -> None:
    artifact_ref = "artifact://art_00000000-0000-4000-8000-000000000001/revisions/1"
    args = _click_args() | {
        "upload_artifact_refs": [artifact_ref],
        # Caller-supplied metadata is deliberately ignored. Only the A2
        # authorizer below is allowed to bind a filename/digest/size.
        "upload_artifacts": ["/Users/attacker/report.pdf"],
    }
    token = BoundContextMixin.bind()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_upload_submit",
            arguments=args,
        )
        bridge = _Bridge()
        stager = _Stager()
        authorizer = _UploadAuthorizer()
        adapter = BrowserOperationAdapter(
            bridge=bridge,
            stager=stager,
            upload_authorizer=authorizer,
        )
        await adapter.build_proposal(request)
    finally:
        OperationContext.unbind(token)

    plan = stager.plans[0]
    assert authorizer.seen == [(artifact_ref,)]
    assert plan.upload_artifact_refs == (artifact_ref,)
    assert plan.upload_artifacts[0].digest == "c" * 64
    assert "/Users/attacker/report.pdf" not in plan.model_dump_json()
    assert bridge.prepare_calls == bridge.apply_calls == 0


async def test_upload_submit_fails_closed_without_an_artifact_authorizer() -> None:
    token = BoundContextMixin.bind()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_upload_submit",
            arguments=_click_args()
            | {
                "upload_artifact_refs": [
                    "artifact://art_00000000-0000-4000-8000-000000000001/revisions/1"
                ]
            },
        )
        stager = _Stager()
        with pytest.raises(RuntimeError, match="authorization is unavailable"):
            await BrowserOperationAdapter(
                bridge=_Bridge(), stager=stager
            ).build_proposal(request)
    finally:
        OperationContext.unbind(token)

    assert stager.plans == []


async def test_browser_action_rejects_missing_exact_page_identity_before_staging() -> (
    None
):
    token = BoundContextMixin.bind()
    try:
        request = OperationRequestFactory.create(
            capability="desktop-browser",
            op="browser_click",
            arguments={"origin": "https://example.com"},
        )
        bridge = _Bridge()
        stager = _Stager()
        adapter = BrowserOperationAdapter(bridge=bridge, stager=stager)
        with pytest.raises(RuntimeError, match="missing session_ref"):
            await adapter.build_proposal(request)
    finally:
        OperationContext.unbind(token)

    assert stager.plans == []
    assert bridge.reads == []
