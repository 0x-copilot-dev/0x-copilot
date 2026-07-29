"""Resumable, fixture-only F1 suite execution with hard resource ceilings."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from time import monotonic
from typing import Callable

from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation import (
    FixtureMiss,
    FixtureToolExecutor,
)
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationCaseProgress,
    EvaluationResult,
    EvaluationScope,
    EvaluationStatus,
    EvaluationSuiteLimits,
    EvaluationSuiteRun,
    EvaluationSuiteRunCheckpoint,
    FixtureCatalog,
    HarnessVariant,
    TrajectoryManifest,
    TrajectoryStep,
)
from agent_runtime.harness_quality.ports import EvaluationRepositoryPort
from agent_runtime.harness_quality.scoring import (
    DEFAULT_HARD_SCORERS,
    BoundedRedactedGrader,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class FixtureExecutionForbidden(RuntimeError):
    """The requested case is not a closed synthetic fixture evaluation."""


class SuiteLimitExceeded(RuntimeError):
    """A hard case or suite ceiling was exceeded before dispatch."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class FixtureUsage(RuntimeContract):
    """Deterministic resource facts carried by a reviewed fixture plan."""

    cost_microusd: int = Field(ge=0)
    model_turns: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)

    def plus(self, other: "FixtureUsage") -> "FixtureUsage":
        return FixtureUsage(
            cost_microusd=self.cost_microusd + other.cost_microusd,
            model_turns=self.model_turns + other.model_turns,
            tool_calls=self.tool_calls + other.tool_calls,
            tokens=self.tokens + other.tokens,
            elapsed_ms=self.elapsed_ms + other.elapsed_ms,
        )


class FixtureCallPlan(RuntimeContract):
    """One exact request admitted only through ``FixtureToolExecutor``."""

    capability_id: str = Field(min_length=1, max_length=160)
    arguments: dict[str, object]
    before_observations: tuple["FixtureTrajectoryObservation", ...] = ()
    after_observations: tuple["FixtureTrajectoryObservation", ...] = ()


