"""A1 artifact/operation/effect entities and strict writer validation."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from copilot_service_contracts.work_ledger import (
    load_ledger_golden_events,
    load_ledger_golden_journeys,
    load_work_ledger_contract,
)

from agent_runtime.surfaces_v2.entities import (
    Artifact,
    ArtifactIntent,
    ArtifactRevision,
    EffectDecision,
    EffectExecutionRequest,
    EffectExecutionResult,
    EffectStage,
    EffectTarget,
    OperationDescriptor,
    OperationDisposition,
    OperationRequest,
    ProposalRef,
    SurfaceSubject,
)
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactKind,
    ArtifactPresentationPreference,
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectOutcome,
    EffectStageStatus,
    GateKind,
    LedgerEventType,
    OperationOutcome,
    OperationResultKind,
    Producer,
    SurfaceSubjectType,
    WorkLedgerVocabulary,
)


OPERATION_ID = "op_018f47a6-7b2c-7a10-8f21-123456789abc"
ARTIFACT_ID = "art_123e4567-e89b-42d3-a456-426614174000"
STAGE_ID = "stg_018f47a6-7b2c-7c10-8f21-123456789abc"
DIGEST = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


_ENTITY_MODELS = {
    "OperationRequest": OperationRequest,
    "OperationDescriptor": OperationDescriptor,
    "OperationDisposition": OperationDisposition,
    "Artifact": Artifact,
    "ArtifactRevision": ArtifactRevision,
    "ArtifactIntent": ArtifactIntent,
    "SurfaceSubject": SurfaceSubject,
    "EffectTarget": EffectTarget,
    "ProposalRef": ProposalRef,
    "EffectStage": EffectStage,
    "EffectDecision": EffectDecision,
    "EffectExecutionRequest": EffectExecutionRequest,
    "EffectExecutionResult": EffectExecutionResult,
}


def _all_payload_samples() -> dict[str, dict[str, object]]:
    samples: dict[str, dict[str, object]] = {}
    legacy = load_ledger_golden_events()["events"]
    assert isinstance(legacy, list)
    for event in legacy:
        assert isinstance(event, dict)
        samples.setdefault(str(event["event_type"]), dict(event["payload"]))
    journeys = load_ledger_golden_journeys()["journeys"]
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, dict)
        events = journey["events"]
        assert isinstance(events, list)
        for event in events:
            assert isinstance(event, dict)
            samples.setdefault(str(event["event_type"]), dict(event["payload"]))
    return samples


def test_entity_metadata_matches_pydantic_models() -> None:
    contract = load_work_ledger_contract()
    entities = contract["entities"]
    assert isinstance(entities, dict)
    assert set(entities) == set(_ENTITY_MODELS)
    for name, model in _ENTITY_MODELS.items():
        metadata = entities[name]
        assert isinstance(metadata, dict)
        schema = model.model_json_schema()
        assert set(schema["required"]) == set(metadata["required"]), name
        assert set(schema["properties"]) == set(metadata["required"]) | set(
            metadata["optional"]
        ), name


def test_every_event_sample_is_strict_for_missing_and_unknown_fields() -> None:
    contract = load_work_ledger_contract()
    events = contract["events"]
    assert isinstance(events, dict)
    samples = _all_payload_samples()
    assert set(samples) == set(events)

    for event_type, metadata in events.items():
        assert isinstance(metadata, dict)
        payload = samples[event_type]
        WorkLedgerVocabulary.validate_payload(event_type, payload)

        with pytest.raises(ValidationError):
            WorkLedgerVocabulary.validate_payload(
                event_type, {**payload, "unexpected_contract_field": True}
            )

        for required in metadata["required"]:
            missing = dict(payload)
            missing.pop(required)
            with pytest.raises(ValidationError):
                WorkLedgerVocabulary.validate_payload(event_type, missing)


def test_unknown_enum_values_fail_writer_validation() -> None:
    contract = load_work_ledger_contract()
    events = contract["events"]
    assert isinstance(events, dict)
    samples = _all_payload_samples()
    for event_type, metadata in events.items():
        assert isinstance(metadata, dict)
        enum_fields = metadata.get("enum_fields") or {}
        assert isinstance(enum_fields, Mapping)
        for field in enum_fields:
            payload = {**samples[event_type], str(field): "__future_unknown__"}
            with pytest.raises(ValidationError):
                WorkLedgerVocabulary.validate_payload(event_type, payload)


def test_legacy_compatibility_is_read_side_only() -> None:
    contract = load_work_ledger_contract()
    compatibility = contract["compatibility"]
    assert isinstance(compatibility, dict)
    assert compatibility["read_side_only"] is True
    assert compatibility["legacy_gate_write_input"] is False
    assert (
        WorkLedgerVocabulary.compatibility_event_type("action.classified")
        is LedgerEventType.OPERATION_CLASSIFIED
    )
    assert (
        WorkLedgerVocabulary.compatibility_event_type("write.staged")
        is LedgerEventType.EFFECT_STAGED
    )
    assert (
        WorkLedgerVocabulary.compatibility_event_type("surface.created")
        is LedgerEventType.SURFACE_CREATED
    )
    assert WorkLedgerVocabulary.compatibility_event_type("gate.opened") is None


def test_core_entities_validate_and_keep_bodies_by_reference() -> None:
    intent = ArtifactIntent(
        kind=ArtifactKind.DOCUMENT,
        title="Plan",
        media_type="text/markdown",
        suggested_filename="plan.md",
        presentation_preference=ArtifactPresentationPreference.CANVAS,
    )
    operation = OperationRequest(
        operation_id=OPERATION_ID,
        run_id="run_1",
        producer=Producer.MODEL,
        capability="authoring",
        op="create_document",
        canonical_args_ref=f"operation://{OPERATION_ID}/args",
        args_digest=DIGEST,
        requested_at="2026-07-24T00:00:00Z",
        artifact_intent=intent,
        effect_hint=EffectClass.INTERNAL_REVERSIBLE,
    )
    descriptor = OperationDescriptor(
        capability="workspace",
        op="save_file",
        executor=EffectExecutorKind.WORKSPACE,
        effect_class=EffectClass.EXTERNAL_REVERSIBLE,
        result_kind=OperationResultKind.ARTIFACT_AND_ACTIVITY,
        supports_prepare=True,
        supports_reconcile=True,
        required_gate_kinds=(GateKind.GRANT,),
        max_inline_result_bytes=4096,
    )
    disposition = OperationDisposition(
        operation_id=OPERATION_ID,
        outcome=OperationOutcome.STAGED,
        artifact_ids=(ARTIFACT_ID,),
        stage_ids=(STAGE_ID,),
        activity_ref="activity://run_1/1",
        agent_summary="Prepared one workspace write.",
        retryable=False,
    )
    artifact = Artifact(
        artifact_id=ARTIFACT_ID,
        org_id="org_1",
        user_id="user_1",
        conversation_id="conv_1",
        run_id="run_1",
        kind=ArtifactKind.DOCUMENT,
        title="Plan",
        media_type="text/markdown",
        current_revision=1,
        created_by=ArtifactAuthor.MODEL,
        created_at="2026-07-24T00:00:00Z",
        updated_at="2026-07-24T00:00:00Z",
    )
    revision = ArtifactRevision(
        artifact_id=ARTIFACT_ID,
        revision=1,
        content_ref=f"artifact://{ARTIFACT_ID}/revisions/1",
        content_digest=DIGEST,
        byte_size=12,
        author=ArtifactAuthor.MODEL,
        created_at="2026-07-24T00:00:00Z",
    )
    target = EffectTarget(
        executor=EffectExecutorKind.WORKSPACE,
        capability="workspace",
        op="save_file",
        target_ref="workspace-target://grant_01/pathToken_01",
        precondition_ref="workspace-precondition://snapshot_01",
        display_label="docs/plan.md",
    )
    proposal = ProposalRef(
        proposal_ref=f"proposal://{STAGE_ID}/revisions/1",
        proposal_digest=DIGEST,
        media_type="text/markdown",
        byte_size=12,
    )
    stage = EffectStage(
        stage_id=STAGE_ID,
        operation_id=OPERATION_ID,
        run_id="run_1",
        executor=EffectExecutorKind.WORKSPACE,
        target=target,
        proposal=proposal,
        revision=1,
        status=EffectStageStatus.STAGED,
        policy_snapshot_ref="policy://run_1/1",
        created_at="2026-07-24T00:00:00Z",
        updated_at="2026-07-24T00:00:00Z",
    )
    decision = EffectDecision(
        stage_id=STAGE_ID,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        actor=EffectActor.USER,
        proposal_digest=DIGEST,
        target_digest=DIGEST,
        decided_at="2026-07-24T00:00:01Z",
        ledger_id="rrun·001",
    )
    execution = EffectExecutionRequest(
        stage_id=STAGE_ID,
        revision=1,
        idempotency_key="effect:stage:1",
        target_ref=target.target_ref,
        target_digest=DIGEST,
        proposal_ref=proposal.proposal_ref,
        proposal_digest=DIGEST,
        actor=EffectActor.USER,
        decision_ledger_id=decision.ledger_id,
    )
    result = EffectExecutionResult(
        outcome=EffectOutcome.APPLIED,
        receipt_ref=f"receipt://effects/{STAGE_ID}/claim_01",
        result_digest=DIGEST,
        retryable=False,
    )
    subject = SurfaceSubject(
        subject_type=SurfaceSubjectType.ARTIFACT, subject_id=ARTIFACT_ID
    )

    assert operation.artifact_intent == intent
    assert descriptor.supports_reconcile
    assert disposition.stage_ids == (STAGE_ID,)
    assert artifact.current_revision == revision.revision
    assert stage.proposal == proposal
    assert execution.proposal_digest == decision.proposal_digest
    assert result.outcome is EffectOutcome.APPLIED
    assert subject.subject_id == ARTIFACT_ID


def test_cross_reference_mismatches_fail_closed() -> None:
    other_artifact = "art_018f47a6-7b2c-7b10-8f21-123456789abc"
    with pytest.raises(ValidationError, match="content_ref"):
        ArtifactRevision(
            artifact_id=ARTIFACT_ID,
            revision=1,
            content_ref=f"artifact://{other_artifact}/revisions/1",
            content_digest=DIGEST,
            byte_size=1,
            author=ArtifactAuthor.MODEL,
            created_at="2026-07-24T00:00:00Z",
        )

    staged = _all_payload_samples()["effect.staged"]
    with pytest.raises(ValidationError, match="opaque non-file"):
        WorkLedgerVocabulary.validate_payload(
            "effect.staged", {**staged, "target_ref": "file:///tmp/output.csv"}
        )
    with pytest.raises(ValidationError, match="proposal_ref"):
        WorkLedgerVocabulary.validate_payload(
            "effect.staged",
            {
                **staged,
                "proposal_ref": (
                    "proposal://stg_018f47a6-7b2c-7c10-8f21-123456789abc/revisions/1"
                ),
            },
        )

    promoted = _all_payload_samples()["artifact.promoted"]
    with pytest.raises(ValidationError, match="physical host path"):
        WorkLedgerVocabulary.validate_payload(
            "artifact.promoted",
            {**promoted, "source_ref": "/Users/alice/private.csv"},
        )

    with pytest.raises(ValidationError, match="physical host path"):
        EffectExecutionRequest(
            stage_id=STAGE_ID,
            revision=1,
            idempotency_key="effect:stage:1",
            target_ref="/Users/alice/private.csv",
            target_digest=DIGEST,
            proposal_ref=f"proposal://{STAGE_ID}/revisions/1",
            proposal_digest=DIGEST,
            actor=EffectActor.USER,
            decision_ledger_id="rrun·001",
        )

    claimed = _all_payload_samples()["effect.claimed"]
    with pytest.raises(ValidationError, match="claim_id"):
        WorkLedgerVocabulary.validate_payload(
            "effect.claimed",
            {**claimed, "claim_id": "claim..traversal"},
        )
    with pytest.raises(ValidationError, match="less than or equal"):
        WorkLedgerVocabulary.validate_payload(
            "effect.claimed",
            {**claimed, "revision": 9_007_199_254_740_993},
        )

    reconciled = _all_payload_samples()["effect.reconciled"]
    with pytest.raises(ValidationError, match="claim_id"):
        WorkLedgerVocabulary.validate_payload(
            "effect.reconciled",
            {
                **reconciled,
                "receipt_ref": (
                    f"receipt://effects/{reconciled['stage_id']}/different_claim"
                ),
            },
        )
