"""Bounded, content-free local diagnostics over the F1 evaluation repository."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from enum import StrEnum
from math import ceil
from statistics import mean

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationProjectionJob,
    EvaluationResult,
    EvaluationScope,
    EvaluationStatus,
    ProjectionJobStatus,
    PromotionDecision,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort


class MetricDistribution(RuntimeContract):
    """A bounded aggregate that cannot reveal an underlying case payload."""

    count: int = Field(ge=0)
    minimum: float | None = None
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    maximum: float | None = None


class ProjectionDiagnostics(RuntimeContract):
    sampled_jobs: int = Field(ge=0)
    sample_limit_reached: bool
    backlog_jobs: int = Field(ge=0)
    status_counts: dict[str, int]
    reason_counts: dict[str, int]

    @field_validator("status_counts", "reason_counts")
    @classmethod
    def _counts_are_canonical(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("diagnostic counts cannot be negative")
        return dict(sorted(value.items()))


class EvaluationGroupDiagnostics(RuntimeContract):
    task_family: str = Field(min_length=1, max_length=80)
    variant_id: str = Field(min_length=1, max_length=160)
    sampled_results: int = Field(ge=1)
    status_counts: dict[str, int]
    scorer_scores: MetricDistribution
    total_cost: MetricDistribution
    model_turns: MetricDistribution
    tool_calls: MetricDistribution
    end_to_end_ms: MetricDistribution

    @field_validator("status_counts")
    @classmethod
    def _status_counts_are_canonical(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("diagnostic counts cannot be negative")
        return dict(sorted(value.items()))


class EvaluationDiagnostics(RuntimeContract):
    sampled_results: int = Field(ge=0)
    sample_limit_reached: bool
    status_counts: dict[str, int]
    reason_counts: dict[str, int]
    groups: tuple[EvaluationGroupDiagnostics, ...]

    @field_validator("status_counts", "reason_counts")
    @classmethod
    def _counts_are_canonical(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("diagnostic counts cannot be negative")
        return dict(sorted(value.items()))


class PromotionHistoryEntry(RuntimeContract):
    decision_id: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=40)
    candidate_variant_id: str = Field(min_length=1, max_length=160)
    control_variant_id: str = Field(min_length=1, max_length=160)
    report_ref: str = Field(min_length=1, max_length=160)
    thresholds_revision: str = Field(min_length=1, max_length=160)
    decided_at: datetime


class PromotionDiagnostics(RuntimeContract):
    sampled_decisions: int = Field(ge=0)
    sample_limit_reached: bool
    status_counts: dict[str, int]
    history: tuple[PromotionHistoryEntry, ...]
    active_manifest_ref: str | None = Field(default=None, max_length=512)

    @field_validator("status_counts")
    @classmethod
    def _status_counts_are_canonical(cls, value: dict[str, int]) -> dict[str, int]:
        if any(count < 0 for count in value.values()):
            raise ValueError("diagnostic counts cannot be negative")
        return dict(sorted(value.items()))


class EvaluationDiagnosticSnapshot(RuntimeContract):
    """One local read model containing aggregates and opaque references only."""

    scope_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    generated_at: datetime
    maximum_items_per_section: int = Field(ge=1, le=1_000)
    projection: ProjectionDiagnostics
    evaluation: EvaluationDiagnostics
    promotion: PromotionDiagnostics


class EvaluationDiagnosticsService:
    """Build an O(N log N), bounded diagnostic snapshot for one fixed scope."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        scope: EvaluationScope,
        maximum_items: int = 500,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if maximum_items < 1 or maximum_items > 1_000:
            raise ValueError("diagnostic item bound must be within [1, 1000]")
        self._repository = repository
        self._scope = scope
        self._maximum_items = maximum_items
        self._clock = clock

    async def snapshot(self) -> EvaluationDiagnosticSnapshot:
        jobs, results, cases, decisions, active = await asyncio.gather(
            self._repository.list_projection_jobs(
                self._scope,
                limit=self._maximum_items,
            ),
            self._repository.list_evaluation_results(
                self._scope,
                limit=self._maximum_items,
            ),
            self._repository.list_cases(
                self._scope,
                limit=self._maximum_items,
            ),
            self._repository.list_promotion_decisions(
                self._scope,
                limit=self._maximum_items,
            ),
            self._repository.get_active_harness_manifest(self._scope),
        )
        case_families = {
            (case.case_id, case.revision): case.task_family for case in cases
        }
        return EvaluationDiagnosticSnapshot(
            scope_digest=self._scope.storage_key,
            generated_at=self._clock(),
            maximum_items_per_section=self._maximum_items,
            projection=_projection_diagnostics(
                jobs,
                maximum=self._maximum_items,
            ),
            evaluation=_evaluation_diagnostics(
                results,
                case_families=case_families,
                maximum=self._maximum_items,
            ),
            promotion=_promotion_diagnostics(
                decisions,
                active_manifest_ref=(
                    None
                    if active is None
                    else (
                        f"harness-manifest://{active.manifest_id}/"
                        f"{active.manifest_revision}/sha256/"
                        f"{active.manifest_payload_digest}"
                    )
                ),
                maximum=self._maximum_items,
            ),
        )


