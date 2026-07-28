"""Content-free durable records for the F4 task-aware tool controller.

These records are deliberately safe to place directly in the canonical run
event journal.  They contain stable identities, keyed digests, closed reason
codes, counters, and protected references only.  User prompts, plan bodies,
raw arguments/results, credentials, physical paths, and evidence text have no
field through which they can enter the journal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self, TypeAlias, runtime_checkable

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    TypeAdapter,
    field_validator,
    model_validator,
)

from agent_runtime.capabilities.task_policy import (
    PlanningRequirement,
    TaskFamily,
    TaskPolicySelectionReason,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"
_CAPABILITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,239}$"
_CODE_PATTERN = r"^[a-z][a-z0-9._:-]{0,119}$"
_SELECTION_REF_PATTERN = (
    r"^task-policy(?:-selection)?://"
    r"[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*$"
)
_PLAN_REF_PATTERN = r"^task-plan://[A-Za-z0-9][A-Za-z0-9._:-]*/sha256/[0-9a-f]{64}$"
_BUDGET_REF_PATTERN = (
    r"^budget://[A-Za-z0-9][A-Za-z0-9._:-]{0,159}/sha256/[0-9a-f]{64}$"
)


class TaskPolicyJournalRecordKind(StrEnum):
    """Closed F4 journal vocabulary."""

    PROFILE_SELECTED = "profile_selected"
    PLAN_BOUND = "plan_bound"
    INTENT_RECORDED = "intent_recorded"
    ADMISSION_RECORDED = "admission_recorded"
    OUTCOME_RECORDED = "outcome_recorded"
    FEEDBACK_RECORDED = "feedback_recorded"
    BUDGET_RECORDED = "budget_recorded"
    PROGRESS_RECORDED = "progress_recorded"


class TaskPolicyAdmissionDisposition(StrEnum):
    """Authoritative pre-dispatch result."""

    ADMITTED = "admitted"
    BLOCKED = "blocked"
    SHADOW_ADMITTED = "shadow_admitted"


class TaskPolicyOutcomeStatus(StrEnum):
    """Observed operation completion class."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class TaskPolicyFeedbackDisposition(StrEnum):
    """Bounded controller handoff after an admission or outcome."""

    CONTINUE = "continue"
    STOP = "stop"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    BLOCKED = "blocked"


class TaskPolicyBudgetDimension(StrEnum):
    """Closed dimensions that may cause a hard stop."""

    MODEL_TURNS = "model_turns"
    TOOL_CALLS = "tool_calls"
    COST = "cost"
    ACTIVE_TOOL_TIME = "active_tool_time"
    DEADLINE = "deadline"


