"""Deterministic, privacy-preserving F4 task policy primitives.

This module deliberately does *not* choose capabilities or authorize an
operation.  It classifies a run into a closed policy family and gives the
existing operation and budget gates bounded feedback about duplicate work.  A
model can neither select a more permissive profile nor use this controller to
bypass the hard ``ToolBudgetGuard`` / approval boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
import hashlib
import hmac
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes


class TaskFamily(StrEnum):
    """Closed task-policy families; unknown is always the conservative fallback."""

    PUBLIC_RESEARCH = "public_research"
    CONNECTED_RECORD_LOOKUP = "connected_record_lookup"
    LIBRARY_GROUNDING = "library_grounding"
    WORKSPACE_ANALYSIS = "workspace_analysis"
    TRANSFORMATION = "transformation"
    ARTIFACT_DRAFTING = "artifact_drafting"
    EFFECT_PROPOSAL = "effect_proposal"
    CODE_DIAGNOSIS = "code_diagnosis"
    DELEGATED_ANALYSIS = "delegated_analysis"
    UNKNOWN = "unknown"


class PlanningRequirement(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ToolUseDisposition(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    BLOCKED = "blocked"


class TaskPolicySelectionReason(StrEnum):
    """Content-free reason explaining a server-side profile selection."""

    EFFECT_INTENT = "effect_intent"
    DELEGATION_INTENT = "delegation_intent"
    SERVER_SELECTED_FAMILY = "server_selected_family"
    CAPABILITY_HINT = "capability_hint"
    CONSERVATIVE_DEFAULT = "conservative_default"


class ToolPlanCreator(StrEnum):
    """Closed set of plan producers; neither value conveys authority."""

    MODEL = "model"
    DETERMINISTIC = "deterministic"


class ToolPlanStatus(StrEnum):
    """Public run-plan lifecycle states."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ToolPlanStepStatus(StrEnum):
    """Public step lifecycle states."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class TaskPolicyProfile(RuntimeContract):
    """A versioned, bounded profile selected by trusted runtime context."""

    profile_id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=160)
    task_family: TaskFamily
    planning_requirement: PlanningRequirement = PlanningRequirement.OPTIONAL
    model_turn_limit: int | None = Field(default=None, ge=1, le=1_000)
    tool_call_limits: dict[str, int] = Field(default_factory=dict)
    checkpoint_interval: int = Field(default=3, ge=1, le=100)
    enforce_exact_duplicates: bool = False
    enforce_unchanged_errors: bool = True
    max_source_history: int = Field(default=500, ge=1, le=500)

    @field_validator("tool_call_limits")
    @classmethod
    def _valid_tool_call_limits(cls, value: dict[str, int]) -> dict[str, int]:
        for tool_name, limit in value.items():
            if not tool_name.strip():
                raise ValueError("tool_call_limits keys must be non-empty")
            if limit < 1:
                raise ValueError("tool_call_limits values must be positive")
        return dict(value)

    def call_limit_for(self, capability_id: str) -> int | None:
        """Return this profile's cap, without changing platform budget policy."""

        return self.tool_call_limits.get(capability_id, self.tool_call_limits.get("*"))

    @classmethod
    def conservative_unknown(cls, *, revision: str) -> "TaskPolicyProfile":
        """Build the bounded fallback used when no task signal matches.

        The profile remains subordinate to platform and capability budgets.
        Requiring a short plan, checking duplicates, and allowing at most three
        calls per capability keeps an unclassified run useful without silently
        inheriting a more permissive specialized profile.
        """

        return cls(
            profile_id="unknown.general",
            revision=revision,
            task_family=TaskFamily.UNKNOWN,
            planning_requirement=PlanningRequirement.REQUIRED,
            model_turn_limit=8,
            tool_call_limits={"*": 3},
            checkpoint_interval=1,
            enforce_exact_duplicates=True,
            enforce_unchanged_errors=True,
        )


