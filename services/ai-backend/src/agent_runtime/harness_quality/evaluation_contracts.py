"""F1 contracts for hermetic harness evaluation and promotion.

These models intentionally retain references, revisions, digests, and bounded
observable action metadata only.  Raw prompts, model output, credentials, tool
arguments, and tool result bodies never become evaluation telemetry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


OpaqueId = Annotated[str, Field(min_length=1, max_length=160)]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Revision = Annotated[str, Field(min_length=1, max_length=160)]


class EvaluationMode(StrEnum):
    """Whether a case can contact a provider.

    ``OFFLINE`` is the only mode accepted by the fixture executor.  ``LIVE``
    is represented for report attribution but must be handled by a separately
    consented provider adapter; it cannot accidentally fall through to a
    fixture miss and use a production transport.
    """

    OFFLINE = "offline"
    LIVE = "live"


class EvaluationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class PromotionStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class ProjectionPolicy(RuntimeContract):
    """Local opt-in policy for producing a redacted evaluation manifest.

    The policy is supplied by the product configuration boundary, not inferred
    from an organization role.  It gives desktop profiles an explicit local
    off switch and keeps production projection disabled by default.
    """

    revision: Revision
    enabled: bool = False
    user_consented: bool = False
    allow_development_runs: bool = False

    def permits(self, *, is_development_run: bool) -> bool:
        return (
            self.enabled
            and self.user_consented
            and (self.allow_development_runs or not is_development_run)
        )


class EvaluationAssertion(RuntimeContract):
    """A deterministic assertion over a fixture trajectory.

    The first delivery keeps the assertion language intentionally small and
    executable: a scorer name plus a canonical JSON-safe expectation.  Richer
    domain predicates are implemented as named scorers rather than ad-hoc
    expression evaluation.
    """

    scorer_id: OpaqueId
    expected: object
    hard_gate: bool = False


class EvaluationCase(RuntimeContract):
    case_id: OpaqueId
    suite_id: OpaqueId
    revision: Revision
    task_family: Annotated[str, Field(min_length=1, max_length=80)]
    input_ref: OpaqueId
    fixture_catalog_ref: OpaqueId
    expected_assertions: tuple[EvaluationAssertion, ...] = ()
    allowed_capabilities: frozenset[str] = frozenset()
    forbidden_capabilities: frozenset[str] = frozenset()
    scorer_set_id: OpaqueId
    sensitivity: Annotated[str, Field(min_length=1, max_length=40)] = "synthetic"

    @model_validator(mode="after")
    def _capability_sets_do_not_overlap(self) -> "EvaluationCase":
        overlap = self.allowed_capabilities & self.forbidden_capabilities
        if overlap:
            raise ValueError("allowed_capabilities overlaps forbidden_capabilities")
        return self


class HarnessVariant(RuntimeContract):
    variant_id: OpaqueId
    revision: Revision
    prompt_plan_revision: Revision
    capability_policy_revision: Revision
    context_policy_revision: Revision
    model_route_revision: Revision
    feature_flags: frozenset[str] = frozenset()

    @property
    def digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class TrajectoryStep(RuntimeContract):
    """A redacted observable action from a run.

    ``payload_digest`` is enough to prove the observed body has not changed
    without persisting that body in the evaluation plane.
    """

    sequence_no: Annotated[int, Field(ge=1)]
    event_type: Annotated[str, Field(min_length=1, max_length=120)]
    source: Annotated[str, Field(min_length=1, max_length=80)]
    parent_task_id: str | None = Field(default=None, max_length=160)
    capability_id: str | None = Field(default=None, max_length=240)
    policy_record_kind: str | None = Field(default=None, max_length=80)
    policy_disposition: str | None = Field(default=None, max_length=80)
    policy_reason_codes: tuple[str, ...] = Field(default=(), max_length=16)
    policy_exhausted_dimensions: tuple[str, ...] = Field(default=(), max_length=8)
    prompt_record_kind: str | None = Field(default=None, max_length=80)
    prompt_cache_outcome: str | None = Field(default=None, max_length=80)
    prompt_cache_owner: str | None = Field(default=None, max_length=80)
    prompt_reason_code: str | None = Field(default=None, max_length=120)
    prompt_provider_reported: bool | None = None
    prompt_input_tokens: Annotated[int, Field(ge=0)] = 0
    prompt_cached_input_tokens: Annotated[int, Field(ge=0)] = 0
    prompt_cache_creation_input_tokens: Annotated[int, Field(ge=0)] = 0
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
    invocation_route_ordinal: Annotated[int, Field(ge=0)] = 0
    invocation_attempt_ordinal: Annotated[int, Field(ge=0)] = 0
    invocation_attempt_count: Annotated[int, Field(ge=0)] = 0
    invocation_input_tokens: Annotated[int, Field(ge=0)] = 0
    invocation_output_tokens: Annotated[int, Field(ge=0)] = 0
    invocation_cost_microusd: Annotated[int, Field(ge=0)] = 0
    discovery_phase: str | None = Field(default=None, max_length=80)
    discovery_outcome: str | None = Field(default=None, max_length=80)
    discovery_candidate_count: Annotated[int, Field(ge=0)] = 0
    discovery_recall_rank: Annotated[int, Field(ge=0)] = 0
    discovery_result_tokens: Annotated[int, Field(ge=0)] = 0
    discovery_model_turns: Annotated[int, Field(ge=0)] = 0
    payload_digest: Sha256


class TrajectoryManifest(RuntimeContract):
    trajectory_id: OpaqueId
    run_id: str | None = Field(default=None, min_length=1, max_length=160)
    case_id: str | None = Field(default=None, min_length=1, max_length=160)
    variant_id: OpaqueId
    ordered_steps: tuple[TrajectoryStep, ...]
    evidence_refs: tuple[str, ...] = ()
    usage_summary: dict[str, int | float] = Field(default_factory=dict)
    redaction_policy_revision: Revision
    harness_revisions: dict[str, str] = Field(default_factory=dict)
    manifest_digest: Sha256
    projected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("projected_at")
    @classmethod
    def _projected_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "projected_at")

    @model_validator(mode="after")
    def _manifest_digest_matches(self) -> "TrajectoryManifest":
        expected = self.digest_for(
            trajectory_id=self.trajectory_id,
            run_id=self.run_id,
            case_id=self.case_id,
            variant_id=self.variant_id,
            ordered_steps=self.ordered_steps,
            evidence_refs=self.evidence_refs,
            usage_summary=self.usage_summary,
            redaction_policy_revision=self.redaction_policy_revision,
            harness_revisions=self.harness_revisions,
        )
        if self.manifest_digest != expected:
            raise ValueError(
                "manifest_digest does not match canonical manifest content"
            )
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class FixtureResponse(RuntimeContract):
    capability_id: OpaqueId
    request_digest: Sha256
    response_ref: OpaqueId
    response_digest: Sha256
    is_error: bool = False


class ScorerAttribution(RuntimeContract):
    """Optional model-scorer identity and usage; deterministic scorers omit it."""

    scorer_revision: Revision
    model_revision: Revision
    prompt_revision: Revision
    tokens: Annotated[int, Field(ge=0)]
    cost_microusd: Annotated[int, Field(ge=0)]


class ScorerResult(RuntimeContract):
    scorer_id: OpaqueId
    score: Annotated[float, Field(ge=0, le=1)]
    passed: bool
    hard_gate: bool = False
    reason_code: Annotated[str, Field(min_length=1, max_length=120)]
    attribution: ScorerAttribution | None = None


class EvaluationRevisionSet(RuntimeContract):
    """Exact immutable inputs needed to reproduce an evaluation result."""

    code_revision: Revision
    model_revision: Revision
    prompt_revision: Revision
    tool_revision: Revision
    policy_revision: Revision
    fixture_revision: Revision
    scorer_revision: Revision


class EvaluationResult(RuntimeContract):
    evaluation_run_id: OpaqueId
    suite_run_id: OpaqueId | None = None
    case_id: OpaqueId
    case_revision: Revision
    variant_id: OpaqueId
    variant_revision: Revision
    scorer_set_id: OpaqueId
    revisions: EvaluationRevisionSet
    status: EvaluationStatus
    scorer_results: tuple[ScorerResult, ...]
    hard_gate_failures: tuple[str, ...] = ()
    total_cost: Annotated[float, Field(ge=0)] = 0
    model_turns: Annotated[int, Field(ge=0)] = 0
    tool_calls: Annotated[int, Field(ge=0)] = 0
    end_to_end_ms: Annotated[int, Field(ge=0)] = 0
    first_useful_answer_ms: Annotated[int, Field(ge=0)] | None = None
    result_digest: Sha256

    @model_validator(mode="after")
    def _result_digest_matches(self) -> "EvaluationResult":
        expected = self.digest_for(
            evaluation_run_id=self.evaluation_run_id,
            suite_run_id=self.suite_run_id,
            case_id=self.case_id,
            case_revision=self.case_revision,
            variant_id=self.variant_id,
            variant_revision=self.variant_revision,
            scorer_set_id=self.scorer_set_id,
            revisions=self.revisions,
            status=self.status,
            scorer_results=self.scorer_results,
            hard_gate_failures=self.hard_gate_failures,
            total_cost=self.total_cost,
            model_turns=self.model_turns,
            tool_calls=self.tool_calls,
            end_to_end_ms=self.end_to_end_ms,
            first_useful_answer_ms=self.first_useful_answer_ms,
        )
        if self.result_digest != expected:
            raise ValueError("result_digest does not match canonical result content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class PromotionThresholds(RuntimeContract):
    """Versioned fail-closed thresholds for a paired promotion report."""

    revision: Revision
    minimum_paired_cases: Annotated[int, Field(ge=1, le=100_000)] = 20
    confidence_level: Annotated[float, Field(gt=0.5, lt=1)] = 0.95
    maximum_success_rate_regression: Annotated[float, Field(ge=0, le=1)] = 0
    maximum_protected_family_regression: Annotated[float, Field(ge=0, le=1)] = 0
    maximum_mean_cost_ratio: Annotated[float, Field(gt=0)] = 1.1
    maximum_p95_latency_ratio: Annotated[float, Field(gt=0)] = 1.1
    protected_task_families: frozenset[str] = frozenset()


class PromotionAssessment(RuntimeContract):
    """Content-free paired candidate/control assessment.

    The report contains aggregate values and reason codes only. Case inputs,
    prompts, outputs, and scorer rationales remain in their owning protected
    fixture/result records.
    """

    candidate_variant_id: OpaqueId
    control_variant_id: OpaqueId
    thresholds_revision: Revision
    paired_case_count: Annotated[int, Field(ge=0)]
    candidate_success_rate: Annotated[float, Field(ge=0, le=1)]
    control_success_rate: Annotated[float, Field(ge=0, le=1)]
    success_rate_delta: Annotated[float, Field(ge=-1, le=1)]
    success_rate_delta_lower_bound: Annotated[float, Field(ge=-1, le=1)]
    mean_cost_ratio: Annotated[float, Field(ge=0)] | None = None
    p95_latency_ratio: Annotated[float, Field(ge=0)] | None = None
    protected_family_lower_bounds: dict[str, float] = Field(default_factory=dict)
    reason_codes: tuple[str, ...]
    passed: bool
    assessment_digest: Sha256

    @field_validator("reason_codes")
    @classmethod
    def _reason_codes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("reason_codes must be unique and sorted")
        return value

    @field_validator("protected_family_lower_bounds")
    @classmethod
    def _family_bounds_are_valid(cls, value: dict[str, float]) -> dict[str, float]:
        for family, bound in value.items():
            if not family.strip():
                raise ValueError("protected task family must be non-empty")
            if bound < -1 or bound > 1:
                raise ValueError("protected family lower bound must be within [-1, 1]")
        return value

    @model_validator(mode="after")
    def _assessment_digest_matches(self) -> "PromotionAssessment":
        expected = self.digest_for(
            candidate_variant_id=self.candidate_variant_id,
            control_variant_id=self.control_variant_id,
            thresholds_revision=self.thresholds_revision,
            paired_case_count=self.paired_case_count,
            candidate_success_rate=self.candidate_success_rate,
            control_success_rate=self.control_success_rate,
            success_rate_delta=self.success_rate_delta,
            success_rate_delta_lower_bound=self.success_rate_delta_lower_bound,
            mean_cost_ratio=self.mean_cost_ratio,
            p95_latency_ratio=self.p95_latency_ratio,
            protected_family_lower_bounds=self.protected_family_lower_bounds,
            reason_codes=self.reason_codes,
            passed=self.passed,
        )
        if self.assessment_digest != expected:
            raise ValueError(
                "assessment_digest does not match canonical assessment content"
            )
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class PromotionDecision(RuntimeContract):
    decision_id: OpaqueId
    candidate_variant_id: OpaqueId
    control_variant_id: OpaqueId
    suite_revisions: tuple[Revision, ...]
    thresholds_revision: Revision
    report_ref: OpaqueId
    assessment_digest: Sha256
    status: PromotionStatus
    actor: OpaqueId
    decided_at: datetime
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("decided_at")
    @classmethod
    def _decided_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "decided_at")

    @field_validator("suite_revisions")
    @classmethod
    def _suite_revisions_are_present(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("suite_revisions must not be empty")
        return value


class EvaluationScope(RuntimeContract):
    """One local evaluation namespace.

    The scope is deliberately product-neutral.  Desktop uses one local profile
    identifier (and, optionally, one project identifier); a future consumer
    may map a signed-in B2C profile to the same contract without introducing
    tenant or administrator semantics into the evaluation domain.
    """

    profile_id: OpaqueId
    project_id: OpaqueId | None = None

    @property
    def storage_key(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class EvaluationRecordKind(StrEnum):
    CASE = "case"
    FIXTURE_CATALOG = "fixture_catalog"
    TRAJECTORY_MANIFEST = "trajectory_manifest"
    SUITE_RUN = "suite_run"
    SUITE_RUN_CHECKPOINT = "suite_run_checkpoint"
    EVALUATION_RESULT = "evaluation_result"
    PAIRED_REPORT = "paired_report"
    PROMOTION_DECISION = "promotion_decision"
    HARNESS_MANIFEST = "harness_manifest"
    HARNESS_MANIFEST_POINTER = "harness_manifest_pointer"
    PROJECTION_JOB = "projection_job"


class EvaluationRecordOwner(RuntimeContract):
    """Exact immutable record that owns a protected CAS reference."""

    kind: EvaluationRecordKind
    record_id: OpaqueId


class ProtectedEvaluationArtifact(RuntimeContract):
    """Scoped reference to bytes stored in the existing file CAS."""

    sha256: Sha256
    size: Annotated[int, Field(ge=0)]
    media_type: Annotated[str, Field(min_length=1, max_length=160)] = (
        "application/octet-stream"
    )

    @property
    def ref(self) -> str:
        return f"eval-cas://sha256/{self.sha256}"

    @classmethod
    def from_ref(
        cls,
        ref: str,
        *,
        size: int,
        media_type: str = "application/octet-stream",
    ) -> "ProtectedEvaluationArtifact":
        prefix = "eval-cas://sha256/"
        if not ref.startswith(prefix):
            raise ValueError("protected evaluation ref uses an unsupported scheme")
        return cls(sha256=ref.removeprefix(prefix), size=size, media_type=media_type)


class FixtureCatalog(RuntimeContract):
    """Immutable exact-request fixture catalog.

    Fixture response bodies stay in protected CAS objects.  The catalog ledger
    stores request/response digests and protected refs only.
    """

    catalog_id: OpaqueId
    revision: Revision
    fixtures: tuple[FixtureResponse, ...]
    created_at: datetime
    catalog_digest: Sha256

    @field_validator("created_at")
    @classmethod
    def _created_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "created_at")

    @field_validator("fixtures")
    @classmethod
    def _fixtures_are_canonical(
        cls,
        value: tuple[FixtureResponse, ...],
    ) -> tuple[FixtureResponse, ...]:
        keys = tuple((item.capability_id, item.request_digest) for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("fixtures must be unique and canonically sorted")
        return value

    @model_validator(mode="after")
    def _catalog_digest_matches(self) -> "FixtureCatalog":
        expected = self.digest_for(
            catalog_id=self.catalog_id,
            revision=self.revision,
            fixtures=self.fixtures,
            created_at=self.created_at,
        )
        if self.catalog_digest != expected:
            raise ValueError("catalog_digest does not match canonical catalog content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class EvaluationCaseRef(RuntimeContract):
    case_id: OpaqueId
    revision: Revision


class EvaluationSuiteLimits(RuntimeContract):
    """Hard ceilings for a fixture-only case and its enclosing suite."""

    revision: Revision
    max_case_cost_microusd: Annotated[int, Field(ge=0)]
    max_suite_cost_microusd: Annotated[int, Field(ge=0)]
    max_case_model_turns: Annotated[int, Field(ge=1)]
    max_suite_model_turns: Annotated[int, Field(ge=1)]
    max_case_tool_calls: Annotated[int, Field(ge=1)]
    max_suite_tool_calls: Annotated[int, Field(ge=1)]
    max_case_tokens: Annotated[int, Field(ge=1)]
    max_suite_tokens: Annotated[int, Field(ge=1)]
    max_case_wall_time_ms: Annotated[int, Field(ge=1)]
    max_suite_wall_time_ms: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _suite_limits_cover_one_case(self) -> "EvaluationSuiteLimits":
        pairs = (
            (self.max_case_cost_microusd, self.max_suite_cost_microusd, "cost"),
            (self.max_case_model_turns, self.max_suite_model_turns, "model turns"),
            (self.max_case_tool_calls, self.max_suite_tool_calls, "tool calls"),
            (self.max_case_tokens, self.max_suite_tokens, "tokens"),
            (
                self.max_case_wall_time_ms,
                self.max_suite_wall_time_ms,
                "wall time",
            ),
        )
        for case_limit, suite_limit, label in pairs:
            if suite_limit < case_limit:
                raise ValueError(f"suite {label} limit must cover one case")
        return self


class EvaluationSuiteRun(RuntimeContract):
    """Immutable assignment for one resumable fixture-only suite execution."""

    suite_run_id: OpaqueId
    suite_id: OpaqueId
    suite_revision: Revision
    variant_id: OpaqueId
    variant_revision: Revision
    variant_digest: Sha256
    fixture_catalog_id: OpaqueId
    fixture_catalog_revision: Revision
    case_refs: tuple[EvaluationCaseRef, ...]
    revisions: EvaluationRevisionSet
    limits: EvaluationSuiteLimits
    created_at: datetime
    suite_run_digest: Sha256

    @field_validator("created_at")
    @classmethod
    def _created_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "created_at")

    @field_validator("case_refs")
    @classmethod
    def _case_refs_are_canonical(
        cls,
        value: tuple[EvaluationCaseRef, ...],
    ) -> tuple[EvaluationCaseRef, ...]:
        keys = tuple((item.case_id, item.revision) for item in value)
        if not keys:
            raise ValueError("suite run must contain at least one case")
        if keys != tuple(sorted(set(keys))):
            raise ValueError("case refs must be unique and canonically sorted")
        return value

    @model_validator(mode="after")
    def _suite_run_digest_matches(self) -> "EvaluationSuiteRun":
        expected = self.digest_for(
            suite_run_id=self.suite_run_id,
            suite_id=self.suite_id,
            suite_revision=self.suite_revision,
            variant_id=self.variant_id,
            variant_revision=self.variant_revision,
            variant_digest=self.variant_digest,
            fixture_catalog_id=self.fixture_catalog_id,
            fixture_catalog_revision=self.fixture_catalog_revision,
            case_refs=self.case_refs,
            revisions=self.revisions,
            limits=self.limits,
            created_at=self.created_at,
        )
        if self.suite_run_digest != expected:
            raise ValueError("suite_run_digest does not match canonical run content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class EvaluationCaseProgress(RuntimeContract):
    """Bounded restart cursor for the single active case in a suite."""

    case_id: OpaqueId
    case_revision: Revision
    resume_cursor_ref: str | None = Field(default=None, max_length=240)
    cost_microusd: Annotated[int, Field(ge=0)] = 0
    model_turns: Annotated[int, Field(ge=0)] = 0
    tool_calls: Annotated[int, Field(ge=0)] = 0
    tokens: Annotated[int, Field(ge=0)] = 0
    elapsed_ms: Annotated[int, Field(ge=0)] = 0


class EvaluationSuiteRunCheckpoint(RuntimeContract):
    """Append-only checkpoint; latest is a fold, never an in-place mutation."""

    suite_run_id: OpaqueId
    checkpoint_no: Annotated[int, Field(ge=0)]
    status: EvaluationStatus
    next_case_index: Annotated[int, Field(ge=0)]
    completed_result_ids: tuple[OpaqueId, ...] = ()
    active_case: EvaluationCaseProgress | None = None
    reason_codes: tuple[str, ...] = ()
    updated_at: datetime
    checkpoint_digest: Sha256

    @field_validator("updated_at")
    @classmethod
    def _updated_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "updated_at")

    @field_validator("completed_result_ids", "reason_codes")
    @classmethod
    def _checkpoint_collections_are_canonical(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("checkpoint collections must be unique and sorted")
        return value

    @model_validator(mode="after")
    def _checkpoint_digest_matches(self) -> "EvaluationSuiteRunCheckpoint":
        expected = self.digest_for(
            suite_run_id=self.suite_run_id,
            checkpoint_no=self.checkpoint_no,
            status=self.status,
            next_case_index=self.next_case_index,
            completed_result_ids=self.completed_result_ids,
            active_case=self.active_case,
            reason_codes=self.reason_codes,
            updated_at=self.updated_at,
        )
        if self.checkpoint_digest != expected:
            raise ValueError("checkpoint_digest does not match canonical checkpoint")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class PairedEvaluationReport(RuntimeContract):
    """Immutable, exact-revision candidate/control promotion evidence."""

    report_id: OpaqueId
    candidate_suite_run_ids: tuple[OpaqueId, ...]
    control_suite_run_ids: tuple[OpaqueId, ...]
    candidate_revisions: EvaluationRevisionSet
    control_revisions: EvaluationRevisionSet
    missing_candidate_case_ids: tuple[OpaqueId, ...] = ()
    missing_control_case_ids: tuple[OpaqueId, ...] = ()
    excluded_case_ids: tuple[OpaqueId, ...] = ()
    assessment: PromotionAssessment
    generated_at: datetime
    report_digest: Sha256

    @field_validator("generated_at")
    @classmethod
    def _generated_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "generated_at")

    @field_validator(
        "candidate_suite_run_ids",
        "control_suite_run_ids",
        "missing_candidate_case_ids",
        "missing_control_case_ids",
        "excluded_case_ids",
    )
    @classmethod
    def _report_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("report identifiers must be unique and sorted")
        return value

    @model_validator(mode="after")
    def _report_digest_matches(self) -> "PairedEvaluationReport":
        expected = self.digest_for(
            report_id=self.report_id,
            candidate_suite_run_ids=self.candidate_suite_run_ids,
            control_suite_run_ids=self.control_suite_run_ids,
            candidate_revisions=self.candidate_revisions,
            control_revisions=self.control_revisions,
            missing_candidate_case_ids=self.missing_candidate_case_ids,
            missing_control_case_ids=self.missing_control_case_ids,
            excluded_case_ids=self.excluded_case_ids,
            assessment=self.assessment,
            generated_at=self.generated_at,
        )
        if self.report_digest != expected:
            raise ValueError("report_digest does not match canonical report content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class HarnessManifestAssignment(RuntimeContract):
    variant_ref: Annotated[str, Field(min_length=1, max_length=256)]
    variant_digest: Sha256
    allocation_basis_points: Annotated[int, Field(ge=0, le=10_000)]


class HarnessManifest(RuntimeContract):
    """Signed release input consumed by run assignment.

    Signature verification is owned by the release service.  This immutable
    envelope defines exactly which canonical bytes are signed and prevents the
    repository from confusing signature presence with successful verification.
    """

    schema_version: Literal[1] = 1
    manifest_id: OpaqueId
    revision: Revision
    assignments: tuple[HarnessManifestAssignment, ...]
    fallback_variant_ref: Annotated[str, Field(min_length=1, max_length=256)]
    assignment_revision: Revision
    source_report_ref: Annotated[str, Field(min_length=1, max_length=256)]
    previous_manifest_ref: str | None = Field(default=None, max_length=256)
    issued_at: datetime
    not_before: datetime
    expires_at: datetime | None = None
    key_id: OpaqueId
    signature_algorithm: Literal["ed25519"] = "ed25519"
    payload_digest: Sha256
    signature_b64: Annotated[str, Field(min_length=40, max_length=512)]

    @field_validator("issued_at", "not_before", "expires_at")
    @classmethod
    def _manifest_times_are_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _aware_datetime(value, "manifest timestamp")

    @field_validator("assignments")
    @classmethod
    def _assignments_are_valid(
        cls,
        value: tuple[HarnessManifestAssignment, ...],
    ) -> tuple[HarnessManifestAssignment, ...]:
        refs = tuple(item.variant_ref for item in value)
        if not refs or refs != tuple(sorted(set(refs))):
            raise ValueError("manifest assignments must be unique and sorted")
        if sum(item.allocation_basis_points for item in value) != 10_000:
            raise ValueError("manifest assignment allocations must total 10000")
        return value

    @model_validator(mode="after")
    def _manifest_is_well_formed(self) -> "HarnessManifest":
        if self.expires_at is not None and self.expires_at <= self.not_before:
            raise ValueError("manifest expiry must follow not_before")
        if self.payload_digest != canonical_json_sha256(self.signed_payload()):
            raise ValueError("payload_digest does not match signed manifest payload")
        return self

    def signed_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"payload_digest", "signature_b64"},
            exclude_none=False,
        )

    @property
    def manifest_ref(self) -> str:
        return (
            f"harness-manifest://{self.manifest_id}/{self.revision}/"
            f"sha256/{self.payload_digest}"
        )


class HarnessManifestPointer(RuntimeContract):
    """CAS-updated local active pointer; referenced manifests stay immutable."""

    pointer_version: Annotated[int, Field(ge=1)]
    manifest_id: OpaqueId
    manifest_revision: Revision
    manifest_payload_digest: Sha256
    activation_decision_id: OpaqueId
    previous_manifest_ref: str | None = Field(default=None, max_length=512)
    updated_at: datetime
    pointer_digest: Sha256

    @field_validator("updated_at")
    @classmethod
    def _updated_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_datetime(value, "updated_at")

    @model_validator(mode="after")
    def _pointer_digest_matches(self) -> "HarnessManifestPointer":
        expected = self.digest_for(
            pointer_version=self.pointer_version,
            manifest_id=self.manifest_id,
            manifest_revision=self.manifest_revision,
            manifest_payload_digest=self.manifest_payload_digest,
            activation_decision_id=self.activation_decision_id,
            previous_manifest_ref=self.previous_manifest_ref,
            updated_at=self.updated_at,
        )
        if self.pointer_digest != expected:
            raise ValueError("pointer_digest does not match canonical pointer content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


class ProjectionJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class EvaluationProjectionJob(RuntimeContract):
    """Versioned durable cursor for a terminal-event projection job."""

    job_id: OpaqueId
    source_org_id: OpaqueId
    source_run_id: OpaqueId
    variant_id: OpaqueId | None = None
    policy_revision: Revision
    terminal_sequence_no: Annotated[int, Field(ge=1)]
    status: ProjectionJobStatus = ProjectionJobStatus.PENDING
    next_sequence_no: Annotated[int, Field(ge=1)] = 1
    attempt_count: Annotated[int, Field(ge=0)] = 0
    lease_owner_digest: Sha256 | None = None
    lease_expires_at: datetime | None = None
    trajectory_id: OpaqueId | None = None
    failure_reason_code: str | None = Field(default=None, max_length=120)
    version: Annotated[int, Field(ge=0)] = 0
    created_at: datetime
    updated_at: datetime
    job_digest: Sha256

    @field_validator("lease_expires_at", "created_at", "updated_at")
    @classmethod
    def _job_times_are_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return None if value is None else _aware_datetime(value, "projection timestamp")

    @model_validator(mode="after")
    def _job_is_valid(self) -> "EvaluationProjectionJob":
        if self.updated_at < self.created_at:
            raise ValueError("projection job update time precedes creation")
        if self.next_sequence_no > self.terminal_sequence_no + 1:
            raise ValueError("projection cursor is beyond the terminal event")
        has_lease = (
            self.lease_owner_digest is not None or self.lease_expires_at is not None
        )
        if has_lease and (
            self.lease_owner_digest is None or self.lease_expires_at is None
        ):
            raise ValueError("projection lease owner and expiry must appear together")
        if self.status is ProjectionJobStatus.RUNNING and not has_lease:
            raise ValueError("running projection job must hold a lease")
        if self.status is not ProjectionJobStatus.RUNNING and has_lease:
            raise ValueError("only a running projection job may hold a lease")
        if self.status is ProjectionJobStatus.SUCCEEDED and (
            self.trajectory_id is None or self.variant_id is None
        ):
            raise ValueError(
                "successful projection job must reference a trajectory and variant"
            )
        if (
            self.status
            in {
                ProjectionJobStatus.FAILED,
                ProjectionJobStatus.SKIPPED,
            }
            and not self.failure_reason_code
        ):
            raise ValueError(
                "failed or skipped projection job must carry a reason code"
            )
        if self.job_digest != self.digest_for(
            job_id=self.job_id,
            source_org_id=self.source_org_id,
            source_run_id=self.source_run_id,
            variant_id=self.variant_id,
            policy_revision=self.policy_revision,
            terminal_sequence_no=self.terminal_sequence_no,
            status=self.status,
            next_sequence_no=self.next_sequence_no,
            attempt_count=self.attempt_count,
            lease_owner_digest=self.lease_owner_digest,
            lease_expires_at=self.lease_expires_at,
            trajectory_id=self.trajectory_id,
            failure_reason_code=self.failure_reason_code,
            version=self.version,
            created_at=self.created_at,
            updated_at=self.updated_at,
        ):
            raise ValueError("job_digest does not match canonical projection job")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return _digest_json_safe(values)


EvaluationRepositoryRecord = (
    EvaluationCase
    | FixtureCatalog
    | TrajectoryManifest
    | EvaluationSuiteRun
    | EvaluationSuiteRunCheckpoint
    | EvaluationResult
    | PairedEvaluationReport
    | PromotionDecision
    | HarnessManifest
    | HarnessManifestPointer
    | EvaluationProjectionJob
)


def evaluation_record_owner(
    record: EvaluationRepositoryRecord,
) -> EvaluationRecordOwner:
    """Return the stable, content-independent owner key for a record."""

    if isinstance(record, EvaluationCase):
        kind = EvaluationRecordKind.CASE
        key: object = [record.case_id, record.revision]
    elif isinstance(record, FixtureCatalog):
        kind = EvaluationRecordKind.FIXTURE_CATALOG
        key = [record.catalog_id, record.revision]
    elif isinstance(record, TrajectoryManifest):
        kind = EvaluationRecordKind.TRAJECTORY_MANIFEST
        key = record.trajectory_id
    elif isinstance(record, EvaluationSuiteRun):
        kind = EvaluationRecordKind.SUITE_RUN
        key = record.suite_run_id
    elif isinstance(record, EvaluationSuiteRunCheckpoint):
        kind = EvaluationRecordKind.SUITE_RUN_CHECKPOINT
        key = [record.suite_run_id, record.checkpoint_no]
    elif isinstance(record, EvaluationResult):
        kind = EvaluationRecordKind.EVALUATION_RESULT
        key = record.evaluation_run_id
    elif isinstance(record, PairedEvaluationReport):
        kind = EvaluationRecordKind.PAIRED_REPORT
        key = record.report_id
    elif isinstance(record, PromotionDecision):
        kind = EvaluationRecordKind.PROMOTION_DECISION
        key = record.decision_id
    elif isinstance(record, HarnessManifest):
        kind = EvaluationRecordKind.HARNESS_MANIFEST
        key = [record.manifest_id, record.revision]
    elif isinstance(record, HarnessManifestPointer):
        kind = EvaluationRecordKind.HARNESS_MANIFEST_POINTER
        key = "active"
    elif isinstance(record, EvaluationProjectionJob):
        kind = EvaluationRecordKind.PROJECTION_JOB
        key = record.job_id
    else:  # pragma: no cover - closed union; guards unsafe dynamic callers
        raise TypeError(f"unsupported evaluation record: {type(record)!r}")
    return EvaluationRecordOwner(
        kind=kind,
        record_id=canonical_json_sha256({"kind": kind.value, "key": key}),
    )


def _digest_json_safe(values: object) -> str:
    """Hash typed F1 material via the repository's canonical JSON contract.

    Pydantic tuples, frozen models, enums, and timestamps are all legitimate
    F1 contract values but deliberately not inputs to ``canonical_json``.
    Converting once here keeps every manifest/result digest deterministic and
    cross-language compatible without leaking storage representations into the
    domain models.
    """

    return canonical_json_sha256(
        _without_empty_task_policy_projection(to_jsonable_python(values))
    )


def _without_empty_task_policy_projection(value: object) -> object:
    """Preserve legacy F1 digests while binding populated F4/F2/F3 projections.

    Safe controller/prompt/discovery fields were appended after F1 manifests and
    golden traces were immutable. Omitting absent/zero fields is wire compatible
    with those records; populated fields remain digest-bound.
    """

    if isinstance(value, list):
        return [_without_empty_task_policy_projection(item) for item in value]
    if not isinstance(value, dict):
        return value
    projection_keys = {
        "policy_record_kind",
        "policy_disposition",
        "policy_reason_codes",
        "policy_exhausted_dimensions",
        "prompt_record_kind",
        "prompt_cache_outcome",
        "prompt_cache_owner",
        "prompt_reason_code",
        "prompt_provider_reported",
        "prompt_input_tokens",
        "prompt_cached_input_tokens",
        "prompt_cache_creation_input_tokens",
        "invocation_record_kind",
        "invocation_status",
        "invocation_fallback_policy",
        "invocation_credential_mode",
        "invocation_decision",
        "invocation_reason",
        "invocation_attempt_state",
        "invocation_failure_class",
        "invocation_recovery_outcome",
        "invocation_exclusion_reasons",
        "invocation_provider_reported_usage",
        "invocation_route_ordinal",
        "invocation_attempt_ordinal",
        "invocation_attempt_count",
        "invocation_input_tokens",
        "invocation_output_tokens",
        "invocation_cost_microusd",
        "discovery_phase",
        "discovery_outcome",
        "discovery_candidate_count",
        "discovery_recall_rank",
        "discovery_result_tokens",
        "discovery_model_turns",
    }
    return {
        key: _without_empty_task_policy_projection(item)
        for key, item in value.items()
        if key not in projection_keys or (item is not None and item != [] and item != 0)
    }


def _aware_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value