def _projection_diagnostics(
    jobs: Sequence[EvaluationProjectionJob],
    *,
    maximum: int,
) -> ProjectionDiagnostics:
    statuses = Counter(getattr(job, "status").value for job in jobs)
    reasons = Counter(
        reason
        for job in jobs
        if (reason := getattr(job, "failure_reason_code")) is not None
    )
    return ProjectionDiagnostics(
        sampled_jobs=len(jobs),
        sample_limit_reached=len(jobs) == maximum,
        backlog_jobs=(
            statuses[ProjectionJobStatus.PENDING.value]
            + statuses[ProjectionJobStatus.RUNNING.value]
        ),
        status_counts=_all_status_counts(ProjectionJobStatus, statuses),
        reason_counts=dict(reasons),
    )


def _evaluation_diagnostics(
    results: Sequence[EvaluationResult],
    *,
    case_families: dict[tuple[str, str], str],
    maximum: int,
) -> EvaluationDiagnostics:
    statuses = Counter(result.status.value for result in results)
    reasons: Counter[str] = Counter()
    grouped: dict[tuple[str, str], list[EvaluationResult]] = defaultdict(list)
    for result in results:
        reasons.update(
            set(result.hard_gate_failures)
            | {
                scorer.reason_code
                for scorer in result.scorer_results
                if not scorer.passed
            }
        )
        family = case_families.get(
            (result.case_id, result.case_revision),
            "case_revision_not_in_sample",
        )
        grouped[(family, result.variant_id)].append(result)
    groups = tuple(
        _evaluation_group(task_family=key[0], variant_id=key[1], results=values)
        for key, values in sorted(grouped.items())
    )
    return EvaluationDiagnostics(
        sampled_results=len(results),
        sample_limit_reached=len(results) == maximum,
        status_counts=_all_status_counts(EvaluationStatus, statuses),
        reason_counts=dict(reasons),
        groups=groups,
    )


def _evaluation_group(
    *,
    task_family: str,
    variant_id: str,
    results: Sequence[EvaluationResult],
) -> EvaluationGroupDiagnostics:
    return EvaluationGroupDiagnostics(
        task_family=task_family,
        variant_id=variant_id,
        sampled_results=len(results),
        status_counts=_all_status_counts(
            EvaluationStatus,
            Counter(result.status.value for result in results),
        ),
        scorer_scores=_distribution(
            scorer.score for result in results for scorer in result.scorer_results
        ),
        total_cost=_distribution(result.total_cost for result in results),
        model_turns=_distribution(result.model_turns for result in results),
        tool_calls=_distribution(result.tool_calls for result in results),
        end_to_end_ms=_distribution(result.end_to_end_ms for result in results),
    )


def _promotion_diagnostics(
    decisions: Sequence[PromotionDecision],
    *,
    active_manifest_ref: str | None,
    maximum: int,
) -> PromotionDiagnostics:
    ordered = tuple(
        sorted(
            decisions,
            key=lambda item: (item.decided_at, item.decision_id),
            reverse=True,
        )
    )
    return PromotionDiagnostics(
        sampled_decisions=len(decisions),
        sample_limit_reached=len(decisions) == maximum,
        status_counts=dict(Counter(item.status.value for item in decisions)),
        history=tuple(
            PromotionHistoryEntry(
                decision_id=item.decision_id,
                status=item.status.value,
                candidate_variant_id=item.candidate_variant_id,
                control_variant_id=item.control_variant_id,
                report_ref=item.report_ref,
                thresholds_revision=item.thresholds_revision,
                decided_at=item.decided_at,
            )
            for item in ordered
        ),
        active_manifest_ref=active_manifest_ref,
    )


def _distribution(values: Iterable[int | float]) -> MetricDistribution:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return MetricDistribution(count=0)
    return MetricDistribution(
        count=len(ordered),
        minimum=ordered[0],
        mean=round(mean(ordered), 6),
        p50=_nearest_rank(ordered, 0.5),
        p95=_nearest_rank(ordered, 0.95),
        maximum=ordered[-1],
    )


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    return values[max(0, ceil(quantile * len(values)) - 1)]


def _all_status_counts(
    enum_type: type[StrEnum],
    counts: Counter[str],
) -> dict[str, int]:
    return {member.value: counts[member.value] for member in enum_type}


__all__ = (
    "EvaluationDiagnosticSnapshot",
    "EvaluationDiagnosticsService",
)