class TaskPolicyReasonCode(StrEnum):
    """Closed controller explanations; arbitrary model/user text is forbidden."""

    ADMITTED = "admitted"
    SHADOW_ADMITTED = "shadow_admitted"
    WITHIN_BUDGET = "within_budget"
    PLANNING_REQUIRED = "planning_required"
    PLAN_MISSING = "plan_missing"
    EXACT_DUPLICATE = "exact_duplicate"
    PROFILE_TOOL_CALL_LIMIT = "profile_tool_call_limit"
    SEMANTIC_QUERY_OVERLAP = "semantic_query_overlap"
    SAME_SOURCES_NO_NEW_EVIDENCE = "same_sources_no_new_evidence"
    SAME_ERROR_WITHOUT_CHANGED_INPUT = "same_error_without_changed_input"
    RETRYABLE_ERROR = "retryable_error"
    OPERATION_FAILED_RETRYABLE = "operation_failed_retryable"
    BUDGET_LOW = "budget_low"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    OBJECTIVE_SATISFIED = "objective_satisfied"
    POLICY_REQUIRES_USER_INPUT = "policy_requires_user_input"
    POLICY_BLOCKED = "policy_blocked"
    AUTHORIZATION_BLOCKED = "authorization_blocked"
    NEW_EVIDENCE = "new_evidence"
    OPERATION_SUCCEEDED = "operation_succeeded"
    OPERATION_COMPLETED = "operation_completed"
    OPERATION_FAILED = "operation_failed"
    OPERATION_INDETERMINATE = "operation_indeterminate"
    OPERATION_REPLAYED = "operation_replayed"
    UNKNOWN = "unknown"


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class _TaskPolicyJournalRecordBase(RuntimeContract):
    """Shared stable identity and self-authenticating digest."""

    schema_version: Literal[1] = 1
    record_kind: TaskPolicyJournalRecordKind
    record_id: str = Field(pattern=_ID_PATTERN)
    record_digest: str = Field(pattern=_DIGEST_PATTERN)
    run_id: str = Field(pattern=_ID_PATTERN)
    snapshot_id: str = Field(pattern=_ID_PATTERN)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        return _aware(value, "created_at")

    @model_validator(mode="after")
    def _digest_matches(self) -> Self:
        if self.record_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError("task-policy record digest does not match canonical body")
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return the semantic body covered by ``record_digest``.

        Observation time is excluded so equivalent concurrent writers converge
        on the first durable event.  Stable record identity remains covered:
        reusing an ID for another logical fact is an idempotency conflict.
        """

        return self.model_dump(
            mode="json",
            exclude={"record_digest", "created_at"},
            exclude_none=False,
            warnings=False,
        )

    @classmethod
    def create(cls, **values: object) -> Self:
        """Build a self-authenticating record of the concrete subclass."""

        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc),
            **values,
        }
        provisional = cls.model_construct(
            **payload,
            record_digest="0" * 64,
        )
        return cls(
            **payload,
            record_digest=canonical_json_sha256(provisional.digest_payload()),
        )


class TaskPolicyProfileSelectedRecord(_TaskPolicyJournalRecordBase):
    """Body-free immutable binding to a deployment-owned profile selection."""

    record_kind: Literal[TaskPolicyJournalRecordKind.PROFILE_SELECTED] = (
        TaskPolicyJournalRecordKind.PROFILE_SELECTED
    )
    selection_ref: str = Field(pattern=_SELECTION_REF_PATTERN)
    selection_digest: str = Field(pattern=_DIGEST_PATTERN)
    profile_id: str = Field(pattern=_ID_PATTERN)
    profile_revision: str = Field(pattern=_ID_PATTERN)
    task_family: TaskFamily
    planning_requirement: PlanningRequirement
    selection_reason: TaskPolicySelectionReason


class TaskPolicyPlanBoundRecord(_TaskPolicyJournalRecordBase):
    """Deterministic plan identity; the public plan body stays behind ``plan_ref``."""

    record_kind: Literal[TaskPolicyJournalRecordKind.PLAN_BOUND] = (
        TaskPolicyJournalRecordKind.PLAN_BOUND
    )
    selection_ref: str = Field(pattern=_SELECTION_REF_PATTERN)
    selection_digest: str = Field(pattern=_DIGEST_PATTERN)
    plan_id: str = Field(pattern=_ID_PATTERN)
    plan_ref: str = Field(pattern=_PLAN_REF_PATTERN)
    plan_digest: str = Field(pattern=_DIGEST_PATTERN)
    created_by: Literal["model", "deterministic"]
    status: Literal["pending", "active", "completed", "blocked", "cancelled"]
    step_count: PositiveInt = Field(le=32)
    success_evidence_requirement_count: PositiveInt = Field(le=16)


class TaskPolicyIntentRecordedRecord(_TaskPolicyJournalRecordBase):
    """Pre-dispatch intent keyed only by opaque identity and keyed fingerprint."""

    record_kind: Literal[TaskPolicyJournalRecordKind.INTENT_RECORDED] = (
        TaskPolicyJournalRecordKind.INTENT_RECORDED
    )
    selection_ref: str = Field(pattern=_SELECTION_REF_PATTERN)
    selection_digest: str = Field(pattern=_DIGEST_PATTERN)
    tool_call_id: str = Field(pattern=_ID_PATTERN)
    operation_id: str = Field(pattern=_ID_PATTERN)
    capability_id: str = Field(pattern=_CAPABILITY_PATTERN)
    request_fingerprint: str = Field(pattern=_DIGEST_PATTERN)
    plan_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    plan_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    plan_step_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    expected_evidence_kind: str | None = Field(default=None, pattern=_CODE_PATTERN)

    @model_validator(mode="after")
    def _plan_binding_is_complete(self) -> Self:
        plan_fields = (self.plan_id, self.plan_digest)
        if (plan_fields[0] is None) != (plan_fields[1] is None):
            raise ValueError("intent plan_id and plan_digest must be supplied together")
        if self.plan_step_id is not None and self.plan_id is None:
            raise ValueError("intent plan_step_id requires a plan binding")
        return self


class TaskPolicyAdmissionRecordedRecord(_TaskPolicyJournalRecordBase):
    """One graph-wide controller decision before budget consumption/dispatch."""

    record_kind: Literal[TaskPolicyJournalRecordKind.ADMISSION_RECORDED] = (
        TaskPolicyJournalRecordKind.ADMISSION_RECORDED
    )
    tool_call_id: str = Field(pattern=_ID_PATTERN)
    operation_id: str = Field(pattern=_ID_PATTERN)
    intent_record_id: str = Field(pattern=_ID_PATTERN)
    intent_digest: str = Field(pattern=_DIGEST_PATTERN)
    disposition: TaskPolicyAdmissionDisposition
    reason_codes: tuple[TaskPolicyReasonCode, ...] = Field(min_length=1, max_length=16)
    duplicate_of_operation_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    model_turn_ordinal: PositiveInt
    tool_call_ordinal: PositiveInt

    @field_validator("reason_codes")
    @classmethod
    def _bounded_unique_reasons(
        cls,
        value: tuple[TaskPolicyReasonCode, ...],
    ) -> tuple[TaskPolicyReasonCode, ...]:
        if len(set(value)) != len(value):
            raise ValueError("reason codes must be unique")
        return value


class TaskPolicyOutcomeRecordedRecord(_TaskPolicyJournalRecordBase):
    """Body-free observed operation result."""

    record_kind: Literal[TaskPolicyJournalRecordKind.OUTCOME_RECORDED] = (
        TaskPolicyJournalRecordKind.OUTCOME_RECORDED
    )
    tool_call_id: str = Field(pattern=_ID_PATTERN)
    operation_id: str = Field(pattern=_ID_PATTERN)
    intent_record_id: str = Field(pattern=_ID_PATTERN)
    intent_digest: str = Field(pattern=_DIGEST_PATTERN)
    request_fingerprint: str = Field(pattern=_DIGEST_PATTERN)
    status: TaskPolicyOutcomeStatus
    result_fingerprint: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    error_fingerprint: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    failure_class: str | None = Field(default=None, pattern=_CODE_PATTERN)
    retryable: bool = False
    new_evidence_count: NonNegativeInt = 0
    observed_source_count: NonNegativeInt = 0
    latency_ms: NonNegativeInt = 0

    @model_validator(mode="after")
    def _outcome_shape_matches_status(self) -> Self:
        if self.status is TaskPolicyOutcomeStatus.SUCCEEDED:
            if self.failure_class is not None or self.error_fingerprint is not None:
                raise ValueError("successful outcomes cannot contain failure facts")
            if self.result_fingerprint is None:
                raise ValueError("successful outcomes require a result fingerprint")
        elif self.failure_class is None or self.error_fingerprint is None:
            raise ValueError(
                "failed/indeterminate outcomes require failure fingerprints"
            )
        return self


class TaskPolicyFeedbackRecordedRecord(_TaskPolicyJournalRecordBase):
    """Bounded post-decision feedback safe for replay and prompt projection."""

    record_kind: Literal[TaskPolicyJournalRecordKind.FEEDBACK_RECORDED] = (
        TaskPolicyJournalRecordKind.FEEDBACK_RECORDED
    )
    tool_call_id: str = Field(pattern=_ID_PATTERN)
    operation_id: str = Field(pattern=_ID_PATTERN)
    admission_record_id: str = Field(pattern=_ID_PATTERN)
    outcome_record_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    disposition: TaskPolicyFeedbackDisposition
    reason_codes: tuple[TaskPolicyReasonCode, ...] = Field(min_length=1, max_length=16)
    duplicate_of_operation_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    new_evidence_count: NonNegativeInt = 0
    total_evidence_count: NonNegativeInt = 0
    budget_record_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    budget_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)

    @field_validator("reason_codes")
    @classmethod
    def _bounded_unique_reasons(
        cls,
        value: tuple[TaskPolicyReasonCode, ...],
    ) -> tuple[TaskPolicyReasonCode, ...]:
        return TaskPolicyAdmissionRecordedRecord._bounded_unique_reasons(value)

    @model_validator(mode="after")
    def _budget_binding_is_complete(self) -> Self:
        if (self.budget_record_id is None) != (self.budget_digest is None):
            raise ValueError(
                "feedback budget_record_id and budget_digest must be supplied together"
            )
        return self


class TaskPolicyBudgetRecordedRecord(_TaskPolicyJournalRecordBase):
    """Exact cumulative spend and the effective ceilings used for admission."""

    record_kind: Literal[TaskPolicyJournalRecordKind.BUDGET_RECORDED] = (
        TaskPolicyJournalRecordKind.BUDGET_RECORDED
    )
    budget_envelope_ref: str = Field(pattern=_BUDGET_REF_PATTERN)
    effective_budget_digest: str = Field(pattern=_DIGEST_PATTERN)
    model_turns_used: NonNegativeInt = 0
    tool_calls_used: NonNegativeInt = 0
    cost_microusd_used: NonNegativeInt = 0
    active_tool_time_ms_used: NonNegativeInt = 0
    model_turn_limit: PositiveInt | None = None
    tool_call_limit: PositiveInt | None = None
    cost_microusd_limit: NonNegativeInt | None = None
    active_tool_time_ms_limit: NonNegativeInt | None = None
    deadline_at: datetime | None = None
    exhausted_dimensions: tuple[TaskPolicyBudgetDimension, ...] = Field(
        default=(), max_length=5
    )
    hard_stop: bool = False

    @field_validator("deadline_at")
    @classmethod
    def _aware_deadline(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value, "deadline_at")

    @field_validator("exhausted_dimensions")
    @classmethod
    def _unique_dimensions(
        cls,
        value: tuple[TaskPolicyBudgetDimension, ...],
    ) -> tuple[TaskPolicyBudgetDimension, ...]:
        if len(set(value)) != len(value):
            raise ValueError("exhausted budget dimensions must be unique")
        return value

    @model_validator(mode="after")
    def _hard_stop_matches_exhaustion(self) -> Self:
        if self.hard_stop != bool(self.exhausted_dimensions):
            raise ValueError("hard_stop must match exhausted budget dimensions")
        return self


class TaskPolicyProgressRecordedRecord(_TaskPolicyJournalRecordBase):
    """Public-plan progress facts without labels, objectives, or evidence bodies."""

    record_kind: Literal[TaskPolicyJournalRecordKind.PROGRESS_RECORDED] = (
        TaskPolicyJournalRecordKind.PROGRESS_RECORDED
    )
    plan_id: str = Field(pattern=_ID_PATTERN)
    plan_digest: str = Field(pattern=_DIGEST_PATTERN)
    plan_status: Literal["pending", "active", "completed", "blocked", "cancelled"]
    step_count: PositiveInt = Field(le=32)
    completed_step_count: NonNegativeInt = Field(le=32)
    blocked_step_count: NonNegativeInt = Field(le=32)
    active_step_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    evidence_count: NonNegativeInt = 0
    checkpoint_ordinal: NonNegativeInt = 0
    waiting_for_approval: bool = False

    @model_validator(mode="after")
    def _valid_step_counts(self) -> Self:
        if self.completed_step_count + self.blocked_step_count > self.step_count:
            raise ValueError("progress step counts exceed the bound plan")
        return self


TaskPolicyJournalRecord: TypeAlias = Annotated[
    TaskPolicyProfileSelectedRecord
    | TaskPolicyPlanBoundRecord
    | TaskPolicyIntentRecordedRecord
    | TaskPolicyAdmissionRecordedRecord
    | TaskPolicyOutcomeRecordedRecord
    | TaskPolicyFeedbackRecordedRecord
    | TaskPolicyBudgetRecordedRecord
    | TaskPolicyProgressRecordedRecord,
    Field(discriminator="record_kind"),
]
TASK_POLICY_JOURNAL_RECORD_ADAPTER = TypeAdapter(TaskPolicyJournalRecord)


def validate_task_policy_journal_record(value: object) -> TaskPolicyJournalRecord:
    """Parse one strict discriminated journal record."""

    return TASK_POLICY_JOURNAL_RECORD_ADAPTER.validate_python(value)


class TaskPolicyJournalConflict(RuntimeError):
    """A stable F4 record ID was reused for a different semantic body."""

    def __init__(self, *, run_id: str, record_id: str) -> None:
        self.run_id = run_id
        self.record_id = record_id
        super().__init__(f"run {run_id} task-policy record {record_id} conflicts")


class TaskPolicyJournalScopeConflict(RuntimeError):
    """A run's F4 journal belongs to another verified subject."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} task-policy state is outside requested scope")


