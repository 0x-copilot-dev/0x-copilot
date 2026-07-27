"""Hermetic F1 evaluation mechanics.

Production runs keep using their existing event and usage stores.  This module
projects redacted manifests and evaluates only through exact fixture lookups;
there is intentionally no ambient HTTP client, connector, MCP client, or
effect executor in the call graph.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationMode,
    EvaluationResult,
    EvaluationStatus,
    FixtureResponse,
    HarnessVariant,
    PromotionDecision,
    PromotionStatus,
    ProjectionPolicy,
    ScorerResult,
    TrajectoryManifest,
    TrajectoryStep,
)
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
        previous = 0
        for event in events:
            if event.sequence_no <= previous:
                raise ValueError(
                    "events must have strictly increasing sequence numbers"
                )
            previous = event.sequence_no


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


class InMemoryEvaluationRepository:
    """Strict immutable repository used by hermetic runners and unit tests."""

    def __init__(self) -> None:
        self._cases: dict[tuple[str, str], EvaluationCase] = {}
        self._results: dict[str, EvaluationResult] = {}
        self._decisions: dict[str, PromotionDecision] = {}

    def put_case(self, case: EvaluationCase) -> bool:
        key = (case.case_id, case.revision)
        existing = self._cases.get(key)
        if existing is not None and existing != case:
            raise ValueError("case revision is immutable")
        self._cases[key] = case
        return existing is None

    def get_case(self, *, case_id: str, revision: str) -> EvaluationCase | None:
        return self._cases.get((case_id, revision))

    def put_result(self, result: EvaluationResult) -> bool:
        existing = self._results.get(result.evaluation_run_id)
        if existing is not None and existing != result:
            raise ValueError("evaluation result idempotency conflict")
        self._results[result.evaluation_run_id] = result
        return existing is None

    def put_decision(self, decision: PromotionDecision) -> bool:
        existing = self._decisions.get(decision.decision_id)
        if existing is not None and existing != decision:
            raise ValueError("promotion decision idempotency conflict")
        self._decisions[decision.decision_id] = decision
        return existing is None


class DeterministicEvaluationRunner:
    """Runs fixture-only evaluations and persists immutable result records."""

    def __init__(
        self,
        *,
        repository: InMemoryEvaluationRepository,
        executor: TrajectoryExecutor,
        scorers: Sequence[EvaluationScorer],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._scorers = tuple(scorers)
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
            "case_id": case.case_id,
            "variant_id": variant.variant_id,
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
        self._repository.put_result(result)
        return result


class PromotionGate:
    """Fail-closed promotion evaluator for immutable evaluation results."""

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
        candidate_results: Sequence[EvaluationResult],
        now: datetime | None = None,
    ) -> PromotionDecision:
        status = (
            PromotionStatus.REJECTED
            if not candidate_results
            or any(
                result.status is not EvaluationStatus.SUCCEEDED
                or result.hard_gate_failures
                for result in candidate_results
            )
            else PromotionStatus.APPROVED
        )
        return PromotionDecision(
            decision_id=decision_id,
            candidate_variant_id=candidate_variant_id,
            control_variant_id=control_variant_id,
            suite_revisions=tuple(suite_revisions),
            thresholds_revision=thresholds_revision,
            report_ref=report_ref,
            status=status,
            actor=actor,
            decided_at=now or datetime.now(timezone.utc),
            rationale=rationale,
        )
