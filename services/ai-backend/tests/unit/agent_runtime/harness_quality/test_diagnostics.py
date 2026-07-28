from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.harness_quality.diagnostics import EvaluationDiagnosticsService
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationProjectionJob,
    EvaluationResult,
    EvaluationRevisionSet,
    EvaluationScope,
    EvaluationStatus,
    ProjectionJobStatus,
    PromotionDecision,
    PromotionStatus,
    ScorerResult,
)
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)


_NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
_SCOPE = EvaluationScope(profile_id="local-profile", project_id="project-1")


def _result() -> EvaluationResult:
    values: dict[str, object] = {
        "evaluation_run_id": "evaluation-1",
        "suite_run_id": "suite-run-1",
        "case_id": "case-1",
        "case_revision": "case-r1",
        "variant_id": "candidate",
        "variant_revision": "candidate-r1",
        "scorer_set_id": "hard-scorers-r1",
        "revisions": EvaluationRevisionSet(
            code_revision="code-r1",
            model_revision="model-r1",
            prompt_revision="prompt-r1",
            tool_revision="tool-r1",
            policy_revision="policy-r1",
            fixture_revision="fixture-r1",
            scorer_revision="scorer-r1",
        ),
        "status": EvaluationStatus.FAILED,
        "scorer_results": (
            ScorerResult(
                scorer_id="effect-safety",
                score=0,
                passed=False,
                hard_gate=True,
                reason_code="unauthorized_effect",
            ),
        ),
        "hard_gate_failures": ("unauthorized_effect",),
        "total_cost": 0.25,
        "model_turns": 2,
        "tool_calls": 3,
        "end_to_end_ms": 120,
        "first_useful_answer_ms": None,
    }
    return EvaluationResult(
        **values,
        result_digest=EvaluationResult.digest_for(**values),
    )


def _skipped_job() -> EvaluationProjectionJob:
    values: dict[str, object] = {
        "job_id": "projection-1",
        "source_org_id": "runtime-org",
        "source_run_id": "source-run-1",
        "variant_id": "variant_unavailable",
        "policy_revision": "projection-policy-r1",
        "terminal_sequence_no": 2,
        "status": ProjectionJobStatus.SKIPPED,
        "next_sequence_no": 1,
        "attempt_count": 0,
        "lease_owner_digest": None,
        "lease_expires_at": None,
        "trajectory_id": None,
        "failure_reason_code": "control_snapshot_missing",
        "version": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    return EvaluationProjectionJob(
        **values,
        job_digest=EvaluationProjectionJob.digest_for(**values),
    )


async def test_diagnostics_are_bounded_content_free_and_exact_revision_grouped() -> (
    None
):
    repository = InMemoryEvaluationRepository()
    case = EvaluationCase(
        case_id="case-1",
        suite_id="suite-1",
        revision="case-r1",
        task_family="conflicting_writes",
        input_ref="synthetic-input-1",
        fixture_catalog_ref="fixture-r1",
        scorer_set_id="hard-scorers-r1",
    )
    await repository.put_case(_SCOPE, case)
    await repository.put_projection_job(_SCOPE, _skipped_job())
    await repository.put_evaluation_result(_SCOPE, _result())
    await repository.put_promotion_decision(
        _SCOPE,
        PromotionDecision(
            decision_id="decision-1",
            candidate_variant_id="candidate",
            control_variant_id="control",
            suite_revisions=("suite-r1",),
            thresholds_revision="thresholds-r1",
            report_ref="paired-report-1",
            assessment_digest="a" * 64,
            status=PromotionStatus.REJECTED,
            actor="local-user",
            decided_at=_NOW,
            rationale="hard safety gate failed",
        ),
    )

    snapshot = await EvaluationDiagnosticsService(
        repository=repository,
        scope=_SCOPE,
        maximum_items=10,
        clock=lambda: _NOW,
    ).snapshot()

    assert snapshot.projection.status_counts["skipped"] == 1
    assert snapshot.projection.reason_counts == {"control_snapshot_missing": 1}
    assert snapshot.projection.backlog_jobs == 0
    assert snapshot.evaluation.status_counts["failed"] == 1
    assert snapshot.evaluation.reason_counts == {"unauthorized_effect": 1}
    group = snapshot.evaluation.groups[0]
    assert group.task_family == "conflicting_writes"
    assert group.variant_id == "candidate"
    assert group.scorer_scores.minimum == 0
    assert group.total_cost.mean == 0.25
    assert snapshot.promotion.status_counts == {"rejected": 1}
    assert snapshot.promotion.history[0].report_ref == "paired-report-1"
    serialized = snapshot.model_dump_json()
    assert "synthetic-input-1" not in serialized
    assert "hard safety gate failed" not in serialized
    assert "local-user" not in serialized


async def test_diagnostics_mark_a_reached_sample_bound() -> None:
    repository = InMemoryEvaluationRepository()
    await repository.put_projection_job(_SCOPE, _skipped_job())

    snapshot = await EvaluationDiagnosticsService(
        repository=repository,
        scope=_SCOPE,
        maximum_items=1,
        clock=lambda: _NOW,
    ).snapshot()

    assert snapshot.maximum_items_per_section == 1
    assert snapshot.projection.sample_limit_reached is True
