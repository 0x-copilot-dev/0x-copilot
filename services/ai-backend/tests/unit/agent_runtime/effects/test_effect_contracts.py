from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.effects.contracts import EffectProposalKind
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind

from .fakes import proposal


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
    assert value.proposal_ref.startswith("artifact://")
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
        ("proposal_ref", "file:///Users/example/secret.md"),
        ("proposal_ref", "data:text/plain,raw-body"),
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
