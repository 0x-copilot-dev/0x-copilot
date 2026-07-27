"""Hermetic F1 evaluation mechanics.

Production runs keep using their existing event and usage stores.  This module
projects redacted manifests and evaluates only through exact fixture lookups;
there is intentionally no ambient HTTP client, connector, MCP client, or
effect executor in the call graph.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from math import ceil, sqrt
from statistics import NormalDist, mean, stdev
from typing import Protocol
from uuid import uuid4

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationMode,
    EvaluationRevisionSet,
    EvaluationResult,
    EvaluationScope,
    EvaluationStatus,
    FixtureResponse,
    HarnessVariant,
    PromotionAssessment,
    PromotionDecision,
    PromotionStatus,
    PromotionThresholds,
    ProjectionPolicy,
    ScorerResult,
    TrajectoryManifest,
    TrajectoryStep,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.api.ports import EventStorePort
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_api.schemas import RuntimeEventEnvelope


class FixtureMiss(LookupError):
    """No exact recorded fixture exists for a requested capability call."""


class EvaluationScorer(Protocol):
    """A deterministic scorer over a redacted trajectory."""

    def score(
        self,
        *,
        case: EvaluationCase,
        trajectory: TrajectoryManifest,
    ) -> ScorerResult: ...


class TrajectoryExecutor(Protocol):
    """Runs one case without admitting an effectful runtime dependency."""

    async def __call__(
        self,
        *,
        case: EvaluationCase,
        variant: "HarnessVariant",
        fixtures: "FixtureToolExecutor",
    ) -> TrajectoryManifest: ...


class FixtureToolExecutor:
    """Closed fixture lookup that cannot contact a live capability.

    Request identity is the canonical digest of capability ID plus structured
    arguments.  A missing fixture is a deterministic failure, not a fallback.
    """

    def __init__(self, fixtures: Iterable[FixtureResponse]) -> None:
        indexed: dict[tuple[str, str], FixtureResponse] = {}
        for fixture in fixtures:
            key = (fixture.capability_id, fixture.request_digest)
            if key in indexed and indexed[key] != fixture:
                raise ValueError("conflicting fixture response for exact request")
            indexed[key] = fixture
        self._fixtures = indexed

    @staticmethod
    def request_digest(*, capability_id: str, arguments: Mapping[str, object]) -> str:
        return canonical_json_sha256(
            {"capability_id": capability_id, "arguments": dict(arguments)}
        )

    async def execute(
        self,
        *,
        capability_id: str,
        arguments: Mapping[str, object],
    ) -> FixtureResponse:
        digest = self.request_digest(capability_id=capability_id, arguments=arguments)
        fixture = self._fixtures.get((capability_id, digest))
        if fixture is None:
            raise FixtureMiss(f"no fixture for capability {capability_id!r}")
        return fixture


class TrajectoryProjector:
    """Project ordered runtime envelopes into content-free manifests.

    The event body is reduced to a canonical digest.  Only explicitly stable
    observable identifiers are carried forward, which prevents this projection
    from becoming an unbounded transcript or connector-payload store.
    """

    _CAPABILITY_KEYS = ("capability_id", "tool_name", "tool", "operation")

    def __init__(self, *, redaction_policy_revision: str) -> None:
        if not redaction_policy_revision.strip():
            raise ValueError("redaction_policy_revision must be non-empty")
        self._redaction_policy_revision = redaction_policy_revision

    def project(
        self,
        *,
        run_id: str | None,
        variant_id: str,
        events: Sequence[RuntimeEventEnvelope],
        evidence_refs: Sequence[str] = (),
        usage_summary: Mapping[str, int | float] | None = None,
        harness_revisions: Mapping[str, str] | None = None,
        case_id: str | None = None,
        trajectory_id: str | None = None,
    ) -> TrajectoryManifest:
        ordered = tuple(sorted(events, key=lambda event: event.sequence_no))
        self._validate_contiguous(ordered)
        steps = tuple(self._step(event) for event in ordered)
        values: dict[str, object] = {
            "trajectory_id": trajectory_id or f"traj_{uuid4().hex}",
            "run_id": run_id,
            "case_id": case_id,
            "variant_id": variant_id,
            "ordered_steps": steps,
            "evidence_refs": tuple(evidence_refs),
            "usage_summary": dict(usage_summary or {}),
            "redaction_policy_revision": self._redaction_policy_revision,
            "harness_revisions": dict(harness_revisions or {}),
        }
        digest = TrajectoryManifest.digest_for(**values)
        return TrajectoryManifest(**values, manifest_digest=digest)

    @classmethod
    def _step(cls, event: RuntimeEventEnvelope) -> TrajectoryStep:
        payload = dict(event.payload)
        capability_id = next(
            (
                str(payload[key])
                for key in cls._CAPABILITY_KEYS
                if isinstance(payload.get(key), str) and payload[key].strip()
            ),
            None,
        )
        return TrajectoryStep(
            sequence_no=event.sequence_no,
            event_type=event.event_type.value,
            source=event.source.value,
            parent_task_id=event.parent_task_id,
            capability_id=capability_id,
            payload_digest=canonical_json_sha256(payload),
        )

    @staticmethod
    def _validate_contiguous(events: Sequence[RuntimeEventEnvelope]) -> None:
        expected = 1
        for event in events:
            if event.sequence_no != expected:
                raise ValueError(
                    "events must contain the complete contiguous sequence "
                    f"starting at 1; expected {expected}, got {event.sequence_no}"
                )
            expected += 1


class RuntimeTrajectoryProjector:
    """Read the existing event store and project only opted-in run metadata.

    This adapter is deliberately read-only. It proves F1 projection uses the
    established durable event timeline instead of duplicating the production
    event store. Scheduling, retry, and manifest persistence remain separate
    concerns so a projection failure cannot affect a user run.
    """

    def __init__(
        self,
        *,
        event_store: EventStorePort,
        projector: TrajectoryProjector,
    ) -> None:
        self._event_store = event_store
        self._projector = projector

    async def project_run(
        self,
        *,
        org_id: str,
        run_id: str,
        variant_id: str,
        policy: ProjectionPolicy,
        is_development_run: bool,
        evidence_refs: Sequence[str] = (),
        usage_summary: Mapping[str, int | float] | None = None,
        harness_revisions: Mapping[str, str] | None = None,
    ) -> TrajectoryManifest | None:
        if not policy.permits(is_development_run=is_development_run):
            return None
        events = await self._event_store.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        return self._projector.project(
            run_id=run_id,
            variant_id=variant_id,
            events=events,
            evidence_refs=evidence_refs,
            usage_summary=usage_summary,
            harness_revisions=harness_revisions,
        )


class DeterministicEvaluationRunner:
    """Runs fixture-only evaluations and persists immutable result records."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        scope: EvaluationScope,
        executor: TrajectoryExecutor,
        scorers: Sequence[EvaluationScorer],
        revisions: EvaluationRevisionSet,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._scope = scope
        self._executor = executor
        self._scorers = tuple(scorers)
        self._revisions = revisions
        self._clock = clock

    async def run(
        self,
        *,
        case: EvaluationCase,
        variant: HarnessVariant,
        fixtures: FixtureToolExecutor,
        mode: EvaluationMode = EvaluationMode.OFFLINE,
        evaluation_run_id: str | None = None,
    ) -> EvaluationResult:
        if mode is not EvaluationMode.OFFLINE:
            raise ValueError("live evaluations require an explicitly consented adapter")
        run_id = evaluation_run_id or f"eval_{uuid4().hex}"
        started = self._clock()
        try:
            trajectory = await self._executor(
                case=case,
                variant=variant,
                fixtures=fixtures,
            )
            scorer_results = tuple(
                scorer.score(case=case, trajectory=trajectory)
                for scorer in self._scorers
            )
            hard_failures = tuple(
                score.reason_code
                for score in scorer_results
                if score.hard_gate and not score.passed
            )
            status = (
                EvaluationStatus.FAILED if hard_failures else EvaluationStatus.SUCCEEDED
            )
        except FixtureMiss:
            scorer_results = ()
            hard_failures = ("fixture_miss",)
            status = EvaluationStatus.INCONCLUSIVE
        ended = self._clock()
        duration_ms = max(0, int((ended - started).total_seconds() * 1_000))
        values: dict[str, object] = {
            "evaluation_run_id": run_id,
            "suite_run_id": None,
            "case_id": case.case_id,
            "case_revision": case.revision,
            "variant_id": variant.variant_id,
            "variant_revision": variant.revision,
            "scorer_set_id": case.scorer_set_id,
            "revisions": self._revisions,
            "status": status,
            "scorer_results": scorer_results,
            "hard_gate_failures": hard_failures,
            "total_cost": 0.0,
            "model_turns": 0,
            "tool_calls": len(trajectory.ordered_steps)
            if "trajectory" in locals()
            else 0,
            "end_to_end_ms": duration_ms,
            "first_useful_answer_ms": None,
        }
        result = EvaluationResult(
            **values,
            result_digest=EvaluationResult.digest_for(**values),
        )
        await self._repository.put_evaluation_result(self._scope, result)
        return result