class FixtureTrajectoryObservation(RuntimeContract):
    """A content-free control-plane fact placed around a fixture call.

    Fixture suites need to evaluate controller decisions as well as successful
    tool calls. These observations deliberately mirror only the public,
    closed F4 journal vocabulary; they cannot carry request or result bodies.
    """

    event_type: str = Field(min_length=1, max_length=120)
    source: str = Field(default="fixture", min_length=1, max_length=80)
    policy_record_kind: str | None = Field(default=None, max_length=80)
    policy_disposition: str | None = Field(default=None, max_length=80)
    policy_reason_codes: tuple[str, ...] = Field(default=(), max_length=16)
    policy_exhausted_dimensions: tuple[str, ...] = Field(default=(), max_length=8)
    prompt_record_kind: str | None = Field(default=None, max_length=80)
    prompt_cache_outcome: str | None = Field(default=None, max_length=80)
    prompt_cache_owner: str | None = Field(default=None, max_length=80)
    prompt_reason_code: str | None = Field(default=None, max_length=120)
    prompt_provider_reported: bool | None = None
    prompt_input_tokens: int = Field(default=0, ge=0)
    prompt_cached_input_tokens: int = Field(default=0, ge=0)
    prompt_cache_creation_input_tokens: int = Field(default=0, ge=0)
    invocation_record_kind: str | None = Field(default=None, max_length=80)
    invocation_status: str | None = Field(default=None, max_length=80)
    invocation_fallback_policy: str | None = Field(default=None, max_length=80)
    invocation_credential_mode: str | None = Field(default=None, max_length=80)
    invocation_decision: str | None = Field(default=None, max_length=80)
    invocation_reason: str | None = Field(default=None, max_length=120)
    invocation_attempt_state: str | None = Field(default=None, max_length=80)
    invocation_failure_class: str | None = Field(default=None, max_length=120)
    invocation_recovery_outcome: str | None = Field(default=None, max_length=80)
    invocation_exclusion_reasons: tuple[str, ...] = Field(default=(), max_length=16)
    invocation_provider_reported_usage: bool | None = None
    invocation_route_ordinal: int = Field(default=0, ge=0)
    invocation_attempt_ordinal: int = Field(default=0, ge=0)
    invocation_attempt_count: int = Field(default=0, ge=0)
    invocation_input_tokens: int = Field(default=0, ge=0)
    invocation_output_tokens: int = Field(default=0, ge=0)
    invocation_cost_microusd: int = Field(default=0, ge=0)
    discovery_phase: str | None = Field(default=None, max_length=80)
    discovery_outcome: str | None = Field(default=None, max_length=80)
    discovery_candidate_count: int = Field(default=0, ge=0)
    discovery_recall_rank: int = Field(default=0, ge=0)
    discovery_result_tokens: int = Field(default=0, ge=0)
    discovery_model_turns: int = Field(default=0, ge=0)
    #: Whether the four counts above are authored measurements rather than
    #: untouched defaults. A fixture discovery observation states this so the
    #: fixture path and the real-event path agree on what a zero means.
    discovery_counts_observed: bool = False
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class FixtureCasePlan(RuntimeContract):
    """Transient reviewed program for one immutable evaluation case revision."""

    case_id: str = Field(min_length=1, max_length=160)
    case_revision: str = Field(min_length=1, max_length=160)
    calls: tuple[FixtureCallPlan, ...]
    usage: FixtureUsage
    redaction_policy_revision: str = Field(min_length=1, max_length=160)
    harness_revisions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _tool_call_usage_matches_program(self) -> "FixtureCasePlan":
        if self.usage.tool_calls != len(self.calls):
            raise ValueError("fixture usage tool_calls must equal exact call count")
        return self

    @property
    def digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class FixtureOnlyCaseExecutor:
    """Concrete executor with no provider, gateway, connector, or effect port."""

    async def execute(
        self,
        *,
        suite_run_id: str,
        case: EvaluationCase,
        variant: HarnessVariant,
        plan: FixtureCasePlan,
        fixtures: FixtureToolExecutor,
        projected_at: datetime,
    ) -> TrajectoryManifest:
        if type(fixtures) is not FixtureToolExecutor:
            raise FixtureExecutionForbidden(
                "fixture executor subclasses are not admitted"
            )
        if case.sensitivity != "synthetic":
            raise FixtureExecutionForbidden("fixture suites admit synthetic cases only")
        if (plan.case_id, plan.case_revision) != (case.case_id, case.revision):
            raise FixtureExecutionForbidden("fixture plan does not bind case revision")
        planned_capabilities = frozenset(call.capability_id for call in plan.calls)
        if (
            not planned_capabilities.issubset(case.allowed_capabilities)
            or planned_capabilities & case.forbidden_capabilities
        ):
            raise FixtureExecutionForbidden(
                "fixture plan contains an unauthorized capability"
            )

        steps: list[TrajectoryStep] = []
        evidence_refs: list[str] = []
        sequence_no = 0
        for call in plan.calls:
            for observation in call.before_observations:
                sequence_no += 1
                steps.append(
                    self._observation_step(
                        sequence_no=sequence_no,
                        observation=observation,
                    )
                )
            response = await fixtures.execute(
                capability_id=call.capability_id,
                arguments=call.arguments,
            )
            evidence_refs.append(response.response_ref)
            sequence_no += 1
            steps.append(
                TrajectoryStep(
                    sequence_no=sequence_no,
                    event_type=(
                        "fixture_tool_error"
                        if response.is_error
                        else "fixture_tool_result"
                    ),
                    source="fixture",
                    capability_id=call.capability_id,
                    payload_digest=response.response_digest,
                )
            )
            for observation in call.after_observations:
                sequence_no += 1
                steps.append(
                    self._observation_step(
                        sequence_no=sequence_no,
                        observation=observation,
                    )
                )

        usage_summary: dict[str, int | float] = {
            "cost_microusd": plan.usage.cost_microusd,
            "model_turns": plan.usage.model_turns,
            "tool_calls": plan.usage.tool_calls,
            "tokens": plan.usage.tokens,
            "elapsed_ms": plan.usage.elapsed_ms,
            "live_effect_dispatches": 0,
        }
        trajectory_id = (
            "traj_"
            + canonical_json_sha256(
                {
                    "suite_run_id": suite_run_id,
                    "case_id": case.case_id,
                    "case_revision": case.revision,
                    "variant_digest": variant.digest,
                }
            )[:32]
        )
        values: dict[str, object] = {
            "trajectory_id": trajectory_id,
            "run_id": None,
            "case_id": case.case_id,
            "variant_id": variant.variant_id,
            "ordered_steps": tuple(steps),
            "evidence_refs": tuple(sorted(set(evidence_refs))),
            "usage_summary": usage_summary,
            "redaction_policy_revision": plan.redaction_policy_revision,
            "harness_revisions": dict(plan.harness_revisions),
        }
        return TrajectoryManifest(
            **values,
            manifest_digest=TrajectoryManifest.digest_for(**values),
            projected_at=projected_at,
        )

    @staticmethod
    def _observation_step(
        *,
        sequence_no: int,
        observation: FixtureTrajectoryObservation,
    ) -> TrajectoryStep:
        return TrajectoryStep(
            sequence_no=sequence_no,
            event_type=observation.event_type,
            source=observation.source,
            policy_record_kind=observation.policy_record_kind,
            policy_disposition=observation.policy_disposition,
            policy_reason_codes=observation.policy_reason_codes,
            policy_exhausted_dimensions=observation.policy_exhausted_dimensions,
            prompt_record_kind=observation.prompt_record_kind,
            prompt_cache_outcome=observation.prompt_cache_outcome,
            prompt_cache_owner=observation.prompt_cache_owner,
            prompt_reason_code=observation.prompt_reason_code,
            prompt_provider_reported=observation.prompt_provider_reported,
            prompt_input_tokens=observation.prompt_input_tokens,
            prompt_cached_input_tokens=observation.prompt_cached_input_tokens,
            prompt_cache_creation_input_tokens=(
                observation.prompt_cache_creation_input_tokens
            ),
            invocation_record_kind=observation.invocation_record_kind,
            invocation_status=observation.invocation_status,
            invocation_fallback_policy=observation.invocation_fallback_policy,
            invocation_credential_mode=observation.invocation_credential_mode,
            invocation_decision=observation.invocation_decision,
            invocation_reason=observation.invocation_reason,
            invocation_attempt_state=observation.invocation_attempt_state,
            invocation_failure_class=observation.invocation_failure_class,
            invocation_recovery_outcome=observation.invocation_recovery_outcome,
            invocation_exclusion_reasons=observation.invocation_exclusion_reasons,
            invocation_provider_reported_usage=(
                observation.invocation_provider_reported_usage
            ),
            invocation_route_ordinal=observation.invocation_route_ordinal,
            invocation_attempt_ordinal=observation.invocation_attempt_ordinal,
            invocation_attempt_count=observation.invocation_attempt_count,
            invocation_input_tokens=observation.invocation_input_tokens,
            invocation_output_tokens=observation.invocation_output_tokens,
            invocation_cost_microusd=observation.invocation_cost_microusd,
            discovery_phase=observation.discovery_phase,
            discovery_outcome=observation.discovery_outcome,
            discovery_candidate_count=observation.discovery_candidate_count,
            discovery_recall_rank=observation.discovery_recall_rank,
            discovery_result_tokens=observation.discovery_result_tokens,
            discovery_model_turns=observation.discovery_model_turns,
            discovery_counts_observed=observation.discovery_counts_observed,
            payload_digest=observation.payload_digest,
        )


