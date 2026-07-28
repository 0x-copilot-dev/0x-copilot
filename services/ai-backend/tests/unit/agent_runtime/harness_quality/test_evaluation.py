from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.harness_quality import (
    DeterministicEvaluationRunner,
    EvaluationCase,
    EvaluationResult,
    EvaluationStatus,
    FixtureMiss,
    FixtureResponse,
    FixtureToolExecutor,
    HarnessVariant,
    PromotionAssessment,
    PromotionGate,
    PromotionStatus,
    PromotionThresholds,
    ProjectionPolicy,
    RuntimeTrajectoryProjector,
    ScorerResult,
    TrajectoryProjector,
)
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationRevisionSet,
    EvaluationScope,
)
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="case_connector_selection",
        suite_id="suite_f1",
        revision="r1",
        task_family="connector_selection",
        input_ref="artifact_input_1",
        fixture_catalog_ref="fixture_catalog_r1",
        scorer_set_id="scorers_r1",
    )


def _revisions() -> EvaluationRevisionSet:
    return EvaluationRevisionSet(
        code_revision="code-r1",
        model_revision="model-r1",
        prompt_revision="prompt-r1",
        tool_revision="tool-r1",
        policy_revision="policy-r1",
        fixture_revision="fixture-r1",
        scorer_revision="scorer-r1",
    )


def _variant(variant_id: str = "control") -> HarnessVariant:
    return HarnessVariant(
        variant_id=variant_id,
        revision="r1",
        prompt_plan_revision="prompt_r1",
        capability_policy_revision="capability_r1",
        context_policy_revision="context_r1",
        model_route_revision="model_r1",
    )


def _event(sequence_no: int, payload: dict[str, object]) -> RuntimeEventEnvelope:
    return RuntimeEventEnvelope(
        run_id="run_1",
        conversation_id="conversation_1",
        source=StreamEventSource.TOOL,
        event_type=RuntimeApiEventType.TOOL_CALL,
        trace_id="trace_1",
        sequence_no=sequence_no,
        activity_kind=RuntimeActivityKind.TOOL,
        payload=payload,
    )


def test_projector_retains_only_observable_identifiers_and_digest() -> None:
    payload = {
        "tool_name": "drive.search",
        "arguments": {"query": "private raw customer query"},
        "access_token": "must-not-project",
    }
    manifest = TrajectoryProjector(redaction_policy_revision="redaction_r1").project(
        run_id="run_1",
        variant_id="control",
        events=(_event(1, payload),),
        harness_revisions={"prompt": "prompt_r1"},
    )

    assert manifest.ordered_steps[0].capability_id == "drive.search"
    assert manifest.ordered_steps[0].payload_digest == canonical_json_sha256(payload)
    assert "private raw customer query" not in manifest.model_dump_json()
    assert "must-not-project" not in manifest.model_dump_json()


def test_projector_retains_only_closed_task_policy_journal_vocabulary() -> None:
    payload = {
        "record": {
            "record_kind": "admission_recorded",
            "disposition": "blocked",
            "reason_codes": ["exact_duplicate"],
            "exhausted_dimensions": ["tool_calls"],
            "plan_body": "private task plan must never be projected",
            "arguments": {"query": "private customer query"},
        }
    }
    manifest = TrajectoryProjector(redaction_policy_revision="redaction_r1").project(
        run_id="run_1",
        variant_id="control",
        events=(_event(1, payload),),
    )

    step = manifest.ordered_steps[0]
    assert step.policy_record_kind == "admission_recorded"
    assert step.policy_disposition == "blocked"
    assert step.policy_reason_codes == ("exact_duplicate",)
    assert step.policy_exhausted_dimensions == ("tool_calls",)
    assert "private task plan" not in manifest.model_dump_json()
    assert "private customer query" not in manifest.model_dump_json()


