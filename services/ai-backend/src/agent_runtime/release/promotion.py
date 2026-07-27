"""Paired candidate/control promotion reports with non-overridable hard gates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation import PromotionGate
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationResult,
    EvaluationRevisionSet,
    PairedEvaluationReport,
    PromotionAssessment,
    PromotionThresholds,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.harness_quality.evaluation_contracts import EvaluationScope


class PromotionEvaluationInput(RuntimeContract):
    """Exact immutable evidence required to build one paired report."""

    report_id: Annotated[str, Field(min_length=1, max_length=160)]
    candidate_variant_id: Annotated[str, Field(min_length=1, max_length=160)]
    control_variant_id: Annotated[str, Field(min_length=1, max_length=160)]
    candidate_suite_run_ids: tuple[str, ...]
    control_suite_run_ids: tuple[str, ...]
    candidate_revisions: EvaluationRevisionSet
    control_revisions: EvaluationRevisionSet
    candidate_results: tuple[EvaluationResult, ...]
    control_results: tuple[EvaluationResult, ...]
    case_task_families: dict[str, str]
    thresholds: PromotionThresholds
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("candidate_suite_run_ids", "control_suite_run_ids")
    @classmethod
    def _suite_run_ids_are_canonical(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("suite run ids must be non-empty, unique, and sorted")
        return value


class PairedPromotionEvaluator:
    """Construct the persisted paired report through existing F1 gate math."""

    def evaluate(self, request: PromotionEvaluationInput) -> PairedEvaluationReport:
        candidate_case_ids = {result.case_id for result in request.candidate_results}
        control_case_ids = {result.case_id for result in request.control_results}
        missing_candidate = tuple(sorted(control_case_ids - candidate_case_ids))
        missing_control = tuple(sorted(candidate_case_ids - control_case_ids))

        assessment = PromotionGate().evaluate(
            candidate_variant_id=request.candidate_variant_id,
            control_variant_id=request.control_variant_id,
            candidate_results=request.candidate_results,
            control_results=request.control_results,
            case_task_families=request.case_task_families,
            thresholds=request.thresholds,
        )
        assessment = self._apply_integrity_and_hard_gates(
            assessment=assessment,
            request=request,
        )
        values: dict[str, object] = {
            "report_id": request.report_id,
            "candidate_suite_run_ids": request.candidate_suite_run_ids,
            "control_suite_run_ids": request.control_suite_run_ids,
            "candidate_revisions": request.candidate_revisions,
            "control_revisions": request.control_revisions,
            "missing_candidate_case_ids": missing_candidate,
            "missing_control_case_ids": missing_control,
            # No result is silently dropped. Incomplete results remain paired
            # hard failures in the assessment instead of becoming exclusions.
            "excluded_case_ids": (),
            "assessment": assessment,
            "generated_at": request.generated_at,
        }
        return PairedEvaluationReport(
            **values,
            report_digest=PairedEvaluationReport.digest_for(**values),
        )

    @staticmethod
    def _apply_integrity_and_hard_gates(
        *,
        assessment: PromotionAssessment,
        request: PromotionEvaluationInput,
    ) -> PromotionAssessment:
        """Reject mismatched evidence and keep hard failures non-overridable."""

        reasons = set(assessment.reason_codes)
        if any(result.hard_gate_failures for result in request.candidate_results):
            reasons.add("candidate_hard_safety_or_conformance_failure")
        if any(
            score.hard_gate and not score.passed
            for result in request.candidate_results
            for score in result.scorer_results
        ):
            reasons.add("candidate_deterministic_hard_scorer_failure")
        for role, results, revisions, suite_run_ids in (
            (
                "candidate",
                request.candidate_results,
                request.candidate_revisions,
                request.candidate_suite_run_ids,
            ),
            (
                "control",
                request.control_results,
                request.control_revisions,
                request.control_suite_run_ids,
            ),
        ):
            if any(result.revisions != revisions for result in results):
                reasons.add(f"{role}_revision_set_mismatch")
            if any(result.suite_run_id not in suite_run_ids for result in results):
                reasons.add(f"{role}_suite_run_mismatch")
        if (
            request.candidate_revisions.fixture_revision
            != request.control_revisions.fixture_revision
        ):
            reasons.add("paired_fixture_revision_mismatch")
        if (
            request.candidate_revisions.scorer_revision
            != request.control_revisions.scorer_revision
        ):
            reasons.add("paired_scorer_revision_mismatch")
        reason_codes = tuple(sorted(reasons))
        if reason_codes == assessment.reason_codes:
            return assessment
        values: dict[str, object] = {
            "candidate_variant_id": assessment.candidate_variant_id,
            "control_variant_id": assessment.control_variant_id,
            "thresholds_revision": assessment.thresholds_revision,
            "paired_case_count": assessment.paired_case_count,
            "candidate_success_rate": assessment.candidate_success_rate,
            "control_success_rate": assessment.control_success_rate,
            "success_rate_delta": assessment.success_rate_delta,
            "success_rate_delta_lower_bound": (
                assessment.success_rate_delta_lower_bound
            ),
            "mean_cost_ratio": assessment.mean_cost_ratio,
            "p95_latency_ratio": assessment.p95_latency_ratio,
            "protected_family_lower_bounds": (assessment.protected_family_lower_bounds),
            "reason_codes": reason_codes,
            "passed": False,
        }
        return PromotionAssessment(
            **values,
            assessment_digest=PromotionAssessment.digest_for(**values),
        )


class PromotionReportService:
    """Persist a pure gate result; this service cannot activate a manifest."""

    def __init__(self, *, repository: EvaluationRepositoryPort) -> None:
        self._repository = repository
        self._evaluator = PairedPromotionEvaluator()

    async def evaluate_and_store(
        self,
        *,
        scope: EvaluationScope,
        request: PromotionEvaluationInput,
    ) -> PairedEvaluationReport:
        report = self._evaluator.evaluate(request)
        await self._repository.put_paired_report(scope, report)
        return report


__all__ = (
    "PairedPromotionEvaluator",
    "PromotionEvaluationInput",
    "PromotionReportService",
)
