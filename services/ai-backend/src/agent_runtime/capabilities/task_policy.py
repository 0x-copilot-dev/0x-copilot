"""Deterministic, privacy-preserving F4 task policy primitives.

This module deliberately does *not* choose capabilities or authorize an
operation.  It classifies a run into a closed policy family and gives the
existing operation and budget gates bounded feedback about duplicate work.  A
model can neither select a more permissive profile nor use this controller to
bypass the hard ``ToolBudgetGuard`` / approval boundaries.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import hmac
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_MAX_SEMANTIC_HISTORY = 20
_MAX_SOURCE_HISTORY = 500
Fingerprint = Annotated[str, Field(pattern=_SHA256_PATTERN)]
OpaqueRef = Annotated[str, Field(min_length=1, max_length=256)]


class _FrozenToolLimits(dict[str, int]):
    """Small JSON-serializable immutable mapping for frozen profile contracts."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("task policy tool-call limits are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


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


class TaskPolicyRecordKind(StrEnum):
    """Closed durable reducer inputs; bodies and raw arguments never appear."""

    BUDGET = "budget"
    INTENT = "intent"
    OUTCOME = "outcome"
    MODEL_TURN = "model_turn"
    FEEDBACK = "feedback"
    PROGRESS = "progress"


class TaskPolicyProfile(RuntimeContract):
    """A versioned, bounded profile selected by trusted runtime context."""

    profile_id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=160)
    task_family: TaskFamily
    planning_requirement: PlanningRequirement = PlanningRequirement.OPTIONAL
    model_turn_limit: int | None = Field(default=None, ge=1, le=1_000)
    total_tool_call_limit: int | None = Field(default=None, ge=1, le=10_000)
    tool_call_limits: dict[str, int] = Field(default_factory=dict)
    cost_limit_microusd: int | None = Field(default=None, ge=0)
    wall_time_limit_seconds: int | None = Field(default=None, ge=1, le=604_800)
    checkpoint_interval: int = Field(default=3, ge=1, le=100)
    enforce_exact_duplicates: bool = False
    enforce_unchanged_errors: bool = True
    max_source_history: int = Field(
        default=_MAX_SOURCE_HISTORY, ge=1, le=_MAX_SOURCE_HISTORY
    )
    semantic_history_limit: int = Field(
        default=_MAX_SEMANTIC_HISTORY, ge=1, le=_MAX_SEMANTIC_HISTORY
    )
    low_yield_streak_threshold: int = Field(default=3, ge=1, le=20)
    objective_evidence_threshold: int | None = Field(default=None, ge=1, le=1_000)

    @field_validator("tool_call_limits")
    @classmethod
    def _valid_tool_call_limits(cls, value: dict[str, int]) -> dict[str, int]:
        for tool_name, limit in value.items():
            if not tool_name.strip():
                raise ValueError("tool_call_limits keys must be non-empty")
            if limit < 1:
                raise ValueError("tool_call_limits values must be positive")
        return _FrozenToolLimits(value)

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
            total_tool_call_limit=6,
            tool_call_limits={"*": 3},
            cost_limit_microusd=1_000_000,
            wall_time_limit_seconds=900,
            checkpoint_interval=1,
            enforce_exact_duplicates=True,
            enforce_unchanged_errors=True,
        )


