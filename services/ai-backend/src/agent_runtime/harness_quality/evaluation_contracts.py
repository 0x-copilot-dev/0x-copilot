"""F1 contracts for hermetic harness evaluation and promotion.

These models intentionally retain references, revisions, digests, and bounded
observable action metadata only.  Raw prompts, model output, credentials, tool
arguments, and tool result bodies never become evaluation telemetry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

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


class ScorerResult(RuntimeContract):
    scorer_id: OpaqueId
    score: Annotated[float, Field(ge=0, le=1)]
    passed: bool
    hard_gate: bool = False
    reason_code: Annotated[str, Field(min_length=1, max_length=120)]


class EvaluationResult(RuntimeContract):
    evaluation_run_id: OpaqueId
    case_id: OpaqueId
    variant_id: OpaqueId
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
            case_id=self.case_id,
            variant_id=self.variant_id,
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


class PromotionDecision(RuntimeContract):
    decision_id: OpaqueId
    candidate_variant_id: OpaqueId
    control_variant_id: OpaqueId
    suite_revisions: tuple[Revision, ...]
    thresholds_revision: Revision
    report_ref: OpaqueId
    status: PromotionStatus
    actor: OpaqueId
    decided_at: datetime
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]

    @field_validator("suite_revisions")
    @classmethod
    def _suite_revisions_are_present(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("suite_revisions must not be empty")
        return value


def _digest_json_safe(values: object) -> str:
    """Hash typed F1 material via the repository's canonical JSON contract.

    Pydantic tuples, frozen models, enums, and timestamps are all legitimate
    F1 contract values but deliberately not inputs to ``canonical_json``.
    Converting once here keeps every manifest/result digest deterministic and
    cross-language compatible without leaking storage representations into the
    domain models.
    """

    return canonical_json_sha256(to_jsonable_python(values))