def test_projector_rejects_a_gap_in_the_canonical_event_timeline() -> None:
    with pytest.raises(ValueError, match=r"expected 2, got 3"):
        TrajectoryProjector(redaction_policy_revision="redaction_r1").project(
            run_id="run_1",
            variant_id="control",
            events=(
                _event(1, {"tool_name": "drive.search"}),
                _event(3, {"tool_name": "drive.open"}),
            ),
        )


@pytest.mark.asyncio
async def test_fixture_executor_is_closed_and_never_falls_back_to_live_transport() -> (
    None
):
    arguments = {"query": "quarterly plan"}
    request_digest = FixtureToolExecutor.request_digest(
        capability_id="drive.search", arguments=arguments
    )
    fixtures = FixtureToolExecutor(
        (
            FixtureResponse(
                capability_id="drive.search",
                request_digest=request_digest,
                response_ref="artifact_fixture_1",
                response_digest=canonical_json_sha256({"fixture": 1}),
            ),
        )
    )

    assert (
        await fixtures.execute(capability_id="drive.search", arguments=arguments)
    ).response_ref == "artifact_fixture_1"
    with pytest.raises(FixtureMiss):
        await fixtures.execute(
            capability_id="drive.search", arguments={"query": "miss"}
        )


class _PassingScorer:
    def score(self, *, case: EvaluationCase, trajectory: object) -> ScorerResult:
        del case, trajectory
        return ScorerResult(
            scorer_id="required_capability",
            score=1,
            passed=True,
            hard_gate=True,
            reason_code="capability_present",
        )


class _FailingScorer:
    def score(self, *, case: EvaluationCase, trajectory: object) -> ScorerResult:
        del case, trajectory
        return ScorerResult(
            scorer_id="effect_safety",
            score=0,
            passed=False,
            hard_gate=True,
            reason_code="unauthorized_effect",
        )


@pytest.mark.asyncio
async def test_hard_gate_failure_blocks_promotion() -> None:
    projector = TrajectoryProjector(redaction_policy_revision="redaction_r1")

    async def executor(*, case, variant, fixtures):
        del case, fixtures
        return projector.project(
            run_id=None,
            variant_id=variant.variant_id,
            events=(),
        )

    repository = InMemoryEvaluationRepository()
    result = await DeterministicEvaluationRunner(
        repository=repository,
        scope=EvaluationScope(profile_id="hermetic-test"),
        executor=executor,
        scorers=(_PassingScorer(), _FailingScorer()),
        revisions=_revisions(),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).run(
        case=_case(),
        variant=_variant("candidate"),
        fixtures=FixtureToolExecutor(()),
    )

    assert result.status is EvaluationStatus.FAILED
    assert result.hard_gate_failures == ("unauthorized_effect",)
    gate = PromotionGate()
    assessment = gate.evaluate(
        candidate_variant_id="candidate",
        control_variant_id="control",
        candidate_results=(result,),
        control_results=(
            _result(
                case_id=result.case_id,
                variant_id="control",
                status=EvaluationStatus.SUCCEEDED,
            ),
        ),
        case_task_families={result.case_id: "connector_selection"},
        thresholds=PromotionThresholds(
            revision="thresholds_r1",
            minimum_paired_cases=1,
        ),
    )
    decision = gate.decide(
        decision_id="decision_1",
        candidate_variant_id="candidate",
        control_variant_id="control",
        suite_revisions=("r1",),
        thresholds_revision="thresholds_r1",
        report_ref="artifact_report_1",
        actor="user_1",
        rationale="Synthetic safety suite.",
        assessment=assessment,
    )
    assert "candidate_hard_gate_failure" in assessment.reason_codes
    assert decision.status is PromotionStatus.REJECTED
    assert decision.assessment_digest == assessment.assessment_digest