class TaskPolicyRequest(RuntimeContract):
    """Server-derived, content-free signals available before the first tool call.

    These fields must be assembled from verified route, capability, and effect
    metadata. Model output is intentionally not represented here. The policy
    revision is persisted with the run and must match the resolver bundle.
    """

    run_id: str = Field(min_length=1, max_length=160)
    policy_revision: str = Field(min_length=1, max_length=160)
    server_selected_family: TaskFamily | None = None
    capability_hints: frozenset[str] = Field(default_factory=frozenset)
    has_effect_intent: bool = False
    has_subagent_intent: bool = False

    @field_validator("capability_hints")
    @classmethod
    def _normalize_capability_hints(cls, value: frozenset[str]) -> frozenset[str]:
        if len(value) > 32:
            raise ValueError("capability_hints must contain at most 32 values")
        normalized = frozenset(hint.strip().lower() for hint in value if hint.strip())
        if any(len(hint) > 80 for hint in normalized):
            raise ValueError("capability_hints values must be at most 80 characters")
        return normalized


class TaskPolicySelection(RuntimeContract):
    """Immutable profile identity bound to one run before model execution."""

    run_id: str = Field(min_length=1, max_length=160)
    profile_id: str = Field(min_length=1, max_length=160)
    profile_revision: str = Field(min_length=1, max_length=160)
    task_family: TaskFamily
    planning_requirement: PlanningRequirement
    selection_reason: TaskPolicySelectionReason


class ToolPlanStep(RuntimeContract):
    """One concise, user-visible step; it contains no private reasoning."""

    step_id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=240)
    expected_evidence_kinds: tuple[str, ...] = Field(
        default_factory=tuple, max_length=16
    )
    status: ToolPlanStepStatus = ToolPlanStepStatus.PENDING

    @field_validator("expected_evidence_kinds")
    @classmethod
    def _validate_evidence_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(kind.strip() for kind in value)
        if any(not kind or len(kind) > 80 for kind in normalized):
            raise ValueError(
                "expected_evidence_kinds must be non-empty and at most 80 characters"
            )
        if len(set(normalized)) != len(normalized):
            raise ValueError("expected_evidence_kinds must be unique")
        return normalized


class SuccessEvidenceRequirement(RuntimeContract):
    """Content-free success criterion for a public plan."""

    evidence_kind: str = Field(min_length=1, max_length=80)
    minimum_count: int = Field(default=1, ge=1, le=1_000)
    description: str | None = Field(default=None, max_length=240)


class RunToolPlan(RuntimeContract):
    """Bounded public plan associated with the selected run policy."""

    plan_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    profile_id: str = Field(min_length=1, max_length=160)
    profile_revision: str = Field(min_length=1, max_length=160)
    task_family: TaskFamily
    objective: str = Field(min_length=1, max_length=512)
    steps: tuple[ToolPlanStep, ...] = Field(min_length=1, max_length=32)
    success_evidence: tuple[SuccessEvidenceRequirement, ...] = Field(
        min_length=1, max_length=16
    )
    created_by: ToolPlanCreator
    status: ToolPlanStatus = ToolPlanStatus.PENDING

    @model_validator(mode="after")
    def _validate_plan_state(self) -> "RunToolPlan":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step_id values must be unique")
        active_steps = sum(
            step.status is ToolPlanStepStatus.ACTIVE for step in self.steps
        )
        if active_steps > 1:
            raise ValueError("a plan can have at most one active step")
        if self.status is ToolPlanStatus.COMPLETED and any(
            step.status
            not in {ToolPlanStepStatus.COMPLETED, ToolPlanStepStatus.SKIPPED}
            for step in self.steps
        ):
            raise ValueError("a completed plan cannot contain unfinished steps")
        return self

    @classmethod
    def for_selection(
        cls,
        *,
        selection: TaskPolicySelection,
        plan_id: str,
        objective: str,
        steps: tuple[ToolPlanStep, ...],
        success_evidence: tuple[SuccessEvidenceRequirement, ...],
        created_by: ToolPlanCreator,
        status: ToolPlanStatus = ToolPlanStatus.PENDING,
    ) -> "RunToolPlan":
        """Construct a plan whose run/profile identity cannot drift."""

        return cls(
            plan_id=plan_id,
            run_id=selection.run_id,
            profile_id=selection.profile_id,
            profile_revision=selection.profile_revision,
            task_family=selection.task_family,
            objective=objective,
            steps=steps,
            success_evidence=success_evidence,
            created_by=created_by,
            status=status,
        )


