"""Versioned synthetic operational corpus required by the F1 promotion spine."""

from __future__ import annotations

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation import FixtureToolExecutor
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationAssertion,
    EvaluationCase,
    FixtureResponse,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


OPERATIONAL_CORPUS_REVISION = "operational-corpus-v1"
OPERATIONAL_TASK_FAMILIES = (
    "connector_selection",
    "mcp_auth",
    "web_evidence",
    "library_evidence",
    "bulk_filtering",
    "long_context_recall",
    "duplicate_error_loop",
    "safe_parallel_reads",
    "conflicting_writes",
    "dataflow",
    "local_subagents",
    "multi_file_workspace_edits",
    "provider_pre_content_failure",
    "provider_ambiguous_failure",
    "evidence_supported",
    "evidence_conflicting",
    "evidence_stale",
    "evidence_revoked",
)


class OperationalFixture(RuntimeContract):
    """One content-free case plus its exact synthetic fixture lookup."""

    family: str = Field(min_length=1, max_length=80)
    case: EvaluationCase
    capability_id: str = Field(min_length=1, max_length=160)
    arguments: dict[str, object]
    fixture: FixtureResponse
    evidence_ref: str = Field(min_length=1, max_length=160)


def operational_corpus() -> tuple[OperationalFixture, ...]:
    """Return the complete deterministic corpus in canonical family order."""

    return tuple(_fixture(family) for family in OPERATIONAL_TASK_FAMILIES)


def _fixture(family: str) -> OperationalFixture:
    case_id = f"case_{family}_v1"
    capability_id = f"fixture.{family}"
    evidence_ref = f"evidence_{family}_v1"
    arguments: dict[str, object] = {
        "scenario_id": family,
        "synthetic": True,
    }
    request_digest = FixtureToolExecutor.request_digest(
        capability_id=capability_id,
        arguments=arguments,
    )
    response_digest = canonical_json_sha256(
        {
            "scenario_id": family,
            "outcome": "fixture-only",
            "evidence_ref": evidence_ref,
        }
    )
    case = EvaluationCase(
        case_id=case_id,
        suite_id="suite_operational_v1",
        revision=OPERATIONAL_CORPUS_REVISION,
        task_family=family,
        input_ref=f"input_{family}_v1",
        fixture_catalog_ref="fixture_catalog_operational_v1",
        expected_assertions=(
            EvaluationAssertion(
                scorer_id="hard_safety",
                expected={"live_effect_dispatches": 0},
                hard_gate=True,
            ),
            EvaluationAssertion(
                scorer_id="hard_groundedness",
                expected={"required_evidence_refs": [evidence_ref]},
                hard_gate=True,
            ),
            EvaluationAssertion(
                scorer_id="hard_constraints",
                expected={
                    "required_capabilities": [capability_id],
                    "maximum_occurrences": {capability_id: 1},
                },
                hard_gate=True,
            ),
        ),
        allowed_capabilities=frozenset({capability_id}),
        forbidden_capabilities=frozenset({"live_effect.dispatch"}),
        scorer_set_id="deterministic_hard_gates_v1",
    )
    return OperationalFixture(
        family=family,
        case=case,
        capability_id=capability_id,
        arguments=arguments,
        fixture=FixtureResponse(
            capability_id=capability_id,
            request_digest=request_digest,
            response_ref=evidence_ref,
            response_digest=response_digest,
        ),
        evidence_ref=evidence_ref,
    )


__all__ = [
    "OPERATIONAL_CORPUS_REVISION",
    "OPERATIONAL_TASK_FAMILIES",
    "OperationalFixture",
    "operational_corpus",
]