class TaskPolicyBundle(RuntimeContract):
    """Deployment-owned immutable policy set with a self-authenticating body."""

    schema_version: Literal[1] = 1
    bundle_id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=160)
    profiles: tuple[TaskPolicyProfile, ...] = Field(min_length=1, max_length=64)
    bundle_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("profiles")
    @classmethod
    def _profiles_are_closed(
        cls, value: tuple[TaskPolicyProfile, ...]
    ) -> tuple[TaskPolicyProfile, ...]:
        families = tuple(profile.task_family for profile in value)
        if len(families) != len(set(families)):
            raise ValueError("task policy bundle has duplicate task families")
        if TaskFamily.UNKNOWN not in families:
            raise ValueError("task policy bundle requires an unknown profile")
        return value

    @model_validator(mode="after")
    def _body_authenticates(self) -> "TaskPolicyBundle":
        return self.verify()

    def verify(self) -> "TaskPolicyBundle":
        """Recheck the canonical body, including any nested mutable mapping."""

        if any(profile.revision != self.revision for profile in self.profiles):
            raise ValueError("task policy profile revision must match bundle revision")
        if self.bundle_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError("task policy bundle digest does not match canonical body")
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return every deployment-controlled semantic field."""

        return self.model_dump(mode="json", exclude={"bundle_digest"})

    @property
    def bundle_ref(self) -> str:
        """Return a stable reference that authenticates the exact bundle body."""

        self.verify()
        return (
            f"task-policy-bundle://{self.bundle_id}/{self.revision}/"
            f"sha256/{self.bundle_digest}"
        )

    @classmethod
    def create(
        cls,
        *,
        bundle_id: str,
        revision: str,
        profiles: Sequence[TaskPolicyProfile],
    ) -> "TaskPolicyBundle":
        ordered = tuple(sorted(profiles, key=lambda profile: profile.task_family.value))
        payload = {
            "schema_version": 1,
            "bundle_id": bundle_id,
            "revision": revision,
            "profiles": [profile.model_dump(mode="json") for profile in ordered],
        }
        return cls(**payload, bundle_digest=canonical_json_sha256(payload))

    @classmethod
    def with_conservative_unknown(
        cls,
        *,
        bundle_id: str,
        revision: str,
        profiles: Sequence[TaskPolicyProfile],
    ) -> "TaskPolicyBundle":
        """Create a bundle while deterministically installing the safe fallback."""

        by_family = {profile.task_family: profile for profile in profiles}
        if len(by_family) != len(profiles):
            raise ValueError("task policy bundle has duplicate task families")
        by_family.setdefault(
            TaskFamily.UNKNOWN,
            TaskPolicyProfile.conservative_unknown(revision=revision),
        )
        return cls.create(
            bundle_id=bundle_id,
            revision=revision,
            profiles=tuple(by_family.values()),
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
    verified_signal_revision: str | None = Field(default=None, max_length=160)

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
    bundle_ref: str = Field(min_length=1, max_length=512)
    selection_digest: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _selection_authenticates(self) -> "TaskPolicySelection":
        if self.selection_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError(
                "task policy selection digest does not match canonical body"
            )
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"selection_digest"})

    @property
    def selection_ref(self) -> str:
        return (
            f"task-policy-selection://{self.run_id}/{self.profile_id}/"
            f"sha256/{self.selection_digest}"
        )

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        profile: TaskPolicyProfile,
        reason: TaskPolicySelectionReason,
        bundle_ref: str,
    ) -> "TaskPolicySelection":
        payload = {
            "run_id": run_id,
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
            "task_family": profile.task_family,
            "planning_requirement": profile.planning_requirement,
            "selection_reason": reason,
            "bundle_ref": bundle_ref,
        }
        digest_payload = cls.model_construct(
            **payload,
            selection_digest="0" * 64,
        ).digest_payload()
        return cls(
            **payload,
            selection_digest=canonical_json_sha256(digest_payload),
        )


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


@dataclass(frozen=True)
class _PlanTemplate:
    objective: str
    steps: tuple[tuple[str, str, tuple[str, ...]], ...]
    success_evidence: tuple[tuple[str, int, str], ...]


class RunToolPlanFactory:
    """Build deterministic, public plans without consuming private reasoning."""

    _TEMPLATES: Mapping[TaskFamily, _PlanTemplate] = {
        TaskFamily.PUBLIC_RESEARCH: _PlanTemplate(
            objective="Gather distinct sources and verify the requested facts.",
            steps=(
                ("discover", "Find distinct relevant sources.", ("source",)),
                ("verify", "Cross-check the material claims.", ("source", "claim")),
            ),
            success_evidence=(
                ("source", 2, "Distinct sources support the material claims."),
            ),
        ),
        TaskFamily.CONNECTED_RECORD_LOOKUP: _PlanTemplate(
            objective="Locate the requested record and verify its current fields.",
            steps=(
                ("lookup", "Locate the requested record.", ("record",)),
                ("verify", "Verify the relevant fields.", ("record_field",)),
            ),
            success_evidence=(("record", 1, "The requested record was located."),),
        ),
        TaskFamily.LIBRARY_GROUNDING: _PlanTemplate(
            objective="Locate retained library evidence and ground the response.",
            steps=(
                ("search", "Find relevant library evidence.", ("library_source",)),
                ("ground", "Check the requested claims against it.", ("claim",)),
            ),
            success_evidence=(
                (
                    "library_source",
                    1,
                    "Retained library evidence supports the response.",
                ),
            ),
        ),
        TaskFamily.WORKSPACE_ANALYSIS: _PlanTemplate(
            objective="Inspect the scoped workspace and report supported findings.",
            steps=(
                ("inspect", "Inspect the relevant workspace material.", ("source",)),
                ("analyze", "Validate the requested findings.", ("finding",)),
            ),
            success_evidence=(
                ("source", 1, "Workspace evidence supports the findings."),
            ),
        ),
        TaskFamily.TRANSFORMATION: _PlanTemplate(
            objective="Apply the requested transformation and verify the result.",
            steps=(("transform", "Transform and validate the input.", ("result",)),),
            success_evidence=(("result", 1, "The transformed result was validated."),),
        ),
        TaskFamily.ARTIFACT_DRAFTING: _PlanTemplate(
            objective="Draft the requested artifact and verify its required sections.",
            steps=(
                ("draft", "Create the requested artifact.", ("artifact",)),
                ("review", "Check the required sections.", ("validation",)),
            ),
            success_evidence=(("artifact", 1, "The requested artifact was produced."),),
        ),
        TaskFamily.EFFECT_PROPOSAL: _PlanTemplate(
            objective="Prepare a reviewable proposal without applying the effect.",
            steps=(
                ("inspect", "Inspect the current state.", ("precondition",)),
                ("propose", "Prepare the exact reviewable proposal.", ("proposal",)),
            ),
            success_evidence=(("proposal", 1, "A reviewable proposal was prepared."),),
        ),
        TaskFamily.CODE_DIAGNOSIS: _PlanTemplate(
            objective="Reproduce the failure, identify its cause, and verify the diagnosis.",
            steps=(
                ("reproduce", "Reproduce the reported failure.", ("test_failure",)),
                ("inspect", "Inspect the relevant implementation.", ("source",)),
                (
                    "verify",
                    "Verify the diagnosis with a focused check.",
                    ("test_result",),
                ),
            ),
            success_evidence=(
                ("test_result", 1, "A focused check verifies the diagnosis."),
            ),
        ),
        TaskFamily.DELEGATED_ANALYSIS: _PlanTemplate(
            objective="Collect bounded delegated findings and verify their coverage.",
            steps=(
                ("delegate", "Collect bounded independent findings.", ("finding",)),
                (
                    "synthesize",
                    "Verify coverage and synthesize the findings.",
                    ("claim",),
                ),
            ),
            success_evidence=(
                ("finding", 1, "Delegated findings cover the objective."),
            ),
        ),
        TaskFamily.UNKNOWN: _PlanTemplate(
            objective="Clarify the objective, gather bounded evidence, and report limits.",
            steps=(
                ("clarify", "Establish the bounded task objective.", ("objective",)),
                ("gather", "Gather only the evidence needed.", ("evidence",)),
                (
                    "verify",
                    "Check the result and report remaining uncertainty.",
                    ("claim",),
                ),
            ),
            success_evidence=(
                ("evidence", 1, "Evidence supports the bounded result."),
            ),
        ),
    }

    @classmethod
    def create_for_selection(
        cls,
        selection: TaskPolicySelection,
        *,
        created_by: ToolPlanCreator = ToolPlanCreator.DETERMINISTIC,
    ) -> RunToolPlan | None:
        """Return the stable family plan, or ``None`` when policy skips planning."""

        if selection.planning_requirement is PlanningRequirement.NONE:
            return None
        template = cls._TEMPLATES[selection.task_family]
        plan_digest = canonical_json_sha256(
            {
                "run_id": selection.run_id,
                "selection_ref": selection.selection_ref,
                "template_family": selection.task_family.value,
            }
        )
        return RunToolPlan.for_selection(
            selection=selection,
            plan_id=f"plan-{plan_digest[:32]}",
            objective=template.objective,
            steps=tuple(
                ToolPlanStep(
                    step_id=step_id,
                    label=label,
                    expected_evidence_kinds=evidence_kinds,
                )
                for step_id, label, evidence_kinds in template.steps
            ),
            success_evidence=tuple(
                SuccessEvidenceRequirement(
                    evidence_kind=evidence_kind,
                    minimum_count=minimum_count,
                    description=description,
                )
                for evidence_kind, minimum_count, description in template.success_evidence
            ),
            created_by=created_by,
        )


class ToolUseIntent(RuntimeContract):
    """Public action intent. The fingerprint is keyed; raw arguments never persist."""

    schema_version: Literal[1] = 1
    record_kind: Literal[TaskPolicyRecordKind.INTENT] = TaskPolicyRecordKind.INTENT
    operation_id: str = Field(min_length=1, max_length=160)
    capability_id: str = Field(min_length=1, max_length=240)
    canonical_request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_step_id: str | None = Field(default=None, max_length=160)
    objective: str | None = Field(default=None, max_length=512)
    expected_evidence_kind: str | None = Field(default=None, max_length=80)
    semantic_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    objective_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @property
    def intent_digest(self) -> str:
        """Content-free idempotency digest for durable operation identity."""

        return canonical_json_sha256(self.model_dump(mode="json"))


class ToolUseFeedback(RuntimeContract):
    """Bounded, content-free feedback returned around a governed operation."""

    disposition: ToolUseDisposition
    reason_code: str = Field(min_length=1, max_length=120)
    budget_remaining: int | None = Field(default=None, ge=0)
    duplicate_of_operation_id: str | None = Field(default=None, max_length=160)
    new_evidence_count: int = Field(default=0, ge=0)

    @property
    def feedback_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class ToolPolicyRejected(ToolBudgetRejected):
    """A non-fatal policy refusal surfaced to the model as a tool result.

    It is deliberately a ``ToolBudgetRejected`` subclass so the existing tool
    error policy keeps the run alive after the controller prevented duplicate
    dispatch. The model can revise its plan and answer with evidence already
    gathered; a refusal must not discard earlier useful work.
    """


class ToolOperationOutcome(RuntimeContract):
    """Observable outcome data only; result bodies and arguments stay protected."""

    schema_version: Literal[1] = 1
    record_kind: Literal[TaskPolicyRecordKind.OUTCOME] = TaskPolicyRecordKind.OUTCOME
    operation_id: str = Field(min_length=1, max_length=160)
    capability_id: str = Field(min_length=1, max_length=240)
    succeeded: bool
    error_class: str | None = Field(default=None, max_length=120)
    retryable: bool = False
    evidence_refs: tuple[OpaqueRef, ...] = Field(default_factory=tuple, max_length=500)
    source_fingerprints: tuple[Fingerprint, ...] = Field(
        default_factory=tuple, max_length=500
    )
    result_fingerprint: Fingerprint | None = None
    evidence_fingerprint: Fingerprint | None = None
    error_fingerprint: Fingerprint | None = None
    cost_microusd: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _error_is_present_for_failure(self) -> "ToolOperationOutcome":
        if not self.succeeded and not self.error_class:
            raise ValueError("failed outcome requires error_class")
        if self.succeeded and self.error_fingerprint is not None:
            raise ValueError("successful outcome cannot have an error_fingerprint")
        if len(set(self.source_fingerprints)) != len(self.source_fingerprints):
            raise ValueError("source_fingerprints must be unique")
        return self

    @property
    def outcome_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


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
        profiles: Sequence[TaskPolicyProfile] | None = None,
        *,
        policy_revision: str | None = None,
        bundle: TaskPolicyBundle | None = None,
    ) -> None:
        if bundle is not None:
            if profiles:
                raise ValueError("provide either bundle or profiles, not both")
            if policy_revision is not None and policy_revision != bundle.revision:
                raise ValueError("policy_revision does not match task policy bundle")
            bundle.verify()
            profiles = tuple(
                TaskPolicyProfile.model_validate(profile.model_dump(mode="python"))
                for profile in bundle.profiles
            )
            policy_revision = bundle.revision
            self._bundle = bundle
        else:
            profiles = tuple(profiles or ())
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
        if bundle is None:
            self._bundle = TaskPolicyBundle.create(
                bundle_id="inline",
                revision=policy_revision,
                profiles=tuple(by_family.values()),
            )
        self._policy_revision = policy_revision
        self._by_family = by_family

    def resolve(self, request: TaskPolicyRequest) -> TaskPolicyProfile:
        """Return the selected profile after enforcing revision affinity."""

        profile, _ = self._resolve_with_reason(request)
        return profile.model_copy()

    def resolve_selection(self, request: TaskPolicyRequest) -> TaskPolicySelection:
        """Return the immutable, persistable run/profile binding."""

        profile, reason = self._resolve_with_reason(request)
        return TaskPolicySelection.create(
            run_id=request.run_id,
            profile=profile,
            reason=reason,
            bundle_ref=self._bundle.bundle_ref,
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

    _IGNORED_FIELDS = frozenset(
        {"idempotency_key", "request_id", "trace_id", "span_id"}
    )
    _ORDER_INSENSITIVE_FIELDS = frozenset({"fields", "include", "expand"})

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
            "kind": "request",
            "capability_id": capability_id,
            "arguments": self._scrub(arguments),
        }
        return self._digest(payload)

    def for_result(
        self,
        *,
        capability_id: str,
        result_metadata: Mapping[str, Any],
    ) -> str:
        """Return a keyed digest for protected result metadata."""

        if not capability_id.strip():
            raise ValueError("capability_id must be non-empty")
        return self._digest(
            {
                "kind": "result",
                "capability_id": capability_id,
                "result": self._scrub(result_metadata),
            }
        )

    def for_error(
        self,
        *,
        capability_id: str,
        request_fingerprint: str,
        error_class: str,
        retryable: bool,
        retry_hint: str | None = None,
    ) -> str:
        """Return a keyed digest for a normalized retry-relevant failure."""

        if not error_class.strip():
            raise ValueError("error_class must be non-empty")
        if len(request_fingerprint) != 64:
            raise ValueError("request_fingerprint must be a sha256 hex digest")
        return self._digest(
            {
                "kind": "error",
                "capability_id": capability_id,
                "request_fingerprint": request_fingerprint,
                "error_class": error_class.strip().lower(),
                "retryable": retryable,
                "retry_hint": retry_hint,
            }
        )

    @classmethod
    def _scrub(cls, value: object, *, parent_key: str | None = None) -> object:
        if isinstance(value, Mapping):
            return {
                str(key): cls._scrub(item, parent_key=str(key))
                for key, item in value.items()
                if str(key) not in cls._IGNORED_FIELDS
            }
        if isinstance(value, tuple):
            scrubbed = [cls._scrub(item) for item in value]
            return (
                sorted(scrubbed, key=canonical_json_bytes)
                if parent_key in cls._ORDER_INSENSITIVE_FIELDS
                else scrubbed
            )
        if isinstance(value, list):
            scrubbed = [cls._scrub(item) for item in value]
            return (
                sorted(scrubbed, key=canonical_json_bytes)
                if parent_key in cls._ORDER_INSENSITIVE_FIELDS
                else scrubbed
            )
        return value

    def _digest(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._key, canonical_json_bytes(dict(payload)), hashlib.sha256
        ).hexdigest()


class ResultFingerprint(RequestFingerprint):
    """Keyed digest of protected result metadata, never the durable raw body."""

    def for_result(
        self,
        *,
        capability_id: str,
        result_metadata: Mapping[str, Any],
    ) -> str:
        if not capability_id.strip():
            raise ValueError("capability_id must be non-empty")
        return self._digest(
            {
                "kind": "result",
                "capability_id": capability_id,
                "result": self._scrub(result_metadata),
            }
        )


class EvidenceFingerprint(RequestFingerprint):
    """Keyed digest for one canonical source/evidence identity."""

    def for_evidence(
        self,
        *,
        source_kind: str,
        source_identity: Mapping[str, Any],
    ) -> str:
        if not source_kind.strip():
            raise ValueError("source_kind must be non-empty")
        return self._digest(
            {
                "kind": "evidence",
                "source_kind": source_kind,
                "source_identity": self._scrub(source_identity),
            }
        )


class ErrorFingerprint(RequestFingerprint):
    """Keyed digest for a normalized error and its retry-relevant hints."""

    def for_error(
        self,
        *,
        capability_id: str,
        request_fingerprint: str,
        error_class: str,
        retryable: bool,
        retry_hint: str | None = None,
    ) -> str:
        if not error_class.strip():
            raise ValueError("error_class must be non-empty")
        if len(request_fingerprint) != 64:
            raise ValueError("request_fingerprint must be a sha256 hex digest")
        return self._digest(
            {
                "kind": "error",
                "capability_id": capability_id,
                "request_fingerprint": request_fingerprint,
                "error_class": error_class.strip().lower(),
                "retryable": retryable,
                "retry_hint": retry_hint,
            }
        )


class TaskPolicyBudgetRecord(RuntimeContract):
    """Durable effective ceilings after all authoritative limits are intersected."""

    schema_version: Literal[1] = 1
    record_kind: Literal[TaskPolicyRecordKind.BUDGET] = TaskPolicyRecordKind.BUDGET
    budget_id: str = Field(min_length=1, max_length=160)
    model_turn_limit: int | None = Field(default=None, ge=1, le=1_000)
    total_tool_call_limit: int | None = Field(default=None, ge=1, le=10_000)
    cost_limit_microusd: int | None = Field(default=None, ge=0)
    deadline_at: datetime | None = None

    @field_validator("deadline_at")
    @classmethod
    def _deadline_is_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware(value, "deadline_at")

    @property
    def record_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class ModelTurnRecord(RuntimeContract):
    """Content-free durable usage input for one model turn."""

    schema_version: Literal[1] = 1
    record_kind: Literal[TaskPolicyRecordKind.MODEL_TURN] = (
        TaskPolicyRecordKind.MODEL_TURN
    )
    turn_id: str = Field(min_length=1, max_length=160)
    cost_microusd: int = Field(default=0, ge=0)

    @property
    def record_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class ToolUseFeedbackRecord(RuntimeContract):
    """Idempotent durable projection of one controller decision."""

    schema_version: Literal[1] = 1
    record_kind: Literal[TaskPolicyRecordKind.FEEDBACK] = TaskPolicyRecordKind.FEEDBACK
    decision_id: str = Field(min_length=1, max_length=160)
    operation_id: str | None = Field(default=None, max_length=160)
    feedback: ToolUseFeedback

    @property
    def record_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


class ToolPlanProgressRecord(RuntimeContract):
    """Bounded public progress input; no plan rationale or result body is stored."""

    schema_version: Literal[1] = 1
    record_kind: Literal[TaskPolicyRecordKind.PROGRESS] = TaskPolicyRecordKind.PROGRESS
    progress_id: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=160)
    active_step_id: str | None = Field(default=None, max_length=160)
    completed_step_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    evidence_count: int = Field(default=0, ge=0, le=1_000_000)
    objective_satisfied: bool = False

    @field_validator("completed_step_ids")
    @classmethod
    def _completed_steps_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("completed_step_ids must be unique")
        if any(not step_id or len(step_id) > 160 for step_id in value):
            raise ValueError("completed_step_ids contain an invalid step id")
        return value

    @model_validator(mode="after")
    def _active_step_is_not_completed(self) -> "ToolPlanProgressRecord":
        if self.active_step_id in self.completed_step_ids:
            raise ValueError("active_step_id cannot already be completed")
        return self

    @property
    def record_digest(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json"))


TaskPolicyDurableRecord = (
    TaskPolicyBudgetRecord
    | ToolUseIntent
    | ToolOperationOutcome
    | ModelTurnRecord
    | ToolUseFeedbackRecord
    | ToolPlanProgressRecord
)


@dataclass(frozen=True)
class ToolControllerState:
    """Immutable content-free snapshot derived solely from durable records."""

    model_turns: int
    tool_calls: int
    cost_microusd: int
    deadline_at: datetime | None
    calls_by_capability: tuple[tuple[str, int], ...]
    source_fingerprint_count: int
    semantic_history_count: int
    evidence_count: int
    low_yield_streak: int
    objective_satisfied: bool

    def calls_for(self, capability_id: str) -> int:
        return dict(self.calls_by_capability).get(capability_id, 0)


class TaskPolicyReducer:
    """Pure replay entry point for building exactly the same controller state."""

    @staticmethod
    def reduce(
        *,
        profile: TaskPolicyProfile,
        records: Iterable[TaskPolicyDurableRecord],
        deadline_at: datetime | None = None,
        started_at: datetime | None = None,
    ) -> ToolControllerState:
        controller = ToolUseController(
            profile=profile,
            deadline_at=deadline_at,
            started_at=started_at,
        )
        controller.replay(records)
        return controller.state


class ToolUseController:
    """Restart-safe exact controller over immutable durable reducer inputs."""

    def __init__(
        self,
        *,
        profile: TaskPolicyProfile,
        records: Iterable[TaskPolicyDurableRecord] = (),
        deadline_at: datetime | None = None,
        started_at: datetime | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.profile = profile
        self._clock = clock
        self._deadline_at = self._effective_deadline(
            deadline_at=deadline_at,
            started_at=started_at,
        )
        self._intents_by_operation: dict[str, ToolUseIntent] = {}
        self._outcomes_by_operation: dict[str, ToolOperationOutcome] = {}
        self._outcome_feedback: dict[str, ToolUseFeedback] = {}
        self._first_operation_by_fingerprint: dict[str, str] = {}
        self._latest_operation_by_fingerprint: dict[str, str] = {}
        self._calls_by_capability: dict[str, int] = {}
        self._error_fingerprint_to_operation: dict[str, str] = {}
        self._source_fingerprints: dict[str, None] = {}
        self._evidence_fingerprints: set[str] = set()
        self._semantic_history: deque[tuple[str, str]] = deque(
            maxlen=profile.semantic_history_limit
        )
        self._model_turns: dict[str, ModelTurnRecord] = {}
        self._budget_record: TaskPolicyBudgetRecord | None = None
        self._feedback_records: dict[str, ToolUseFeedbackRecord] = {}
        self._progress_records: dict[str, ToolPlanProgressRecord] = {}
        self._cost_microusd = 0
        self._low_yield_streak = 0
        self._objective_satisfied = False
        self.replay(records)

    @classmethod
    def rebuild(
        cls,
        *,
        profile: TaskPolicyProfile,
        records: Iterable[TaskPolicyDurableRecord],
        deadline_at: datetime | None = None,
        started_at: datetime | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> "ToolUseController":
        """Rehydrate from canonical records without resetting any limit."""

        return cls(
            profile=profile,
            records=records,
            deadline_at=deadline_at,
            started_at=started_at,
            clock=clock,
        )

    @property
    def state(self) -> ToolControllerState:
        return ToolControllerState(
            model_turns=len(self._model_turns),
            tool_calls=len(self._intents_by_operation),
            cost_microusd=self._cost_microusd,
            deadline_at=self._deadline_at,
            calls_by_capability=tuple(sorted(self._calls_by_capability.items())),
            source_fingerprint_count=len(self._source_fingerprints),
            semantic_history_count=len(self._semantic_history),
            evidence_count=len(
                self._evidence_fingerprints.union(self._source_fingerprints)
            ),
            low_yield_streak=self._low_yield_streak,
            objective_satisfied=self._objective_satisfied,
        )

    def replay(self, records: Iterable[TaskPolicyDurableRecord]) -> None:
        """Fold persisted records in journal order with conflict detection."""

        for record in records:
            if isinstance(record, TaskPolicyBudgetRecord):
                self._apply_budget(record)
            elif isinstance(record, ToolUseIntent):
                self._apply_intent(record)
            elif isinstance(record, ToolOperationOutcome):
                self._apply_outcome(record, replaying=True)
            elif isinstance(record, ModelTurnRecord):
                self._apply_model_turn(record)
            elif isinstance(record, ToolUseFeedbackRecord):
                previous = self._feedback_records.get(record.decision_id)
                if previous is not None and previous != record:
                    raise ValueError("decision_id conflicts with durable feedback")
                self._feedback_records.setdefault(record.decision_id, record)
            elif isinstance(record, ToolPlanProgressRecord):
                previous = self._progress_records.get(record.progress_id)
                if previous is not None and previous != record:
                    raise ValueError("progress_id conflicts with durable progress")
                self._progress_records.setdefault(record.progress_id, record)
                self._objective_satisfied = (
                    self._objective_satisfied or record.objective_satisfied
                )
            else:  # pragma: no cover - union callers are statically closed.
                raise TypeError("unsupported task-policy durable record")

    def record_model_turn(self, record: ModelTurnRecord) -> ToolUseFeedback:
        """Idempotently admit one model turn against turn/cost/deadline limits."""

        previous = self._model_turns.get(record.turn_id)
        if previous is not None:
            if previous != record:
                raise ValueError("turn_id cannot be reused for different usage")
            return ToolUseFeedback(
                disposition=ToolUseDisposition.CONTINUE,
                reason_code="model_turn_replayed",
            )
        blocked = self._hard_limit_feedback(
            additional_cost_microusd=record.cost_microusd,
            additional_model_turns=1,
        )
        if blocked is not None:
            return blocked
        self._apply_model_turn(record)
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="model_turn_recorded",
        )

    def observe_model_turn(self, record: ModelTurnRecord) -> None:
        """Account for a shadow-admitted turn after preserving its policy decision."""

        self._apply_model_turn(record)

    def bind_budget(self, record: TaskPolicyBudgetRecord) -> None:
        """Bind the one durable effective budget snapshot idempotently."""

        self._apply_budget(record)

    def before_operation(self, intent: ToolUseIntent) -> ToolUseFeedback:
        """Admit once, with O(1) exact lookup and advisory semantic feedback."""

        previous = self._intents_by_operation.get(intent.operation_id)
        if previous is not None:
            if previous != intent:
                raise ValueError("operation_id cannot be reused for a different intent")
            return self._feedback_for_existing(intent)

        blocked = self._hard_limit_feedback(additional_tool_calls=1)
        if blocked is not None:
            return blocked

        duplicate_of = self._latest_operation_by_fingerprint.get(
            intent.canonical_request_fingerprint
        )
        if duplicate_of is not None:
            prior_outcome = self._outcomes_by_operation.get(duplicate_of)
            if prior_outcome is None:
                return self._duplicate_feedback(intent, duplicate_of)
            if not prior_outcome.succeeded and prior_outcome.retryable:
                duplicate_of = None
            elif not prior_outcome.succeeded and self.profile.enforce_unchanged_errors:
                return ToolUseFeedback(
                    disposition=ToolUseDisposition.STOP,
                    reason_code="same_error_without_changed_input",
                    budget_remaining=self._remaining(intent.capability_id),
                    duplicate_of_operation_id=prior_outcome.operation_id,
                )
            else:
                return self._duplicate_feedback(intent, duplicate_of)

        current_calls = self._calls_by_capability.get(intent.capability_id, 0)
        limit = self.profile.call_limit_for(intent.capability_id)
        if limit is not None and current_calls >= limit:
            return ToolUseFeedback(
                disposition=ToolUseDisposition.STOP,
                reason_code="profile_tool_call_limit",
                budget_remaining=0,
            )

        semantic_overlap = intent.semantic_fingerprint is not None and any(
            fingerprint == intent.semantic_fingerprint
            for _, fingerprint in self._semantic_history
        )
        self._apply_intent(intent)
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="semantic_query_overlap" if semantic_overlap else "admitted",
            budget_remaining=self._remaining(intent.capability_id),
            duplicate_of_operation_id=(
                next(
                    (
                        operation_id
                        for operation_id, fingerprint in self._semantic_history
                        if operation_id != intent.operation_id
                        and fingerprint == intent.semantic_fingerprint
                    ),
                    None,
                )
                if semantic_overlap
                else None
            ),
        )

    def observe_dispatched(self, intent: ToolUseIntent) -> None:
        """Account for a shadow-admitted dispatch without changing its decision."""

        self._apply_intent(intent)

    def after_operation(self, outcome: ToolOperationOutcome) -> ToolUseFeedback:
        """Fold one outcome idempotently and emit only bounded structured advice."""

        previous = self._outcomes_by_operation.get(outcome.operation_id)
        if previous is not None:
            if previous != outcome:
                raise ValueError("operation outcome conflicts with durable outcome")
            return self._outcome_feedback[outcome.operation_id]
        return self._apply_outcome(outcome, replaying=False)

    def _apply_intent(self, intent: ToolUseIntent) -> None:
        previous = self._intents_by_operation.get(intent.operation_id)
        if previous is not None:
            if previous != intent:
                raise ValueError("operation_id conflicts with durable intent")
            return
        self._intents_by_operation[intent.operation_id] = intent
        self._first_operation_by_fingerprint.setdefault(
            intent.canonical_request_fingerprint, intent.operation_id
        )
        self._latest_operation_by_fingerprint[intent.canonical_request_fingerprint] = (
            intent.operation_id
        )
        self._calls_by_capability[intent.capability_id] = (
            self._calls_by_capability.get(intent.capability_id, 0) + 1
        )
        if intent.semantic_fingerprint is not None:
            self._semantic_history.append(
                (intent.operation_id, intent.semantic_fingerprint)
            )

    def _apply_outcome(
        self,
        outcome: ToolOperationOutcome,
        *,
        replaying: bool,
    ) -> ToolUseFeedback:
        intent = self._intents_by_operation.get(outcome.operation_id)
        if intent is None:
            raise ValueError("outcome must reference an admitted operation")
        if intent.capability_id != outcome.capability_id:
            raise ValueError("outcome capability_id does not match its intent")
        previous = self._outcomes_by_operation.get(outcome.operation_id)
        if previous is not None:
            if previous != outcome:
                raise ValueError("operation outcome conflicts with durable outcome")
            return self._outcome_feedback[outcome.operation_id]

        self._outcomes_by_operation[outcome.operation_id] = outcome
        self._cost_microusd += outcome.cost_microusd
        source_fingerprints = outcome.source_fingerprints or outcome.evidence_refs
        new_sources = 0
        for fingerprint in source_fingerprints:
            if len(self._source_fingerprints) >= self.profile.max_source_history:
                break
            if fingerprint not in self._source_fingerprints:
                self._source_fingerprints[fingerprint] = None
                new_sources += 1
        new_evidence = 0
        for fingerprint in (
            (outcome.evidence_fingerprint,) if outcome.evidence_fingerprint else ()
        ):
            if fingerprint not in self._evidence_fingerprints:
                self._evidence_fingerprints.add(fingerprint)
                new_evidence += 1

        remaining = self._remaining(outcome.capability_id)
        if not outcome.succeeded:
            assert outcome.error_class is not None
            error_fingerprint = outcome.error_fingerprint or canonical_json_sha256(
                {
                    "capability_id": outcome.capability_id,
                    "request_fingerprint": intent.canonical_request_fingerprint,
                    "error_class": outcome.error_class.strip().lower(),
                }
            )
            prior = self._error_fingerprint_to_operation.get(error_fingerprint)
            self._error_fingerprint_to_operation.setdefault(
                error_fingerprint, outcome.operation_id
            )
            if (
                prior is not None
                and self.profile.enforce_unchanged_errors
                and not outcome.retryable
            ):
                feedback = ToolUseFeedback(
                    disposition=ToolUseDisposition.STOP,
                    reason_code="same_error_without_changed_input",
                    budget_remaining=remaining,
                    duplicate_of_operation_id=prior,
                )
            else:
                feedback = ToolUseFeedback(
                    disposition=ToolUseDisposition.REPLAN,
                    reason_code="operation_failed_retryable"
                    if outcome.retryable
                    else "operation_failed",
                    budget_remaining=remaining,
                )
        else:
            observed_evidence = bool(
                source_fingerprints or outcome.evidence_fingerprint
            )
            if new_sources == 0 and new_evidence == 0 and observed_evidence:
                self._low_yield_streak += 1
            else:
                self._low_yield_streak = 0
            evidence_total = len(self._evidence_fingerprints) + len(
                self._source_fingerprints
            )
            if (
                self.profile.objective_evidence_threshold is not None
                and evidence_total >= self.profile.objective_evidence_threshold
            ):
                self._objective_satisfied = True
                reason = "objective_satisfied"
            elif self._low_yield_streak >= self.profile.low_yield_streak_threshold:
                reason = "same_sources_no_new_evidence"
            elif new_sources or new_evidence:
                reason = "new_evidence"
            else:
                reason = "operation_completed"
            feedback = ToolUseFeedback(
                disposition=ToolUseDisposition.CONTINUE,
                reason_code=reason,
                budget_remaining=remaining,
                new_evidence_count=max(new_sources, new_evidence),
            )
        self._outcome_feedback[outcome.operation_id] = feedback
        del replaying
        return feedback

    def _apply_model_turn(self, record: ModelTurnRecord) -> None:
        previous = self._model_turns.get(record.turn_id)
        if previous is not None:
            if previous != record:
                raise ValueError("turn_id conflicts with durable model usage")
            return
        self._model_turns[record.turn_id] = record
        self._cost_microusd += record.cost_microusd

    def _apply_budget(self, record: TaskPolicyBudgetRecord) -> None:
        previous = self._budget_record
        if previous is not None:
            if previous != record:
                raise ValueError("task policy controller budget conflicts")
            return
        self._budget_record = record
        if record.deadline_at is not None:
            self._deadline_at = (
                record.deadline_at
                if self._deadline_at is None
                else min(self._deadline_at, record.deadline_at)
            )

    def _feedback_for_existing(self, intent: ToolUseIntent) -> ToolUseFeedback:
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="operation_replayed",
            budget_remaining=self._remaining(intent.capability_id),
        )

    def _duplicate_feedback(
        self, intent: ToolUseIntent, duplicate_of: str
    ) -> ToolUseFeedback:
        return ToolUseFeedback(
            disposition=(
                ToolUseDisposition.STOP
                if self.profile.enforce_exact_duplicates
                else ToolUseDisposition.REPLAN
            ),
            reason_code="exact_duplicate",
            budget_remaining=self._remaining(intent.capability_id),
            duplicate_of_operation_id=duplicate_of,
        )

    def _hard_limit_feedback(
        self,
        *,
        additional_model_turns: int = 0,
        additional_tool_calls: int = 0,
        additional_cost_microusd: int = 0,
    ) -> ToolUseFeedback | None:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("task policy clock must return a timezone-aware datetime")
        if self._deadline_at is not None and now >= self._deadline_at:
            return ToolUseFeedback(
                disposition=ToolUseDisposition.STOP,
                reason_code="profile_deadline_exhausted",
            )
        model_turn_limit = self._minimum_limit(
            self.profile.model_turn_limit,
            self._budget_record.model_turn_limit
            if self._budget_record is not None
            else None,
        )
        if (
            model_turn_limit is not None
            and len(self._model_turns) + additional_model_turns > model_turn_limit
        ):
            return ToolUseFeedback(
                disposition=ToolUseDisposition.STOP,
                reason_code="profile_model_turn_limit",
            )
        total_tool_call_limit = self._minimum_limit(
            self.profile.total_tool_call_limit,
            self._budget_record.total_tool_call_limit
            if self._budget_record is not None
            else None,
        )
        if (
            total_tool_call_limit is not None
            and len(self._intents_by_operation) + additional_tool_calls
            > total_tool_call_limit
        ):
            return ToolUseFeedback(
                disposition=ToolUseDisposition.STOP,
                reason_code="profile_total_tool_call_limit",
                budget_remaining=0,
            )
        cost_limit_microusd = self._minimum_limit(
            self.profile.cost_limit_microusd,
            self._budget_record.cost_limit_microusd
            if self._budget_record is not None
            else None,
        )
        if (
            cost_limit_microusd is not None
            and self._cost_microusd + additional_cost_microusd > cost_limit_microusd
        ):
            return ToolUseFeedback(
                disposition=ToolUseDisposition.STOP,
                reason_code="profile_cost_limit",
            )
        return None

    def _remaining(self, capability_id: str) -> int | None:
        limits: list[int] = []
        capability_limit = self.profile.call_limit_for(capability_id)
        if capability_limit is not None:
            limits.append(
                max(
                    capability_limit - self._calls_by_capability.get(capability_id, 0),
                    0,
                )
            )
        total_tool_call_limit = self._minimum_limit(
            self.profile.total_tool_call_limit,
            self._budget_record.total_tool_call_limit
            if self._budget_record is not None
            else None,
        )
        if total_tool_call_limit is not None:
            limits.append(
                max(
                    total_tool_call_limit - len(self._intents_by_operation),
                    0,
                )
            )
        return min(limits) if limits else None

    def _effective_deadline(
        self,
        *,
        deadline_at: datetime | None,
        started_at: datetime | None,
    ) -> datetime | None:
        if deadline_at is not None:
            deadline_at = _aware(deadline_at, "deadline_at")
        if started_at is not None:
            started_at = _aware(started_at, "started_at")
        profile_deadline = (
            started_at + timedelta(seconds=self.profile.wall_time_limit_seconds)
            if started_at is not None
            and self.profile.wall_time_limit_seconds is not None
            else None
        )
        if deadline_at is None:
            return profile_deadline
        if profile_deadline is None:
            return deadline_at
        return min(deadline_at, profile_deadline)

    @staticmethod
    def _minimum_limit(*limits: int | None) -> int | None:
        present = tuple(limit for limit in limits if limit is not None)
        return min(present) if present else None


__all__ = (
    "ErrorFingerprint",
    "EvidenceFingerprint",
    "ModelTurnRecord",
    "PlanningRequirement",
    "RequestFingerprint",
    "ResultFingerprint",
    "RunToolPlan",
    "RunToolPlanFactory",
    "SuccessEvidenceRequirement",
    "TaskFamily",
    "TaskPolicyBundle",
    "TaskPolicyBudgetRecord",
    "TaskPolicyDurableRecord",
    "TaskPolicyProfile",
    "TaskPolicyRecordKind",
    "TaskPolicyReducer",
    "TaskPolicyRequest",
    "TaskPolicyResolver",
    "TaskPolicySelection",
    "TaskPolicySelectionReason",
    "ToolPlanCreator",
    "ToolPlanStatus",
    "ToolPlanStep",
    "ToolPlanStepStatus",
    "ToolOperationOutcome",
    "ToolControllerState",
    "ToolPlanProgressRecord",
    "ToolPolicyRejected",
    "ToolUseController",
    "ToolUseDisposition",
    "ToolUseFeedback",
    "ToolUseFeedbackRecord",
    "ToolUseIntent",
)
