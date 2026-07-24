from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.effects.contracts import EffectProposalKind
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind

from .fakes import proposal, revision_from


@pytest.mark.parametrize(
    ("kind", "executor"),
    [
        (EffectProposalKind.CANONICAL_ARGUMENTS, EffectExecutorKind.MCP),
        (EffectProposalKind.ARTIFACT_REVISION, EffectExecutorKind.WORKSPACE),
        (EffectProposalKind.WORKSPACE_CHANGE_SET, EffectExecutorKind.WORKSPACE),
        (EffectProposalKind.ROW_SET, EffectExecutorKind.MCP),
        (EffectProposalKind.BROWSER_SUBMISSION, EffectExecutorKind.BROWSER),
        (EffectProposalKind.SANDBOX_PATCH, EffectExecutorKind.SANDBOX),
        (EffectProposalKind.BUILTIN_PAYLOAD, EffectExecutorKind.BUILTIN),
    ],
)
def test_proposal_union_covers_every_a4_kind(
    kind: EffectProposalKind,
    executor: EffectExecutorKind,
) -> None:
    value = proposal(kind=kind, executor=executor)

    assert value.proposal_kind is kind
    assert value.executor is executor
    assert value.proposal_content_ref.startswith("artifact://")
    assert "proposal_ref" not in type(value).model_fields
    assert "body" not in type(value).model_fields
    assert "raw_args" not in type(value).model_fields


def test_proposal_rejects_incompatible_kind_and_executor() -> None:
    with pytest.raises(ValidationError, match="incompatible"):
        proposal(
            kind=EffectProposalKind.BROWSER_SUBMISSION,
            executor=EffectExecutorKind.MCP,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposal_content_ref", "file:///Users/example/secret.md"),
        ("proposal_content_ref", "data:text/plain,raw-body"),
        ("proposal_content_ref", "/Users/example/secret.md"),
        ("proposal_content_ref", "artifact://safe/%2e%2e/secret"),
        ("proposal_content_ref", "artifact://safe/%252e%252e/secret"),
        ("proposal_content_ref", r"artifact://safe\..\secret"),
        ("proposal_content_ref", "https://example.com/proposal.json"),
        ("proposal_content_ref", "artifact://safe/revision?mutable=true"),
        (
            "proposal_content_ref",
            "proposal://stg_00000000-0000-4000-8000-000000000001/revisions/1",
        ),
        ("safe_summary_ref", "/Users/example/secret.md"),
    ],
)
def test_proposal_rejects_raw_or_physical_references(field: str, value: str) -> None:
    data = proposal().model_dump()
    data[field] = value

    with pytest.raises(ValidationError):
        proposal().__class__(**data)


def test_precondition_reference_and_digest_are_paired() -> None:
    data = proposal().model_dump()
    data["precondition_digest"] = None

    with pytest.raises(ValidationError, match="precondition"):
        proposal().__class__(**data)


@pytest.mark.parametrize(
    "value",
    [
        "file:///tmp/body.json",
        "data:application/json,%7B%7D",
        r"C:\Users\example\body.json",
        "artifact://safe/a/../body",
        "artifact://safe/a/%2e%2e/body",
        "artifact://safe/a/%252e%252e/body",
        "proposal://stg_00000000-0000-4000-8000-000000000001/revisions/2",
    ],
)
def test_revision_proposal_rejects_non_content_references(value: str) -> None:
    revision = revision_from(proposal())
    data = revision.model_dump()
    data["proposal_content_ref"] = value

    with pytest.raises(ValidationError):
        revision.__class__(**data)
