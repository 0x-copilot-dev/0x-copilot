"""Strict v2.1 operation/artifact/effect identifier and reference codecs."""

from __future__ import annotations

import pytest

from copilot_service_contracts.work_ledger import load_ledger_contract_vectors

from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    ArtifactEffectFormatError,
    ArtifactIdCodec,
    EffectReceiptRefCodec,
    EffectStageIdCodec,
    OperationArgsRefCodec,
    OperationIdCodec,
    ProposalUriCodec,
    WorkspaceTargetRefCodec,
)


_ID_CODECS = {
    "operation_id": OperationIdCodec,
    "artifact_id": ArtifactIdCodec,
    "effect_stage_id": EffectStageIdCodec,
}

_REF_CODECS = {
    "artifact_content": ArtifactContentRefCodec,
    "operation_args": OperationArgsRefCodec,
    "proposal": ProposalUriCodec,
    "effect_receipt": EffectReceiptRefCodec,
    "workspace_target": WorkspaceTargetRefCodec,
}


def test_identifier_vectors_round_trip() -> None:
    vectors = load_ledger_contract_vectors()["identifiers"]
    assert isinstance(vectors, list)
    for vector in vectors:
        assert isinstance(vector, dict)
        codec = _ID_CODECS[str(vector["kind"])]
        uuid = str(vector["uuid"])
        formatted = str(vector["formatted"])
        assert codec.format(uuid) == formatted
        assert codec.parse(formatted) == uuid


def test_reference_vectors_round_trip() -> None:
    vectors = load_ledger_contract_vectors()["references"]
    assert isinstance(vectors, list)
    for vector in vectors:
        assert isinstance(vector, dict)
        codec = _REF_CODECS[str(vector["kind"])]
        formatted = str(vector["formatted"])
        parts = vector["parts"]
        assert isinstance(parts, dict)
        assert codec.format(**parts) == formatted
        assert codec.parse(formatted).model_dump() == parts


@pytest.mark.parametrize(
    "text",
    [
        "",
        "018f47a6-7b2c-7a10-8f21-123456789abc",
        "op_018F47A6-7B2C-7A10-8F21-123456789ABC",
        "op_018f47a6-7b2c-1a10-8f21-123456789abc",
        "op_018f47a6-7b2c-7a10-7f21-123456789abc",
        " op_018f47a6-7b2c-7a10-8f21-123456789abc",
        "op_018f47a6-7b2c-7a10-8f21-123456789abc ",
    ],
)
def test_operation_id_rejects_noncanonical_or_bare_values(text: str) -> None:
    with pytest.raises(ArtifactEffectFormatError):
        OperationIdCodec.parse(text)


@pytest.mark.parametrize(
    ("codec", "text"),
    [
        (
            ArtifactContentRefCodec,
            "artifact://art_123e4567-e89b-42d3-a456-426614174000/revisions/0",
        ),
        (
            ArtifactContentRefCodec,
            "artifact://art_123e4567-e89b-42d3-a456-426614174000/revisions/1/extra",
        ),
        (
            OperationArgsRefCodec,
            "operation://op_018f47a6-7b2c-7a10-8f21-123456789abc/../args",
        ),
        (
            ProposalUriCodec,
            "proposal://stg_018f47a6-7b2c-7c10-8f21-123456789abc/revisions/-1",
        ),
        (
            EffectReceiptRefCodec,
            "receipt://effects/stg_018f47a6-7b2c-7c10-8f21-123456789abc/../claim",
        ),
        (
            WorkspaceTargetRefCodec,
            "workspace-target://grant/../../etc/passwd",
        ),
    ],
)
def test_references_reject_zero_traversal_and_extra_segments(
    codec: type[object], text: str
) -> None:
    with pytest.raises(ArtifactEffectFormatError):
        codec.parse(text)  # type: ignore[attr-defined]


def test_references_reject_overlong_values() -> None:
    text = "workspace-target://grant/" + ("a" * 3000)
    with pytest.raises(ArtifactEffectFormatError):
        WorkspaceTargetRefCodec.parse(text)


def test_revision_refs_reject_values_above_cross_language_safe_integer() -> None:
    artifact_id = "art_123e4567-e89b-42d3-a456-426614174000"
    unsafe = 9_007_199_254_740_993
    with pytest.raises(ArtifactEffectFormatError, match="safe integer"):
        ArtifactContentRefCodec.format(artifact_id, unsafe)
    with pytest.raises(ArtifactEffectFormatError, match="safe integer"):
        ArtifactContentRefCodec.parse(f"artifact://{artifact_id}/revisions/{unsafe}")