class PromotionGate:
    """Fail-closed paired promotion evaluator for immutable results."""

    _INCOMPLETE_STATUSES = frozenset(
        {
            EvaluationStatus.PENDING,
            EvaluationStatus.RUNNING,
            EvaluationStatus.INCONCLUSIVE,
        }
    )

    def evaluate(
        self,
        *,
        candidate_variant_id: str,
        control_variant_id: str,
        candidate_results: Sequence[EvaluationResult],
        control_results: Sequence[EvaluationResult],
        case_task_families: Mapping[str, str],
        thresholds: PromotionThresholds,
    ) -> PromotionAssessment:
        """Compare exact paired cases under a versioned threshold policy."""

        reasons: set[str] = set()
        candidate_by_case = self._index_results(
            candidate_results,
            expected_variant_id=candidate_variant_id,
            role="candidate",
            reasons=reasons,
        )
        control_by_case = self._index_results(
            control_results,
            expected_variant_id=control_variant_id,
            role="control",
            reasons=reasons,
        )
        if not candidate_results:
            reasons.add("candidate_results_empty")
        if not control_results:
            reasons.add("control_results_empty")
        if candidate_by_case.keys() != control_by_case.keys():
            reasons.add("unpaired_case_set")
        paired_case_ids = tuple(
            sorted(candidate_by_case.keys() & control_by_case.keys())
        )
        if len(paired_case_ids) < thresholds.minimum_paired_cases:
            reasons.add("minimum_paired_cases_not_met")

        candidate_successes: list[int] = []
        control_successes: list[int] = []
        for case_id in paired_case_ids:
            candidate = candidate_by_case[case_id]
            control = control_by_case[case_id]
            if candidate.case_revision != control.case_revision:
                reasons.add("case_revision_mismatch")
            if candidate.status in self._INCOMPLETE_STATUSES:
                reasons.add("candidate_incomplete_status")
            if control.status in self._INCOMPLETE_STATUSES:
                reasons.add("control_incomplete_status")
            if candidate.hard_gate_failures:
                reasons.add("candidate_hard_gate_failure")
            candidate_successes.append(int(self._succeeded(candidate)))
            control_successes.append(int(self._succeeded(control)))

        candidate_rate = self._rate(candidate_successes)
        control_rate = self._rate(control_successes)
        differences = tuple(
            candidate - control
            for candidate, control in zip(
                candidate_successes, control_successes, strict=True
            )
        )
        success_delta = candidate_rate - control_rate
        success_lower_bound = self._mean_lower_bound(
            differences,
            confidence_level=thresholds.confidence_level,
        )
        if success_lower_bound < -thresholds.maximum_success_rate_regression:
            reasons.add("success_regression")

        protected_bounds: dict[str, float] = {}
        for family in sorted(thresholds.protected_task_families):
            family_differences = tuple(
                candidate_successes[index] - control_successes[index]
                for index, case_id in enumerate(paired_case_ids)
                if case_task_families.get(case_id) == family
            )
            if not family_differences:
                reasons.add("protected_family_missing")
                continue
            lower_bound = self._mean_lower_bound(
                family_differences,
                confidence_level=thresholds.confidence_level,
            )
            protected_bounds[family] = lower_bound
            if lower_bound < -thresholds.maximum_protected_family_regression:
                reasons.add("protected_family_regression")

        if any(case_id not in case_task_families for case_id in paired_case_ids):
            reasons.add("task_family_mapping_missing")

        mean_cost_ratio = self._ratio(
            candidate_values=tuple(
                candidate_by_case[case_id].total_cost for case_id in paired_case_ids
            ),
            control_values=tuple(
                control_by_case[case_id].total_cost for case_id in paired_case_ids
            ),
            zero_regression_reason="cost_regression_from_zero",
            reasons=reasons,
        )
        if (
            mean_cost_ratio is not None
            and mean_cost_ratio > thresholds.maximum_mean_cost_ratio
        ):
            reasons.add("mean_cost_ratio_exceeded")

        p95_latency_ratio = self._ratio(
            candidate_values=tuple(
                float(candidate_by_case[case_id].end_to_end_ms)
                for case_id in paired_case_ids
            ),
            control_values=tuple(
                float(control_by_case[case_id].end_to_end_ms)
                for case_id in paired_case_ids
            ),
            zero_regression_reason="latency_regression_from_zero",
            reasons=reasons,
            aggregate=self._p95,
        )
        if (
            p95_latency_ratio is not None
            and p95_latency_ratio > thresholds.maximum_p95_latency_ratio
        ):
            reasons.add("p95_latency_ratio_exceeded")

        reason_codes = tuple(sorted(reasons))
        values: dict[str, object] = {
            "candidate_variant_id": candidate_variant_id,
            "control_variant_id": control_variant_id,
            "thresholds_revision": thresholds.revision,
            "paired_case_count": len(paired_case_ids),
            "candidate_success_rate": candidate_rate,
            "control_success_rate": control_rate,
            "success_rate_delta": success_delta,
            "success_rate_delta_lower_bound": success_lower_bound,
            "mean_cost_ratio": mean_cost_ratio,
            "p95_latency_ratio": p95_latency_ratio,
            "protected_family_lower_bounds": protected_bounds,
            "reason_codes": reason_codes,
            "passed": not reason_codes,
        }
        return PromotionAssessment(
            **values,
            assessment_digest=PromotionAssessment.digest_for(**values),
        )

    def decide(
        self,
        *,
        decision_id: str,
        candidate_variant_id: str,
        control_variant_id: str,
        suite_revisions: Sequence[str],
        thresholds_revision: str,
        report_ref: str,
        actor: str,
        rationale: str,
        assessment: PromotionAssessment,
        now: datetime | None = None,
    ) -> PromotionDecision:
        if assessment.candidate_variant_id != candidate_variant_id:
            raise ValueError("assessment candidate variant does not match decision")
        if assessment.control_variant_id != control_variant_id:
            raise ValueError("assessment control variant does not match decision")
        if assessment.thresholds_revision != thresholds_revision:
            raise ValueError("assessment thresholds revision does not match decision")
        return PromotionDecision(
            decision_id=decision_id,
            candidate_variant_id=candidate_variant_id,
            control_variant_id=control_variant_id,
            suite_revisions=tuple(suite_revisions),
            thresholds_revision=thresholds_revision,
            report_ref=report_ref,
            assessment_digest=assessment.assessment_digest,
            status=(
                PromotionStatus.APPROVED
                if assessment.passed
                else PromotionStatus.REJECTED
            ),
            actor=actor,
            decided_at=now or datetime.now(timezone.utc),
            rationale=rationale,
        )

    @classmethod
    def _index_results(
        cls,
        results: Sequence[EvaluationResult],
        *,
        expected_variant_id: str,
        role: str,
        reasons: set[str],
    ) -> dict[str, EvaluationResult]:
        indexed: dict[str, EvaluationResult] = {}
        for result in results:
            if result.variant_id != expected_variant_id:
                reasons.add(f"{role}_variant_mismatch")
            if result.case_id in indexed:
                reasons.add(f"{role}_duplicate_case")
                continue
            indexed[result.case_id] = result
        return indexed

    @staticmethod
    def _succeeded(result: EvaluationResult) -> bool:
        return (
            result.status is EvaluationStatus.SUCCEEDED
            and not result.hard_gate_failures
        )

    @staticmethod
    def _rate(values: Sequence[int]) -> float:
        return float(mean(values)) if values else 0.0

    @staticmethod
    def _mean_lower_bound(
        differences: Sequence[int],
        *,
        confidence_level: float,
    ) -> float:
        if not differences:
            return -1.0
        average = float(mean(differences))
        if len(differences) == 1:
            return average
        standard_error = stdev(differences) / sqrt(len(differences))
        z_score = NormalDist().inv_cdf(0.5 + (confidence_level / 2))
        return max(-1.0, min(1.0, average - (z_score * standard_error)))

    @staticmethod
    def _p95(values: Sequence[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        return float(ordered[max(0, ceil(len(ordered) * 0.95) - 1)])

    @staticmethod
    def _ratio(
        *,
        candidate_values: Sequence[float],
        control_values: Sequence[float],
        zero_regression_reason: str,
        reasons: set[str],
        aggregate: Callable[[Sequence[float]], float] | None = None,
    ) -> float | None:
        if not candidate_values or not control_values:
            return None
        aggregator = aggregate or (lambda values: float(mean(values)))
        candidate_value = aggregator(candidate_values)
        control_value = aggregator(control_values)
        if control_value == 0:
            if candidate_value > 0:
                reasons.add(zero_regression_reason)
                return None
            return 1.0
        return candidate_value / control_value