@pytest.mark.asyncio
async def test_fixture_miss_is_inconclusive_not_a_candidate_pass() -> None:
    async def executor(*, case, variant, fixtures):
        del case, variant
        await fixtures.execute(
            capability_id="drive.search", arguments={"query": "missing"}
        )
        raise AssertionError("fixture miss must have raised")

    result = await DeterministicEvaluationRunner(
        repository=InMemoryEvaluationRepository(),
        scope=EvaluationScope(profile_id="hermetic-test"),
        executor=executor,
        scorers=(),
        revisions=_revisions(),
    ).run(case=_case(), variant=_variant(), fixtures=FixtureToolExecutor(()))

    assert result.status is EvaluationStatus.INCONCLUSIVE
    assert result.hard_gate_failures == ("fixture_miss",)


class _EventStore:
    def __init__(self, events: tuple[RuntimeEventEnvelope, ...]) -> None:
        self.events = events
        self.calls = 0

    async def list_events_after(self, **_kwargs) -> tuple[RuntimeEventEnvelope, ...]:
        self.calls += 1
        return self.events


@pytest.mark.asyncio
async def test_runtime_projection_is_opt_in_and_reads_existing_event_store() -> None:
    store = _EventStore((_event(1, {"tool_name": "drive.search"}),))
    projector = RuntimeTrajectoryProjector(
        event_store=store,  # type: ignore[arg-type]
        projector=TrajectoryProjector(redaction_policy_revision="redaction_r1"),
    )
    disabled = await projector.project_run(
        org_id="org_1",
        run_id="run_1",
        variant_id="control",
        policy=ProjectionPolicy(revision="policy_r1"),
        is_development_run=False,
    )
    assert disabled is None
    assert store.calls == 0

    projected = await projector.project_run(
        org_id="org_1",
        run_id="run_1",
        variant_id="control",
        policy=ProjectionPolicy(
            revision="policy_r2", enabled=True, user_consented=True
        ),
        is_development_run=False,
    )
    assert projected is not None
    assert projected.run_id == "run_1"
    assert store.calls == 1


def _result(
    *,
    case_id: str,
    variant_id: str,
    status: EvaluationStatus = EvaluationStatus.SUCCEEDED,
    hard_gate_failures: tuple[str, ...] = (),
    total_cost: float = 1,
    end_to_end_ms: int = 100,
) -> EvaluationResult:
    values: dict[str, object] = {
        "evaluation_run_id": f"eval_{variant_id}_{case_id}",
        "suite_run_id": "suite-run-r1",
        "case_id": case_id,
        "case_revision": "case-r1",
        "variant_id": variant_id,
        "variant_revision": "variant-r1",
        "scorer_set_id": "scorer-set-r1",
        "revisions": _revisions(),
        "status": status,
        "scorer_results": (),
        "hard_gate_failures": hard_gate_failures,
        "total_cost": total_cost,
        "model_turns": 1,
        "tool_calls": 1,
        "end_to_end_ms": end_to_end_ms,
        "first_useful_answer_ms": end_to_end_ms,
    }
    return EvaluationResult(
        **values,
        result_digest=EvaluationResult.digest_for(**values),
    )


def test_promotion_assessment_requires_exact_paired_cases() -> None:
    assessment = PromotionGate().evaluate(
        candidate_variant_id="candidate",
        control_variant_id="control",
        candidate_results=(_result(case_id="case_1", variant_id="candidate"),),
        control_results=(_result(case_id="case_2", variant_id="control"),),
        case_task_families={
            "case_1": "connector_selection",
            "case_2": "connector_selection",
        },
        thresholds=PromotionThresholds(
            revision="thresholds_r1",
            minimum_paired_cases=1,
        ),
    )

    assert not assessment.passed
    assert assessment.paired_case_count == 0
    assert "unpaired_case_set" in assessment.reason_codes
    assert "minimum_paired_cases_not_met" in assessment.reason_codes