class TaskPolicyJournalSnapshotConflict(RuntimeError):
    """An F4 record does not bind the run's immutable control snapshot."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} task-policy snapshot binding conflicts")


class TaskPolicyJournalCorruption(RuntimeError):
    """Canonical F4 events cannot be replayed as a valid ordered journal."""

    def __init__(self, *, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"run {run_id} task-policy journal is invalid: {reason}")


class TaskPolicyJournalWrite(RuntimeContract):
    """Verified transport facts needed for one canonical append."""

    org_id: str = Field(min_length=1, max_length=160)
    trace_id: str = Field(min_length=1, max_length=160)
    subject_fingerprint: str = Field(pattern=_DIGEST_PATTERN)
    record: TaskPolicyJournalRecord


class SequencedTaskPolicyJournalRecord(RuntimeContract):
    """One F4 fact plus its canonical per-run event sequence."""

    sequence_no: PositiveInt
    record: TaskPolicyJournalRecord


@runtime_checkable
class TaskPolicyJournalStorePort(Protocol):
    """Append-only F4 fold over the canonical run event stream."""

    async def append(
        self,
        write: TaskPolicyJournalWrite,
    ) -> SequencedTaskPolicyJournalRecord: ...

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedTaskPolicyJournalRecord, ...]: ...


__all__ = [
    "SequencedTaskPolicyJournalRecord",
    "TaskPolicyAdmissionDisposition",
    "TaskPolicyAdmissionRecordedRecord",
    "TaskPolicyBudgetDimension",
    "TaskPolicyBudgetRecordedRecord",
    "TaskPolicyFeedbackDisposition",
    "TaskPolicyFeedbackRecordedRecord",
    "TaskPolicyIntentRecordedRecord",
    "TaskPolicyJournalConflict",
    "TaskPolicyJournalCorruption",
    "TaskPolicyJournalRecord",
    "TaskPolicyJournalRecordKind",
    "TaskPolicyJournalScopeConflict",
    "TaskPolicyJournalSnapshotConflict",
    "TaskPolicyJournalStorePort",
    "TaskPolicyJournalWrite",
    "TaskPolicyOutcomeRecordedRecord",
    "TaskPolicyOutcomeStatus",
    "TaskPolicyPlanBoundRecord",
    "TaskPolicyProfileSelectedRecord",
    "TaskPolicyProgressRecordedRecord",
    "TaskPolicyReasonCode",
    "validate_task_policy_journal_record",
]