class ToolUseIntent(RuntimeContract):
    """Public action intent. The fingerprint is keyed; raw arguments never persist."""

    operation_id: str = Field(min_length=1, max_length=160)
    capability_id: str = Field(min_length=1, max_length=240)
    canonical_request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_step_id: str | None = Field(default=None, max_length=160)
    objective: str | None = Field(default=None, max_length=512)
    expected_evidence_kind: str | None = Field(default=None, max_length=80)


class ToolUseFeedback(RuntimeContract):
    """Bounded, content-free feedback returned around a governed operation."""

    disposition: ToolUseDisposition
    reason_code: str = Field(min_length=1, max_length=120)
    budget_remaining: int | None = Field(default=None, ge=0)
    duplicate_of_operation_id: str | None = Field(default=None, max_length=160)
    new_evidence_count: int = Field(default=0, ge=0)


class ToolPolicyRejected(ToolBudgetRejected):
    """A non-fatal policy refusal surfaced to the model as a tool result.

    It is deliberately a ``ToolBudgetRejected`` subclass so the existing tool
    error policy keeps the run alive after the controller prevented duplicate
    dispatch. The model can revise its plan and answer with evidence already
    gathered; a refusal must not discard earlier useful work.
    """


class ToolOperationOutcome(RuntimeContract):
    """Observable outcome data only; result bodies and arguments stay protected."""

    operation_id: str = Field(min_length=1, max_length=160)
    capability_id: str = Field(min_length=1, max_length=240)
    succeeded: bool
    error_class: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _error_is_present_for_failure(self) -> "ToolOperationOutcome":
        if not self.succeeded and not self.error_class:
            raise ValueError("failed outcome requires error_class")
        return self


class TaskPolicyResolver:
    """Resolve one revisioned policy bundle from server-derived signals.

    Effect and delegation facts take precedence over a server-selected family,
    preventing an explicit route from weakening the policy for a sensitive
    operation. It never consumes model text and is not an authorization or
    capability-discovery path.
    """

    _HINT_FAMILIES: tuple[tuple[frozenset[str], TaskFamily], ...] = (
        (frozenset({"effect", "write", "destructive"}), TaskFamily.EFFECT_PROPOSAL),
        (frozenset({"subagent", "delegate"}), TaskFamily.DELEGATED_ANALYSIS),
        (frozenset({"web", "browser", "search"}), TaskFamily.PUBLIC_RESEARCH),
        (frozenset({"mcp", "connector", "record"}), TaskFamily.CONNECTED_RECORD_LOOKUP),
        (frozenset({"workspace", "file"}), TaskFamily.WORKSPACE_ANALYSIS),
        (frozenset({"code", "test"}), TaskFamily.CODE_DIAGNOSIS),
        (frozenset({"artifact", "draft"}), TaskFamily.ARTIFACT_DRAFTING),
        (frozenset({"calculate", "transform"}), TaskFamily.TRANSFORMATION),
    )

    def __init__(
        self,
        profiles: Sequence[TaskPolicyProfile],
        *,
        policy_revision: str | None = None,
    ) -> None:
        revisions = {profile.revision for profile in profiles}
        if policy_revision is None:
            if len(revisions) != 1:
                raise ValueError(
                    "policy_revision is required unless profiles share one revision"
                )
            policy_revision = next(iter(revisions))
        if not policy_revision.strip():
            raise ValueError("policy_revision must be non-empty")
        if revisions.difference({policy_revision}):
            raise ValueError("all profiles must match the resolver policy_revision")

        by_family: dict[TaskFamily, TaskPolicyProfile] = {}
        for profile in profiles:
            if profile.task_family in by_family:
                raise ValueError("exactly one task policy profile per task family")
            by_family[profile.task_family] = profile
        if TaskFamily.UNKNOWN not in by_family:
            by_family[TaskFamily.UNKNOWN] = TaskPolicyProfile.conservative_unknown(
                revision=policy_revision
            )
        self._policy_revision = policy_revision
        self._by_family = by_family

    def resolve(self, request: TaskPolicyRequest) -> TaskPolicyProfile:
        """Return the selected profile after enforcing revision affinity."""

        profile, _ = self._resolve_with_reason(request)
        return profile

    def resolve_selection(self, request: TaskPolicyRequest) -> TaskPolicySelection:
        """Return the immutable, persistable run/profile binding."""

        profile, reason = self._resolve_with_reason(request)
        return TaskPolicySelection(
            run_id=request.run_id,
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            task_family=profile.task_family,
            planning_requirement=profile.planning_requirement,
            selection_reason=reason,
        )

    def _resolve_with_reason(
        self, request: TaskPolicyRequest
    ) -> tuple[TaskPolicyProfile, TaskPolicySelectionReason]:
        if request.policy_revision != self._policy_revision:
            raise ValueError(
                "task policy request revision does not match the resolver bundle"
            )
        if request.has_effect_intent:
            return (
                self._by_family.get(
                    TaskFamily.EFFECT_PROPOSAL, self._by_family[TaskFamily.UNKNOWN]
                ),
                TaskPolicySelectionReason.EFFECT_INTENT,
            )
        if request.has_subagent_intent:
            return (
                self._by_family.get(
                    TaskFamily.DELEGATED_ANALYSIS, self._by_family[TaskFamily.UNKNOWN]
                ),
                TaskPolicySelectionReason.DELEGATION_INTENT,
            )
        if request.server_selected_family is not None:
            return (
                self._by_family.get(
                    request.server_selected_family, self._by_family[TaskFamily.UNKNOWN]
                ),
                TaskPolicySelectionReason.SERVER_SELECTED_FAMILY,
            )
        hints = request.capability_hints
        for matching_hints, family in self._HINT_FAMILIES:
            if hints.intersection(matching_hints):
                return (
                    self._by_family.get(family, self._by_family[TaskFamily.UNKNOWN]),
                    TaskPolicySelectionReason.CAPABILITY_HINT,
                )
        return (
            self._by_family[TaskFamily.UNKNOWN],
            TaskPolicySelectionReason.CONSERVATIVE_DEFAULT,
        )