def test_promotion_assessment_rejects_mismatched_case_revisions() -> None:
    candidate = _result(case_id="case_1", variant_id="candidate").model_copy(
        update={"case_revision": "case-r2"}
    )
    control = _result(case_id="case_1", variant_id="control")

    assessment = PromotionGate().evaluate(
        candidate_variant_id="candidate",
        control_variant_id="control",
        candidate_results=(candidate,),
        control_results=(control,),
        case_task_families={"case_1": "connector_selection"},
        thresholds=PromotionThresholds(
            revision="thresholds_r1",
            minimum_paired_cases=1,
        ),
    )

    assert not assessment.passed
    assert "case_revision_mismatch" in assessment.reason_codes


def test_promotion_assessment_passes_non_regressing_candidate() -> None:
    candidate = tuple(
        _result(
            case_id=f"case_{index}",
            variant_id="candidate",
            total_cost=0.9,
            end_to_end_ms=90,
        )
        for index in range(1, 6)
    )
    control = tuple(
        _result(case_id=f"case_{index}", variant_id="control") for index in range(1, 6)
    )
    assessment = PromotionGate().evaluate(
        candidate_variant_id="candidate",
        control_variant_id="control",
        candidate_results=candidate,
        control_results=control,
        case_task_families={
            f"case_{index}": "connector_selection" for index in range(1, 6)
        },
        thresholds=PromotionThresholds(
            revision="thresholds_r1",
            minimum_paired_cases=5,
            protected_task_families=frozenset({"connector_selection"}),
        ),
    )

    assert assessment.passed
    assert assessment.reason_codes == ()
    assert assessment.success_rate_delta_lower_bound == 0
    assert assessment.protected_family_lower_bounds == {"connector_selection": 0}
    assert assessment.mean_cost_ratio == pytest.approx(0.9)
    assert assessment.p95_latency_ratio == pytest.approx(0.9)


def test_promotion_assessment_rejects_quality_cost_and_latency_regressions() -> None:
    candidate = (
        _result(
            case_id="case_1",
            variant_id="candidate",
            status=EvaluationStatus.FAILED,
            total_cost=2,
            end_to_end_ms=300,
        ),
        _result(
            case_id="case_2",
            variant_id="candidate",
            total_cost=2,
            end_to_end_ms=300,
        ),
    )
    control = (
        _result(case_id="case_1", variant_id="control"),
        _result(case_id="case_2", variant_id="control"),
    )
    assessment = PromotionGate().evaluate(
        candidate_variant_id="candidate",
        control_variant_id="control",
        candidate_results=candidate,
        control_results=control,
        case_task_families={
            "case_1": "protected",
            "case_2": "protected",
        },
        thresholds=PromotionThresholds(
            revision="thresholds_r1",
            minimum_paired_cases=2,
            protected_task_families=frozenset({"protected"}),
        ),
    )

    assert not assessment.passed
    assert {
        "success_regression",
        "protected_family_regression",
        "mean_cost_ratio_exceeded",
        "p95_latency_ratio_exceeded",
    }.issubset(assessment.reason_codes)


def test_promotion_decision_rejects_mismatched_assessment_binding() -> None:
    values: dict[str, object] = {
        "candidate_variant_id": "other_candidate",
        "control_variant_id": "control",
        "thresholds_revision": "thresholds_r1",
        "paired_case_count": 1,
        "candidate_success_rate": 1,
        "control_success_rate": 1,
        "success_rate_delta": 0,
        "success_rate_delta_lower_bound": 0,
        "mean_cost_ratio": 1,
        "p95_latency_ratio": 1,
        "protected_family_lower_bounds": {},
        "reason_codes": (),
        "passed": True,
    }
    assessment = PromotionAssessment(
        **values,
        assessment_digest=PromotionAssessment.digest_for(**values),
    )

    with pytest.raises(ValueError, match="candidate variant"):
        PromotionGate().decide(
            decision_id="decision_1",
            candidate_variant_id="candidate",
            control_variant_id="control",
            suite_revisions=("r1",),
            thresholds_revision="thresholds_r1",
            report_ref="artifact_report_1",
            actor="user_1",
            rationale="Binding test.",
            assessment=assessment,
        )
