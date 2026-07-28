"""Operational corpus completeness and deterministic scorer tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agent_runtime.harness_quality.evaluation import FixtureToolExecutor
from agent_runtime.harness_quality.evaluation_contracts import (
    HarnessVariant,
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
from agent_runtime.harness_quality.suite_execution import FixtureOnlyCaseExecutor
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _variant() -> HarnessVariant:
    return HarnessVariant(
        variant_id="candidate",
        revision="variant-v1",
        prompt_plan_revision="prompt-v1",
        capability_policy_revision="capability-v1",
        context_policy_revision="context-v1",
        model_route_revision="model-v1",
    )


def _trajectory(
    entry,
    *,
    evidence_refs=None,
    steps=None,
    usage_summary=None,
) -> TrajectoryManifest:
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
        "usage_summary": usage_summary or {"live_effect_dispatches": 0},
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
        "task_policy_one_call_lookup",
        "task_policy_plan_before_tool",
        "task_policy_pagination_changed_cursor",
        "task_policy_exact_duplicate_blocked",
        "task_policy_retryable_error_changed_input",
        "task_policy_nonretryable_error_stopped",
        "task_policy_same_source_advisory",
        "task_policy_objective_completeness",
        "task_policy_cost_budget_exhaustion",
        "task_policy_tool_budget_exhaustion",
        "task_policy_turn_budget_exhaustion",
        "task_policy_deadline_exhaustion",
        "task_policy_restart_replay",
        "task_policy_approval_resume",
        "task_policy_shadow_enforce_comparison",
        "prompt_cache_prefix_reuse",
    )
    entries = operational_corpus()
    assert tuple(entry.family for entry in entries) == OPERATIONAL_TASK_FAMILIES
    assert len({entry.case.case_id for entry in entries}) == len(entries)
    assert all(entry.case.sensitivity == "synthetic" for entry in entries)


async def test_every_operational_fixture_is_an_exact_offline_lookup() -> None:
    entries = operational_corpus()
    executor = FixtureToolExecutor(
        fixture for entry in entries for fixture in entry.fixtures
    )

    for entry in entries:
        for call in entry.calls:
            response = await executor.execute(
                capability_id=call.capability_id,
                arguments=call.arguments,
            )
            assert response == call.fixture
            assert response.request_digest == FixtureToolExecutor.request_digest(
                capability_id=call.capability_id,
                arguments=call.arguments,
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
    assert all(result.passed for result in first)
    assert tuple(result.scorer_id for result in first) == (
        "hard_safety",
        "hard_groundedness",
        "hard_constraints",
        "task_policy_trajectory",
        "prompt_cache_trajectory",
    )
    assert all(result.hard_gate for result in first[:3])
    assert all(result.hard_gate is False for result in first[-2:])


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


async def test_f4_corpus_cases_execute_and_score_through_existing_fixture_runner() -> (
    None
):
    entries = tuple(
        entry
        for entry in operational_corpus()
        if entry.family.startswith("task_policy_")
    )
    fixtures = FixtureToolExecutor(
        fixture for entry in entries for fixture in entry.fixtures
    )
    executor = FixtureOnlyCaseExecutor()

    for entry in entries:
        trajectory = await executor.execute(
            suite_run_id="suite_f4",
            case=entry.case,
            variant=_variant(),
            plan=entry.plan(),
            fixtures=fixtures,
            projected_at=_NOW,
        )
        results = tuple(
            scorer.score(case=entry.case, trajectory=trajectory)
            for scorer in DEFAULT_HARD_SCORERS
        )
        assert all(result.passed for result in results), entry.family


async def test_f2_prefix_reuse_case_executes_and_requires_provider_reports() -> None:
    entry = next(
        item
        for item in operational_corpus()
        if item.family == "prompt_cache_prefix_reuse"
    )
    trajectory = await FixtureOnlyCaseExecutor().execute(
        suite_run_id="suite_f2",
        case=entry.case,
        variant=_variant(),
        plan=entry.plan(),
        fixtures=FixtureToolExecutor(entry.fixtures),
        projected_at=_NOW,
    )
    scorer = next(
        scorer
        for scorer in DEFAULT_HARD_SCORERS
        if scorer.scorer_id == "prompt_cache_trajectory"
    )
    passed = scorer.score(case=entry.case, trajectory=trajectory)
    unreported = trajectory.model_copy(
        update={
            "ordered_steps": tuple(
                step.model_copy(update={"prompt_provider_reported": False})
                if step.prompt_record_kind == "cache_observed"
                else step
                for step in trajectory.ordered_steps
            )
        }
    )
    failed = scorer.score(case=entry.case, trajectory=unreported)

    assert passed.passed
    assert passed.reason_code == "prompt_cache_trajectory_passed"
    assert not failed.passed
    assert failed.reason_code == "prompt_cache_provider_report_missing"


def test_f4_hard_and_advisory_trajectory_semantics_are_distinct() -> None:
    entries = {entry.family: entry for entry in operational_corpus()}
    hard = entries["task_policy_exact_duplicate_blocked"]
    advisory = entries["task_policy_same_source_advisory"]
    scorer = next(
        scorer
        for scorer in DEFAULT_HARD_SCORERS
        if scorer.scorer_id == "task_policy_trajectory"
    )
    empty = _trajectory(hard)

    hard_result = scorer.score(case=hard.case, trajectory=empty)
    advisory_result = scorer.score(case=advisory.case, trajectory=empty)

    assert hard_result.passed is False
    assert hard_result.hard_gate is True
    assert advisory_result.passed is False
    assert advisory_result.hard_gate is False


def test_f4_shadow_and_enforce_duplicate_trajectories_are_distinct() -> None:
    entry = next(
        item
        for item in operational_corpus()
        if item.family == "task_policy_shadow_enforce_comparison"
    )
    shadow_case = entry.case.model_copy(
        update={
            "expected_assertions": tuple(
                assertion.model_copy(
                    update={
                        "expected": {
                            **assertion.expected,
                            "required_dispositions": ["shadow_admitted"],
                            "maximum_tool_calls": 2,
                        },
                        "hard_gate": False,
                    }
                )
                if assertion.scorer_id == "task_policy_trajectory"
                else assertion
                for assertion in entry.case.expected_assertions
            )
        }
    )
    shadow_steps = (
        TrajectoryStep(
            sequence_no=1,
            event_type="tool_policy.journal.v1",
            source="runtime",
            policy_record_kind="admission_recorded",
            policy_disposition="shadow_admitted",
            policy_reason_codes=("exact_duplicate",),
            payload_digest=canonical_json_sha256({"shadow": "duplicate"}),
        ),
        TrajectoryStep(
            sequence_no=2,
            event_type="fixture_tool_result",
            source="fixture",
            capability_id=entry.capability_id,
            payload_digest=entry.fixture.response_digest,
        ),
        TrajectoryStep(
            sequence_no=3,
            event_type="fixture_tool_result",
            source="fixture",
            capability_id=entry.capability_id,
            payload_digest=entry.fixture.response_digest,
        ),
    )
    shadow = _trajectory(
        entry,
        steps=shadow_steps,
        usage_summary={"live_effect_dispatches": 0, "tool_calls": 2},
    ).model_copy(update={"variant_id": "shadow"})
    enforce = _trajectory(
        entry,
        steps=(
            TrajectoryStep(
                sequence_no=1,
                event_type="tool_policy.journal.v1",
                source="runtime",
                policy_record_kind="admission_recorded",
                policy_disposition="blocked",
                policy_reason_codes=("exact_duplicate",),
                payload_digest=canonical_json_sha256({"enforce": "duplicate"}),
            ),
            TrajectoryStep(
                sequence_no=2,
                event_type="fixture_tool_result",
                source="fixture",
                capability_id=entry.capability_id,
                payload_digest=entry.fixture.response_digest,
            ),
        ),
        usage_summary={"live_effect_dispatches": 0, "tool_calls": 1},
    ).model_copy(update={"variant_id": "enforce"})
    scorer = next(
        scorer
        for scorer in DEFAULT_HARD_SCORERS
        if scorer.scorer_id == "task_policy_trajectory"
    )

    assert scorer.score(case=shadow_case, trajectory=shadow).passed
    assert scorer.score(case=entry.case, trajectory=enforce).passed
    assert shadow.usage_summary["tool_calls"] > enforce.usage_summary["tool_calls"]


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
