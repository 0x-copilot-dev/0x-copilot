"""Operational corpus completeness and deterministic scorer tests."""

from __future__ import annotations

import asyncio

from agent_runtime.harness_quality.evaluation import FixtureToolExecutor
from agent_runtime.harness_quality.evaluation_contracts import (
    TrajectoryManifest,
    TrajectoryStep,
)
from agent_runtime.harness_quality.operational_corpus import (
    OPERATIONAL_TASK_FAMILIES,
    operational_corpus,
)
from agent_runtime.harness_quality.scoring import (
    BoundedRedactedGrader,
    DEFAULT_HARD_SCORERS,
    GraderAttribution,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


def _trajectory(entry, *, evidence_refs=None, steps=None) -> TrajectoryManifest:
    values = {
        "trajectory_id": f"trajectory_{entry.family}",
        "run_id": None,
        "case_id": entry.case.case_id,
        "variant_id": "candidate",
        "ordered_steps": steps
        or (
            TrajectoryStep(
                sequence_no=1,
                event_type="fixture_tool_result",
                source="fixture",
                capability_id=entry.capability_id,
                payload_digest=entry.fixture.response_digest,
            ),
        ),
        "evidence_refs": (
            tuple(evidence_refs) if evidence_refs is not None else (entry.evidence_ref,)
        ),
        "usage_summary": {"live_effect_dispatches": 0},
        "redaction_policy_revision": "redaction-v1",
        "harness_revisions": {"suite": "suite-v1"},
    }
    return TrajectoryManifest(
        **values,
        manifest_digest=TrajectoryManifest.digest_for(**values),
    )


def test_operational_corpus_covers_every_required_family_and_scenario() -> None:
    assert OPERATIONAL_TASK_FAMILIES == (
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
    entries = operational_corpus()
    assert tuple(entry.family for entry in entries) == OPERATIONAL_TASK_FAMILIES
    assert len({entry.case.case_id for entry in entries}) == len(entries)
    assert all(entry.case.sensitivity == "synthetic" for entry in entries)


async def test_every_operational_fixture_is_an_exact_offline_lookup() -> None:
    entries = operational_corpus()
    executor = FixtureToolExecutor(entry.fixture for entry in entries)

    for entry in entries:
        response = await executor.execute(
            capability_id=entry.capability_id,
            arguments=entry.arguments,
        )
        assert response == entry.fixture
        assert response.request_digest == FixtureToolExecutor.request_digest(
            capability_id=entry.capability_id,
            arguments=entry.arguments,
        )


def test_hard_safety_groundedness_and_constraints_are_deterministic() -> None:
    entry = operational_corpus()[0]
    trajectory = _trajectory(entry)

    first = tuple(
        scorer.score(case=entry.case, trajectory=trajectory)
        for scorer in DEFAULT_HARD_SCORERS
    )
    second = tuple(
        scorer.score(case=entry.case, trajectory=trajectory)
        for scorer in DEFAULT_HARD_SCORERS
    )

    assert first == second
    assert all(result.hard_gate and result.passed for result in first)
    assert tuple(result.scorer_id for result in first) == (
        "hard_safety",
        "hard_groundedness",
        "hard_constraints",
    )


def test_groundedness_and_constraint_failures_use_stable_reason_codes() -> None:
    entry = operational_corpus()[0]
    empty_step = TrajectoryStep(
        sequence_no=1,
        event_type="fixture_tool_result",
        source="fixture",
        capability_id="fixture.wrong_capability",
        payload_digest=canonical_json_sha256({"wrong": True}),
    )
    trajectory = _trajectory(entry, evidence_refs=(), steps=(empty_step,))
    results = {
        scorer.scorer_id: scorer.score(case=entry.case, trajectory=trajectory)
        for scorer in DEFAULT_HARD_SCORERS
    }

    assert results["hard_groundedness"].reason_code == "required_evidence_missing"
    assert results["hard_constraints"].reason_code == "required_capability_missing"


class _SlowGrader:
    def __init__(self) -> None:
        self.calls = 0

    async def grade(self, _request):
        self.calls += 1
        await asyncio.sleep(0.02)
        return GraderAttribution(
            grader_id="slow",
            grader_revision="v1",
            model_revision="grader-model-v1",
            prompt_revision="grader-prompt-v1",
            score=1,
            passed=True,
            reason_code="late",
        )


async def test_optional_grader_is_time_and_request_bounded() -> None:
    entry = operational_corpus()[0]
    trajectory = _trajectory(entry)
    deterministic = tuple(
        scorer.score(case=entry.case, trajectory=trajectory)
        for scorer in DEFAULT_HARD_SCORERS
    )
    grader_port = _SlowGrader()
    grader = BoundedRedactedGrader(
        grader=grader_port,
        maximum_requests=1,
        timeout_ms=1,
        maximum_tokens=100,
        maximum_cost_microusd=10,
    )

    timed_out = await grader.score(
        case=entry.case,
        trajectory=trajectory,
        deterministic_results=deterministic,
    )
    exhausted = await grader.score(
        case=entry.case,
        trajectory=trajectory,
        deterministic_results=deterministic,
    )

    assert timed_out is not None
    assert timed_out.reason_code == "optional_grader_timeout"
    assert timed_out.hard_gate is False
    assert exhausted is None
    assert grader_port.calls == 1


class _OverBudgetGrader:
    async def grade(self, _request):
        return GraderAttribution(
            grader_id="over-budget",
            grader_revision="v1",
            model_revision="grader-model-v1",
            prompt_revision="grader-prompt-v1",
            score=1,
            passed=True,
            reason_code="claims_pass",
            tokens=101,
            cost_microusd=11,
        )


async def test_optional_grader_cannot_exceed_attributed_token_or_cost_budget() -> None:
    entry = operational_corpus()[0]
    trajectory = _trajectory(entry)
    deterministic = tuple(
        scorer.score(case=entry.case, trajectory=trajectory)
        for scorer in DEFAULT_HARD_SCORERS
    )
    grader = BoundedRedactedGrader(
        grader=_OverBudgetGrader(),
        maximum_requests=1,
        timeout_ms=100,
        maximum_tokens=100,
        maximum_cost_microusd=10,
    )

    result = await grader.score(
        case=entry.case,
        trajectory=trajectory,
        deterministic_results=deterministic,
    )

    assert result is not None
    assert result.reason_code == "optional_grader_budget_exceeded"
    assert result.hard_gate is False
    assert result.attribution is not None
    assert result.attribution.tokens == 101
    assert result.attribution.cost_microusd == 11
