"""Focused conformance tests for resumable fixture-only F1 suite execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationAssertion,
    EvaluationCase,
    EvaluationCaseRef,
    EvaluationRevisionSet,
    EvaluationScope,
    EvaluationStatus,
    EvaluationSuiteLimits,
    EvaluationSuiteRun,
    EvaluationSuiteRunCheckpoint,
    FixtureCatalog,
    HarnessVariant,
)
from agent_runtime.harness_quality.operational_corpus import operational_corpus
from agent_runtime.harness_quality.scoring import (
    BoundedRedactedGrader,
    GraderAttribution,
    RedactedGradeRequest,
)
from agent_runtime.harness_quality.suite_execution import (
    FixtureCasePlan,
    FixtureExecutionForbidden,
    FixtureOnlySuiteRunner,
)
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)

_NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


@dataclass
class _Repository:
    cases: dict[tuple[str, str], EvaluationCase]
    catalog: FixtureCatalog
    suite_runs: dict[str, EvaluationSuiteRun] = field(default_factory=dict)
    checkpoints: list[EvaluationSuiteRunCheckpoint] = field(default_factory=list)
    manifests: dict[str, object] = field(default_factory=dict)
    results: dict[str, object] = field(default_factory=dict)
    result_put_attempts: int = 0
    raise_after_checkpoint: int | None = None

    async def put_suite_run(self, _scope, suite_run):
        existing = self.suite_runs.get(suite_run.suite_run_id)
        if existing is not None and existing != suite_run:
            raise ValueError("suite conflict")
        self.suite_runs[suite_run.suite_run_id] = suite_run
        return existing is None

    async def get_fixture_catalog(self, _scope, *, catalog_id, revision):
        if (catalog_id, revision) == (
            self.catalog.catalog_id,
            self.catalog.revision,
        ):
            return self.catalog
        return None

    async def get_case(self, _scope, *, case_id, revision):
        return self.cases.get((case_id, revision))

    async def latest_suite_run_checkpoint(self, _scope, *, suite_run_id):
        matching = [
            checkpoint
            for checkpoint in self.checkpoints
            if checkpoint.suite_run_id == suite_run_id
        ]
        return matching[-1] if matching else None

    async def append_suite_run_checkpoint(self, _scope, checkpoint):
        self.checkpoints.append(checkpoint)
        if self.raise_after_checkpoint == checkpoint.checkpoint_no:
            self.raise_after_checkpoint = None
            raise RuntimeError("simulated process crash after durable checkpoint")
        return True

    async def put_trajectory_manifest(self, _scope, manifest):
        existing = self.manifests.get(manifest.trajectory_id)
        if existing is not None and existing != manifest:
            raise ValueError("manifest conflict")
        self.manifests[manifest.trajectory_id] = manifest
        return existing is None

    async def put_evaluation_result(self, _scope, result):
        self.result_put_attempts += 1
        existing = self.results.get(result.evaluation_run_id)
        if existing is not None and existing != result:
            raise ValueError("result conflict")
        self.results[result.evaluation_run_id] = result
        return existing is None

    async def get_evaluation_result(self, _scope, *, evaluation_run_id):
        return self.results.get(evaluation_run_id)


class _Grader:
    def __init__(self) -> None:
        self.requests: list[RedactedGradeRequest] = []

    async def grade(self, request: RedactedGradeRequest) -> GraderAttribution:
        self.requests.append(request)
        return GraderAttribution(
            grader_id="bounded-reviewer",
            grader_revision="reviewer-v1",
            model_revision="grader-model-v1",
            prompt_revision="grader-prompt-v1",
            score=1,
            passed=True,
            reason_code="grader_claims_pass",
            tokens=5,
            cost_microusd=7,
        )


def _variant() -> HarnessVariant:
    return HarnessVariant(
        variant_id="candidate",
        revision="variant-v1",
        prompt_plan_revision="prompt-v1",
        capability_policy_revision="capability-v1",
        context_policy_revision="context-v1",
        model_route_revision="model-v1",
    )


def _limits(**updates: int) -> EvaluationSuiteLimits:
    values = {
        "revision": "limits-v1",
        "max_case_cost_microusd": 100,
        "max_suite_cost_microusd": 1_000,
        "max_case_model_turns": 10,
        "max_suite_model_turns": 100,
        "max_case_tool_calls": 10,
        "max_suite_tool_calls": 100,
        "max_case_tokens": 1_000,
        "max_suite_tokens": 10_000,
        "max_case_wall_time_ms": 1_000,
        "max_suite_wall_time_ms": 10_000,
    }
    values.update(updates)
    return EvaluationSuiteLimits(**values)


def _fixture_catalog(entries) -> FixtureCatalog:
    values = {
        "catalog_id": "fixture_catalog_operational_v1",
        "revision": "fixture-v1",
        "fixtures": tuple(
            sorted(
                (fixture for entry in entries for fixture in entry.fixtures),
                key=lambda item: (item.capability_id, item.request_digest),
            )
        ),
        "created_at": _NOW,
    }
    return FixtureCatalog(
        **values,
        catalog_digest=FixtureCatalog.digest_for(**values),
    )


def _suite(
    *,
    entries,
    limits: EvaluationSuiteLimits | None = None,
    cases: tuple[EvaluationCase, ...] | None = None,
) -> tuple[_Repository, EvaluationSuiteRun, dict[str, FixtureCasePlan]]:
    selected_cases = cases or tuple(entry.case for entry in entries)
    catalog = _fixture_catalog(entries)
    variant = _variant()
    case_refs = tuple(
        EvaluationCaseRef(case_id=case.case_id, revision=case.revision)
        for case in sorted(
            selected_cases, key=lambda item: (item.case_id, item.revision)
        )
    )
    values = {
        "suite_run_id": "suite-run-1",
        "suite_id": "suite_operational_v1",
        "suite_revision": "suite-v1",
        "variant_id": variant.variant_id,
        "variant_revision": variant.revision,
        "variant_digest": variant.digest,
        "fixture_catalog_id": catalog.catalog_id,
        "fixture_catalog_revision": catalog.revision,
        "case_refs": case_refs,
        "revisions": EvaluationRevisionSet(
            code_revision="code-v1",
            model_revision="model-v1",
            prompt_revision="prompt-v1",
            tool_revision="tool-v1",
            policy_revision="policy-v1",
            fixture_revision="fixture-v1",
            scorer_revision="scorer-v1",
        ),
        "limits": limits or _limits(),
        "created_at": _NOW,
    }
    suite_run = EvaluationSuiteRun(
        **values,
        suite_run_digest=EvaluationSuiteRun.digest_for(**values),
    )
    by_family = {entry.family: entry for entry in entries}
    plans = {
        case.case_id: by_family[case.task_family].plan() for case in selected_cases
    }
    repository = _Repository(
        cases={(case.case_id, case.revision): case for case in selected_cases},
        catalog=catalog,
    )
    return repository, suite_run, plans


async def test_suite_executes_only_exact_fixtures_and_checkpoints_each_case() -> None:
    entries = operational_corpus()[:2]
    repository, suite_run, plans = _suite(entries=entries)
    runner = FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        clock=lambda: _NOW,
    )

    terminal = await runner.run(
        suite_run=suite_run,
        variant=_variant(),
        plans=plans,
    )

    assert terminal.status is EvaluationStatus.SUCCEEDED
    assert terminal.next_case_index == 2
    assert len(repository.results) == len(repository.manifests) == 2
    assert [checkpoint.checkpoint_no for checkpoint in repository.checkpoints] == [
        0,
        1,
        2,
        3,
        4,
        5,
    ]
    assert all(
        manifest.usage_summary["live_effect_dispatches"] == 0
        for manifest in repository.manifests.values()
    )


async def test_runner_conforms_to_canonical_async_repository_port() -> None:
    entries = operational_corpus()[:2]
    _fake, suite_run, plans = _suite(entries=entries)
    repository = InMemoryEvaluationRepository()
    scope = EvaluationScope(profile_id="local-profile")
    for entry in entries:
        await repository.put_case(scope, entry.case)
    await repository.put_fixture_catalog(scope, _fixture_catalog(entries))

    terminal = await FixtureOnlySuiteRunner(
        repository=repository,
        scope=scope,
        clock=lambda: _NOW,
    ).run(suite_run=suite_run, variant=_variant(), plans=plans)

    assert terminal.status is EvaluationStatus.SUCCEEDED
    results = await repository.list_evaluation_results(
        scope,
        variant_id="candidate",
    )
    assert len(results) == 2
    assert all(result.status is EvaluationStatus.SUCCEEDED for result in results)


async def test_resume_uses_durable_cursor_and_immutable_case_result() -> None:
    entries = operational_corpus()[:2]
    repository, suite_run, plans = _suite(entries=entries)
    repository.raise_after_checkpoint = 2
    runner = FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        clock=lambda: _NOW,
    )

    with pytest.raises(RuntimeError, match="simulated process crash"):
        await runner.run(suite_run=suite_run, variant=_variant(), plans=plans)
    assert repository.checkpoints[-1].next_case_index == 1

    terminal = await runner.run(
        suite_run=suite_run,
        variant=_variant(),
        plans=plans,
    )

    assert terminal.status is EvaluationStatus.SUCCEEDED
    assert terminal.next_case_index == 2
    assert len(repository.results) == 2
    assert repository.result_put_attempts == 2


async def test_resume_rejects_changed_active_fixture_plan() -> None:
    entry = operational_corpus()[0]
    repository, suite_run, plans = _suite(entries=(entry,))
    repository.raise_after_checkpoint = 1
    runner = FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        clock=lambda: _NOW,
    )

    with pytest.raises(RuntimeError, match="simulated process crash"):
        await runner.run(suite_run=suite_run, variant=_variant(), plans=plans)
    original = plans[entry.case.case_id]
    plans[entry.case.case_id] = original.model_copy(
        update={
            "calls": (
                original.calls[0].model_copy(
                    update={"arguments": {"scenario_id": "changed"}}
                ),
            )
        }
    )

    with pytest.raises(FixtureExecutionForbidden, match="bind the fixture plan"):
        await runner.run(suite_run=suite_run, variant=_variant(), plans=plans)

    assert repository.results == {}
    assert repository.manifests == {}


@pytest.mark.parametrize(
    ("usage_updates", "limit_updates", "reason"),
    [
        ({"cost_microusd": 11}, {"max_case_cost_microusd": 10}, "case_cost"),
        ({"model_turns": 2}, {"max_case_model_turns": 1}, "case_model_turns"),
        ({"tool_calls": 2}, {"max_case_tool_calls": 1}, "case_tool_calls"),
        ({"tokens": 101}, {"max_case_tokens": 100}, "case_tokens"),
        ({"elapsed_ms": 101}, {"max_case_wall_time_ms": 100}, "case_wall_time"),
    ],
)
async def test_every_per_case_ceiling_fails_before_fixture_dispatch(
    usage_updates: dict[str, int],
    limit_updates: dict[str, int],
    reason: str,
) -> None:
    entry = operational_corpus()[0]
    repository, suite_run, plans = _suite(
        entries=(entry,),
        limits=_limits(**limit_updates),
    )
    plan = plans[entry.case.case_id]
    if usage_updates.get("tool_calls") == 2:
        plan = plan.model_copy(update={"calls": (*plan.calls, plan.calls[0])})
    plans[entry.case.case_id] = plan.model_copy(
        update={"usage": plan.usage.model_copy(update=usage_updates)}
    )

    terminal = await FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        clock=lambda: _NOW,
    ).run(suite_run=suite_run, variant=_variant(), plans=plans)

    assert terminal.status is EvaluationStatus.FAILED
    assert terminal.reason_codes == (f"{reason}_limit_exceeded",)
    assert repository.manifests == {}
    assert repository.results == {}


@pytest.mark.parametrize(
    ("usage_field", "case_value", "suite_limit", "reason"),
    [
        ("cost_microusd", 6, 10, "suite_cost"),
        ("model_turns", 1, 1, "suite_model_turns"),
        ("tool_calls", 1, 1, "suite_tool_calls"),
        ("tokens", 60, 100, "suite_tokens"),
        ("elapsed_ms", 60, 100, "suite_wall_time"),
    ],
)
async def test_every_per_suite_ceiling_stops_before_over_budget_case(
    usage_field: str,
    case_value: int,
    suite_limit: int,
    reason: str,
) -> None:
    entries = operational_corpus()[:2]
    limit_field = {
        "cost_microusd": "max_suite_cost_microusd",
        "model_turns": "max_suite_model_turns",
        "tool_calls": "max_suite_tool_calls",
        "tokens": "max_suite_tokens",
        "elapsed_ms": "max_suite_wall_time_ms",
    }[usage_field]
    case_limit_field = limit_field.replace("max_suite_", "max_case_")
    repository, suite_run, plans = _suite(
        entries=entries,
        limits=_limits(
            **{
                limit_field: suite_limit,
                case_limit_field: max(case_value, 1),
            }
        ),
    )
    for entry in entries:
        plan = plans[entry.case.case_id]
        plans[entry.case.case_id] = plan.model_copy(
            update={"usage": plan.usage.model_copy(update={usage_field: case_value})}
        )

    terminal = await FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        clock=lambda: _NOW,
    ).run(suite_run=suite_run, variant=_variant(), plans=plans)

    assert terminal.status is EvaluationStatus.FAILED
    assert terminal.reason_codes == (f"{reason}_limit_exceeded",)
    assert terminal.next_case_index == 1
    assert len(repository.results) == 1


async def test_non_synthetic_case_fails_closed_without_persisting_result() -> None:
    entry = operational_corpus()[0]
    non_synthetic = entry.case.model_copy(update={"sensitivity": "private"})
    repository, suite_run, plans = _suite(
        entries=(entry,),
        cases=(non_synthetic,),
    )

    with pytest.raises(FixtureExecutionForbidden, match="synthetic cases only"):
        await FixtureOnlySuiteRunner(
            repository=repository,  # type: ignore[arg-type]
            scope=EvaluationScope(profile_id="local-profile"),
            clock=lambda: _NOW,
        ).run(suite_run=suite_run, variant=_variant(), plans=plans)

    assert repository.results == {}
    assert repository.manifests == {}


async def test_fixture_miss_is_terminally_inconclusive_without_live_fallback() -> None:
    entry = operational_corpus()[0]
    repository, suite_run, plans = _suite(entries=(entry,))
    plan = plans[entry.case.case_id]
    plans[entry.case.case_id] = plan.model_copy(
        update={
            "calls": (
                plan.calls[0].model_copy(
                    update={"arguments": {"scenario_id": "unrecorded"}}
                ),
            )
        }
    )

    terminal = await FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        clock=lambda: _NOW,
    ).run(suite_run=suite_run, variant=_variant(), plans=plans)

    result = next(iter(repository.results.values()))
    assert terminal.status is EvaluationStatus.FAILED
    assert result.status is EvaluationStatus.INCONCLUSIVE
    assert result.hard_gate_failures == ("fixture_miss",)
    assert repository.manifests == {}


async def test_advisory_grader_cannot_override_deterministic_hard_failure() -> None:
    entry = operational_corpus()[0]
    ungrounded = entry.case.model_copy(
        update={
            "expected_assertions": tuple(
                EvaluationAssertion(
                    scorer_id=assertion.scorer_id,
                    expected=(
                        {"required_evidence_refs": ["evidence_missing_v1"]}
                        if assertion.scorer_id == "hard_groundedness"
                        else assertion.expected
                    ),
                    hard_gate=assertion.hard_gate,
                )
                for assertion in entry.case.expected_assertions
            ),
        }
    )
    repository, suite_run, plans = _suite(
        entries=(entry,),
        cases=(ungrounded,),
    )
    grader = _Grader()

    terminal = await FixtureOnlySuiteRunner(
        repository=repository,  # type: ignore[arg-type]
        scope=EvaluationScope(profile_id="local-profile"),
        optional_grader=BoundedRedactedGrader(
            grader=grader,
            maximum_requests=1,
            timeout_ms=100,
            maximum_tokens=100,
            maximum_cost_microusd=10,
        ),
        clock=lambda: _NOW,
    ).run(suite_run=suite_run, variant=_variant(), plans=plans)

    result = next(iter(repository.results.values()))
    assert terminal.status is EvaluationStatus.FAILED
    assert result.status is EvaluationStatus.FAILED
    assert result.hard_gate_failures == ("required_evidence_missing",)
    assert result.scorer_results[-1].hard_gate is False
    assert result.scorer_results[-1].passed is True
    assert result.scorer_results[-1].attribution is not None
    assert result.scorer_results[-1].attribution.model_revision == "grader-model-v1"
    assert result.total_cost == 0.000017
    assert result.model_turns == 2
    assert len(grader.requests) == 1
    assert grader.requests[0].maximum_output_tokens == 100
    assert grader.requests[0].maximum_cost_microusd == 10
    assert "arguments" not in grader.requests[0].model_dump()
    assert "response" not in grader.requests[0].model_dump()