class FixtureOnlySuiteRunner:
    """Execute and resume one immutable suite through its canonical repository."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        scope: EvaluationScope,
        optional_grader: BoundedRedactedGrader | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._repository = repository
        self._scope = scope
        self._scorers = DEFAULT_HARD_SCORERS
        self._optional_grader = optional_grader
        self._executor = FixtureOnlyCaseExecutor()
        self._clock = clock
        self._monotonic = monotonic_clock

    async def run(
        self,
        *,
        suite_run: EvaluationSuiteRun,
        variant: HarnessVariant,
        plans: Mapping[str, FixtureCasePlan],
    ) -> EvaluationSuiteRunCheckpoint:
        self._verify_variant(suite_run=suite_run, variant=variant)
        await self._repository.put_suite_run(self._scope, suite_run)
        catalog = await self._load_catalog(suite_run)
        cases = await self._load_cases(suite_run)
        fixture_executor = FixtureToolExecutor(catalog.fixtures)
        checkpoint = await self._repository.latest_suite_run_checkpoint(
            self._scope,
            suite_run_id=suite_run.suite_run_id,
        )
        if checkpoint is None:
            checkpoint = await self._append_checkpoint(
                suite_run_id=suite_run.suite_run_id,
                checkpoint_no=0,
                status=EvaluationStatus.RUNNING,
                next_case_index=0,
                completed_result_ids=(),
            )
        if checkpoint.status in {
            EvaluationStatus.SUCCEEDED,
            EvaluationStatus.FAILED,
            EvaluationStatus.INCONCLUSIVE,
        }:
            return checkpoint
        if checkpoint.next_case_index > len(cases):
            raise FixtureExecutionForbidden("checkpoint cursor exceeds suite cases")
        if len(checkpoint.completed_result_ids) != checkpoint.next_case_index:
            raise FixtureExecutionForbidden(
                "checkpoint result count does not match case cursor"
            )
        if checkpoint.active_case is not None:
            if checkpoint.next_case_index >= len(cases):
                raise FixtureExecutionForbidden(
                    "terminal case cursor cannot have active progress"
                )
            expected_case = cases[checkpoint.next_case_index]
            if (
                checkpoint.active_case.case_id,
                checkpoint.active_case.case_revision,
            ) != (expected_case.case_id, expected_case.revision):
                raise FixtureExecutionForbidden(
                    "active checkpoint does not bind the next case"
                )
            expected_plan = self._plan_for(case=expected_case, plans=plans)
            if checkpoint.active_case.resume_cursor_ref != _fixture_plan_ref(
                expected_plan
            ):
                raise FixtureExecutionForbidden(
                    "active checkpoint does not bind the fixture plan"
                )

        usage = self._completed_usage(
            cases=cases,
            plans=plans,
            completed_count=checkpoint.next_case_index,
        )
        suite_started = self._monotonic()
        completed_ids = set(checkpoint.completed_result_ids)
        any_hard_failure = await self._completed_hard_failure(completed_ids)
        checkpoint_no = checkpoint.checkpoint_no

        for case_index in range(checkpoint.next_case_index, len(cases)):
            case = cases[case_index]
            plan = self._plan_for(case=case, plans=plans)
            try:
                self._check_case_limits(plan.usage, suite_run.limits)
                self._check_suite_limits(
                    usage.plus(plan.usage),
                    suite_run.limits,
                    observed_wall_ms=self._observed_ms(suite_started),
                )
            except SuiteLimitExceeded as exc:
                return await self._append_checkpoint(
                    suite_run_id=suite_run.suite_run_id,
                    checkpoint_no=checkpoint_no + 1,
                    status=EvaluationStatus.FAILED,
                    next_case_index=case_index,
                    completed_result_ids=completed_ids,
                    active_case=self._progress(case, plan, plan.usage),
                    reason_codes=(exc.reason_code,),
                )

            checkpoint_no += 1
            await self._append_checkpoint(
                suite_run_id=suite_run.suite_run_id,
                checkpoint_no=checkpoint_no,
                status=EvaluationStatus.RUNNING,
                next_case_index=case_index,
                completed_result_ids=completed_ids,
                active_case=self._progress(
                    case,
                    plan,
                    FixtureUsage(
                        cost_microusd=0,
                        model_turns=0,
                        tool_calls=0,
                        tokens=0,
                        elapsed_ms=0,
                    ),
                ),
            )
            result = await self._run_case(
                suite_run=suite_run,
                case=case,
                variant=variant,
                plan=plan,
                fixtures=fixture_executor,
                timeout_ms=min(
                    suite_run.limits.max_case_wall_time_ms,
                    max(
                        1,
                        suite_run.limits.max_suite_wall_time_ms
                        - max(usage.elapsed_ms, self._observed_ms(suite_started)),
                    ),
                ),
            )
            completed_ids.add(result.evaluation_run_id)
            any_hard_failure = any_hard_failure or bool(result.hard_gate_failures)
            usage = usage.plus(plan.usage)
            checkpoint_no += 1
            checkpoint = await self._append_checkpoint(
                suite_run_id=suite_run.suite_run_id,
                checkpoint_no=checkpoint_no,
                status=EvaluationStatus.RUNNING,
                next_case_index=case_index + 1,
                completed_result_ids=completed_ids,
            )

        terminal_status = (
            EvaluationStatus.FAILED if any_hard_failure else EvaluationStatus.SUCCEEDED
        )
        reason_codes = ("case_hard_gate_failure",) if any_hard_failure else ()
        return await self._append_checkpoint(
            suite_run_id=suite_run.suite_run_id,
            checkpoint_no=checkpoint.checkpoint_no + 1,
            status=terminal_status,
            next_case_index=len(cases),
            completed_result_ids=completed_ids,
            reason_codes=reason_codes,
        )

    async def _run_case(
        self,
        *,
        suite_run: EvaluationSuiteRun,
        case: EvaluationCase,
        variant: HarnessVariant,
        plan: FixtureCasePlan,
        fixtures: FixtureToolExecutor,
        timeout_ms: int,
    ) -> EvaluationResult:
        evaluation_run_id = _evaluation_run_id(suite_run.suite_run_id, case.case_id)
        try:
            async with asyncio.timeout(timeout_ms / 1_000):
                trajectory = await self._executor.execute(
                    suite_run_id=suite_run.suite_run_id,
                    case=case,
                    variant=variant,
                    plan=plan,
                    fixtures=fixtures,
                    projected_at=suite_run.created_at,
                )
            deterministic = tuple(
                scorer.score(case=case, trajectory=trajectory)
                for scorer in self._scorers
            )
            advisory = (
                await self._optional_grader.score(
                    case=case,
                    trajectory=trajectory,
                    deterministic_results=deterministic,
                )
                if self._optional_grader is not None
                else None
            )
            scorer_results = (
                (*deterministic, advisory) if advisory is not None else deterministic
            )
            hard_failures = tuple(
                sorted(
                    result.reason_code
                    for result in deterministic
                    if result.hard_gate and not result.passed
                )
            )
            status = (
                EvaluationStatus.FAILED if hard_failures else EvaluationStatus.SUCCEEDED
            )
            await self._repository.put_trajectory_manifest(self._scope, trajectory)
        except FixtureMiss:
            scorer_results = ()
            hard_failures = ("fixture_miss",)
            status = EvaluationStatus.INCONCLUSIVE
        except TimeoutError:
            scorer_results = ()
            hard_failures = ("case_wall_time_limit_exceeded",)
            status = EvaluationStatus.FAILED

        values: dict[str, object] = {
            "evaluation_run_id": evaluation_run_id,
            "suite_run_id": suite_run.suite_run_id,
            "case_id": case.case_id,
            "case_revision": case.revision,
            "variant_id": variant.variant_id,
            "variant_revision": variant.revision,
            "scorer_set_id": case.scorer_set_id,
            "revisions": suite_run.revisions,
            "status": status,
            "scorer_results": scorer_results,
            "hard_gate_failures": hard_failures,
            "total_cost": (
                plan.usage.cost_microusd
                + sum(
                    scorer.attribution.cost_microusd
                    for scorer in scorer_results
                    if scorer.attribution is not None
                )
            )
            / 1_000_000,
            "model_turns": plan.usage.model_turns
            + sum(1 for scorer in scorer_results if scorer.attribution is not None),
            "tool_calls": plan.usage.tool_calls,
            "end_to_end_ms": plan.usage.elapsed_ms,
            "first_useful_answer_ms": None,
        }
        result = EvaluationResult(
            **values,
            result_digest=EvaluationResult.digest_for(**values),
        )
        await self._repository.put_evaluation_result(self._scope, result)
        return result

    async def _load_catalog(self, suite_run: EvaluationSuiteRun) -> FixtureCatalog:
        catalog = await self._repository.get_fixture_catalog(
            self._scope,
            catalog_id=suite_run.fixture_catalog_id,
            revision=suite_run.fixture_catalog_revision,
        )
        if catalog is None:
            raise FixtureExecutionForbidden("fixture catalog is not persisted")
        return catalog

    async def _load_cases(
        self,
        suite_run: EvaluationSuiteRun,
    ) -> tuple[EvaluationCase, ...]:
        cases: list[EvaluationCase] = []
        for case_ref in suite_run.case_refs:
            case = await self._repository.get_case(
                self._scope,
                case_id=case_ref.case_id,
                revision=case_ref.revision,
            )
            if case is None:
                raise FixtureExecutionForbidden("suite case revision is not persisted")
            if case.suite_id != suite_run.suite_id:
                raise FixtureExecutionForbidden("suite case belongs to another suite")
            if case.fixture_catalog_ref != suite_run.fixture_catalog_id:
                raise FixtureExecutionForbidden(
                    "suite case binds another fixture catalog"
                )
            cases.append(case)
        return tuple(cases)

    async def _completed_hard_failure(
        self,
        completed_result_ids: set[str],
    ) -> bool:
        for result_id in completed_result_ids:
            result = await self._repository.get_evaluation_result(
                self._scope,
                evaluation_run_id=result_id,
            )
            if result is None:
                raise FixtureExecutionForbidden(
                    "checkpoint references a missing evaluation result"
                )
            if result.hard_gate_failures:
                return True
        return False

    async def _append_checkpoint(
        self,
        *,
        suite_run_id: str,
        checkpoint_no: int,
        status: EvaluationStatus,
        next_case_index: int,
        completed_result_ids: Sequence[str] | set[str],
        active_case: EvaluationCaseProgress | None = None,
        reason_codes: Sequence[str] = (),
    ) -> EvaluationSuiteRunCheckpoint:
        values: dict[str, object] = {
            "suite_run_id": suite_run_id,
            "checkpoint_no": checkpoint_no,
            "status": status,
            "next_case_index": next_case_index,
            "completed_result_ids": tuple(sorted(set(completed_result_ids))),
            "active_case": active_case,
            "reason_codes": tuple(sorted(set(reason_codes))),
            "updated_at": self._clock(),
        }
        checkpoint = EvaluationSuiteRunCheckpoint(
            **values,
            checkpoint_digest=EvaluationSuiteRunCheckpoint.digest_for(**values),
        )
        await self._repository.append_suite_run_checkpoint(self._scope, checkpoint)
        return checkpoint

    @staticmethod
    def _verify_variant(
        *,
        suite_run: EvaluationSuiteRun,
        variant: HarnessVariant,
    ) -> None:
        if (
            suite_run.variant_id != variant.variant_id
            or suite_run.variant_revision != variant.revision
            or suite_run.variant_digest != variant.digest
        ):
            raise FixtureExecutionForbidden("suite run does not bind variant")

    @staticmethod
    def _plan_for(
        *,
        case: EvaluationCase,
        plans: Mapping[str, FixtureCasePlan],
    ) -> FixtureCasePlan:
        plan = plans.get(case.case_id)
        if plan is None or plan.case_revision != case.revision:
            raise FixtureExecutionForbidden("case plan is missing or stale")
        return plan

    @classmethod
    def _completed_usage(
        cls,
        *,
        cases: Sequence[EvaluationCase],
        plans: Mapping[str, FixtureCasePlan],
        completed_count: int,
    ) -> FixtureUsage:
        usage = FixtureUsage(
            cost_microusd=0,
            model_turns=0,
            tool_calls=0,
            tokens=0,
            elapsed_ms=0,
        )
        for case in cases[:completed_count]:
            usage = usage.plus(cls._plan_for(case=case, plans=plans).usage)
        return usage

    @staticmethod
    def _progress(
        case: EvaluationCase,
        plan: FixtureCasePlan,
        usage: FixtureUsage,
    ) -> EvaluationCaseProgress:
        return EvaluationCaseProgress(
            case_id=case.case_id,
            case_revision=case.revision,
            resume_cursor_ref=_fixture_plan_ref(plan),
            cost_microusd=usage.cost_microusd,
            model_turns=usage.model_turns,
            tool_calls=usage.tool_calls,
            tokens=usage.tokens,
            elapsed_ms=usage.elapsed_ms,
        )

    @staticmethod
    def _check_case_limits(
        usage: FixtureUsage,
        limits: EvaluationSuiteLimits,
    ) -> None:
        _check_usage(
            usage=usage,
            prefix="case",
            cost=limits.max_case_cost_microusd,
            turns=limits.max_case_model_turns,
            calls=limits.max_case_tool_calls,
            tokens=limits.max_case_tokens,
            wall_ms=limits.max_case_wall_time_ms,
        )

    @staticmethod
    def _check_suite_limits(
        usage: FixtureUsage,
        limits: EvaluationSuiteLimits,
        *,
        observed_wall_ms: int,
    ) -> None:
        _check_usage(
            usage=usage.model_copy(
                update={"elapsed_ms": max(usage.elapsed_ms, observed_wall_ms)}
            ),
            prefix="suite",
            cost=limits.max_suite_cost_microusd,
            turns=limits.max_suite_model_turns,
            calls=limits.max_suite_tool_calls,
            tokens=limits.max_suite_tokens,
            wall_ms=limits.max_suite_wall_time_ms,
        )

    def _observed_ms(self, started: float) -> int:
        return max(0, int((self._monotonic() - started) * 1_000))


def _check_usage(
    *,
    usage: FixtureUsage,
    prefix: str,
    cost: int,
    turns: int,
    calls: int,
    tokens: int,
    wall_ms: int,
) -> None:
    values = (
        ("cost", usage.cost_microusd, cost),
        ("model_turns", usage.model_turns, turns),
        ("tool_calls", usage.tool_calls, calls),
        ("tokens", usage.tokens, tokens),
        ("wall_time", usage.elapsed_ms, wall_ms),
    )
    for resource, observed, limit in values:
        if observed > limit:
            raise SuiteLimitExceeded(f"{prefix}_{resource}_limit_exceeded")


def _evaluation_run_id(suite_run_id: str, case_id: str) -> str:
    digest = canonical_json_sha256({"suite_run_id": suite_run_id, "case_id": case_id})
    return f"eval_{digest[:32]}"


def _fixture_plan_ref(plan: FixtureCasePlan) -> str:
    return f"fixture-plan://sha256/{plan.digest}"


__all__ = [
    "FixtureCallPlan",
    "FixtureCasePlan",
    "FixtureExecutionForbidden",
    "FixtureOnlyCaseExecutor",
    "FixtureOnlySuiteRunner",
    "FixtureTrajectoryObservation",
    "FixtureUsage",
    "SuiteLimitExceeded",
]