class RequestFingerprint:
    """Keyed canonical request fingerprints for duplicate detection.

    The HMAC prevents a durable duplicate index from becoming an offline
    dictionary over private connector arguments. Volatile tracing and
    idempotency fields are excluded before hashing, while meaningful cursors
    remain, so changing a pagination cursor is not a duplicate.
    """

    _IGNORED_FIELDS = frozenset({"idempotency_key", "request_id", "trace_id"})

    def __init__(self, *, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("request fingerprint key must contain at least 32 bytes")
        self._key = bytes(key)

    def for_request(
        self,
        *,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> str:
        if not capability_id.strip():
            raise ValueError("capability_id must be non-empty")
        payload = {
            "capability_id": capability_id,
            "arguments": self._scrub(arguments),
        }
        return hmac.new(
            self._key, canonical_json_bytes(payload), hashlib.sha256
        ).hexdigest()

    @classmethod
    def _scrub(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {
                str(key): cls._scrub(item)
                for key, item in value.items()
                if str(key) not in cls._IGNORED_FIELDS
            }
        if isinstance(value, tuple):
            return [cls._scrub(item) for item in value]
        if isinstance(value, list):
            return [cls._scrub(item) for item in value]
        return value


class ToolUseController:
    """Per-run exact duplicate/error controller with bounded memory.

    This is intentionally a reducer rather than middleware: the existing
    guarded-tool wrapper remains the hard enforcement point. The wrapper can
    consume ``before_operation`` in a later integration slice without changing
    its already-correct budget accounting.
    """

    def __init__(self, *, profile: TaskPolicyProfile) -> None:
        self.profile = profile
        self._intents_by_operation: dict[str, ToolUseIntent] = {}
        self._first_operation_by_fingerprint: dict[str, str] = {}
        self._calls_by_capability: dict[str, int] = {}
        self._error_fingerprint_to_operation: dict[tuple[str, str, str], str] = {}
        self._evidence_refs: set[str] = set()

    def before_operation(self, intent: ToolUseIntent) -> ToolUseFeedback:
        """Return deterministic feedback without recording a completed action."""

        previous = self._intents_by_operation.get(intent.operation_id)
        if previous is not None:
            if previous != intent:
                raise ValueError("operation_id cannot be reused for a different intent")
            return self._feedback_for_existing(intent)

        duplicate_of = self._first_operation_by_fingerprint.get(
            intent.canonical_request_fingerprint
        )
        current_calls = self._calls_by_capability.get(intent.capability_id, 0)
        limit = self.profile.call_limit_for(intent.capability_id)
        remaining = None if limit is None else max(limit - current_calls, 0)

        if duplicate_of is not None:
            return ToolUseFeedback(
                disposition=(
                    ToolUseDisposition.STOP
                    if self.profile.enforce_exact_duplicates
                    else ToolUseDisposition.REPLAN
                ),
                reason_code="exact_duplicate",
                budget_remaining=remaining,
                duplicate_of_operation_id=duplicate_of,
            )
        if limit is not None and current_calls >= limit:
            return ToolUseFeedback(
                disposition=ToolUseDisposition.STOP,
                reason_code="profile_tool_call_limit",
                budget_remaining=0,
            )
        self._record_intent(intent, current_calls=current_calls)
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="admitted",
            budget_remaining=None if remaining is None else max(remaining - 1, 0),
        )

    def after_operation(self, outcome: ToolOperationOutcome) -> ToolUseFeedback:
        """Fold observable outcome facts into future feedback.

        A failed retryable operation is never stopped by this reducer. A
        repeated non-retryable error for the same canonical request is stopped
        if an integration explicitly admits a retry; normal duplicate handling
        returns replan/stop before dispatch, which is the cheaper path.
        """

        intent = self._intents_by_operation.get(outcome.operation_id)
        if intent is None:
            raise ValueError("outcome must reference an admitted operation")
        if intent.capability_id != outcome.capability_id:
            raise ValueError("outcome capability_id does not match its intent")

        new_evidence = 0
        for ref in outcome.evidence_refs:
            if len(self._evidence_refs) >= self.profile.max_source_history:
                break
            if ref not in self._evidence_refs:
                self._evidence_refs.add(ref)
                new_evidence += 1
        remaining = self._remaining(outcome.capability_id)

        if not outcome.succeeded:
            assert outcome.error_class is not None
            error_key = (
                outcome.capability_id,
                intent.canonical_request_fingerprint,
                outcome.error_class,
            )
            prior = self._error_fingerprint_to_operation.get(error_key)
            self._error_fingerprint_to_operation.setdefault(
                error_key, outcome.operation_id
            )
            if (
                prior is not None
                and self.profile.enforce_unchanged_errors
                and not outcome.retryable
            ):
                return ToolUseFeedback(
                    disposition=ToolUseDisposition.STOP,
                    reason_code="same_error_without_changed_input",
                    budget_remaining=remaining,
                    duplicate_of_operation_id=prior,
                )
            return ToolUseFeedback(
                disposition=ToolUseDisposition.REPLAN,
                reason_code="operation_failed_retryable"
                if outcome.retryable
                else "operation_failed",
                budget_remaining=remaining,
            )

        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="new_evidence" if new_evidence else "operation_completed",
            budget_remaining=remaining,
            new_evidence_count=new_evidence,
        )

    def _feedback_for_existing(self, intent: ToolUseIntent) -> ToolUseFeedback:
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="operation_replayed",
            budget_remaining=self._remaining(intent.capability_id),
        )

    def _record_intent(self, intent: ToolUseIntent, *, current_calls: int) -> None:
        self._intents_by_operation[intent.operation_id] = intent
        self._first_operation_by_fingerprint.setdefault(
            intent.canonical_request_fingerprint, intent.operation_id
        )
        self._calls_by_capability[intent.capability_id] = current_calls + 1

    def _remaining(self, capability_id: str) -> int | None:
        limit = self.profile.call_limit_for(capability_id)
        if limit is None:
            return None
        return max(limit - self._calls_by_capability.get(capability_id, 0), 0)


__all__ = (
    "PlanningRequirement",
    "RequestFingerprint",
    "RunToolPlan",
    "SuccessEvidenceRequirement",
    "TaskFamily",
    "TaskPolicyProfile",
    "TaskPolicyRequest",
    "TaskPolicyResolver",
    "TaskPolicySelection",
    "TaskPolicySelectionReason",
    "ToolPlanCreator",
    "ToolPlanStatus",
    "ToolPlanStep",
    "ToolPlanStepStatus",
    "ToolOperationOutcome",
    "ToolPolicyRejected",
    "ToolUseController",
    "ToolUseDisposition",
    "ToolUseFeedback",
    "ToolUseIntent",
)
