"""Body-free F10 model-invocation and provider-attempt journal contracts.

The journal contains only verified control bindings, opaque identities, canonical
digests, closed policy outcomes, bounded timings, and provider-reported usage. It
must never receive prompt/response bodies, credentials, endpoint URLs, raw provider
request IDs, exception strings, or model-generated explanations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from typing import Annotated, Literal, Protocol, TypeAlias, TypeVar

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.model_invocation.contracts import (
    ModelAttemptDecision,
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelCredentialMode,
    ModelDispatchState,
    ModelFailureClass,
    ModelFallbackPolicy,
    ModelRouteEntry,
    ModelRouteExclusion,
    ModelRouteExclusionReason,
    ModelRoutePlan,
    ModelStreamState,
)
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_ROUTE_DIGEST_PATTERN = r"^sha256:[a-f0-9]{64}$"
_ENDPOINT_REF_PATTERN = r"^endpoint_[a-f0-9]{32}$"
_PROVIDER_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
_RecordT = TypeVar("_RecordT", bound="_ModelInvocationRecord")


class ModelInvocationStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelAttemptLifecycleState(StrEnum):
    ADMITTED = "admitted"
    DISPATCHING = "dispatching"
    ACCEPTED = "accepted"
    STREAM_STARTED = "stream_started"
    VISIBLE_OUTPUT = "visible_output"
    TOOL_CALL_CONTENT = "tool_call_content"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


class ModelRecoveryKind(StrEnum):
    SAME_DEPLOYMENT_RETRY = "same_deployment_retry"
    ALTERNATE_ROUTE = "alternate_route"
    CRASH_RECONCILIATION = "crash_reconciliation"


class ModelRecoveryOutcome(StrEnum):
    ADMITTED = "admitted"
    DENIED = "denied"
    RECONCILED_COMPLETED = "reconciled_completed"
    RECONCILED_FAILED = "reconciled_failed"
    AMBIGUOUS = "ambiguous"


class ModelInvocationFailureReason(StrEnum):
    NO_ELIGIBLE_ROUTE = "no_eligible_route"
    ADMISSION_DENIED = "admission_denied"
    ATTEMPT_FAILED = "attempt_failed"
    AMBIGUOUS_RECOVERY = "ambiguous_recovery"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    BUDGET_EXHAUSTED = "budget_exhausted"


class _ModelInvocationRecord(RuntimeContract):
    schema_version: Literal[1] = 1
    record_id: Annotated[str, Field(min_length=1, max_length=180)]
    run_id: Annotated[str, Field(min_length=1, max_length=160)]
    snapshot_id: Annotated[str, Field(min_length=1, max_length=160)]
    snapshot_digest: str = Field(pattern=_SHA256_PATTERN)
    model_call_id: Annotated[str, Field(min_length=1, max_length=160)]
    invocation_id: Annotated[str, Field(min_length=1, max_length=180)]
    created_at: datetime
    record_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _digest_matches(self) -> "_ModelInvocationRecord":
        if self.record_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError(
                "model invocation record digest does not match canonical record"
            )
        return self

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"created_at", "record_digest"},
        )


class ModelInvocationPlannedRecord(_ModelInvocationRecord):
    """Immutable invocation identity and the complete route-plan binding."""

    record_kind: Literal["invocation_planned"] = "invocation_planned"
    execution_scope: Annotated[str, Field(min_length=1, max_length=320)]
    model_turn: PositiveInt
    purpose: Purpose
    request_digest: str = Field(pattern=_SHA256_PATTERN)
    requirements_digest: str = Field(pattern=_SHA256_PATTERN)
    requirements_revision: Annotated[str, Field(min_length=1, max_length=160)]
    descriptor_set_revision: Annotated[str, Field(min_length=1, max_length=160)]
    route_plan_id: Annotated[str, Field(min_length=1, max_length=180)]
    route_digest: str = Field(pattern=_ROUTE_DIGEST_PATTERN)
    route_policy_revision: Annotated[str, Field(min_length=1, max_length=160)]
    fallback_policy: ModelFallbackPolicy
    max_attempts: PositiveInt = Field(le=3)
    max_same_deployment_attempts: PositiveInt = Field(le=3)
    max_cost_microusd: NonNegativeInt | None = None
    max_input_tokens: PositiveInt | None = Field(default=None, le=10_000_000)
    max_output_tokens: PositiveInt | None = Field(default=None, le=10_000_000)
    deadline_at: datetime | None = None
    eligible_route_count: NonNegativeInt
    exclusion_count: NonNegativeInt
    status: Literal[ModelInvocationStatus.PLANNED] = ModelInvocationStatus.PLANNED

    @field_validator("deadline_at")
    @classmethod
    def _aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _budget_is_consistent(self) -> "ModelInvocationPlannedRecord":
        if self.max_same_deployment_attempts > self.max_attempts:
            raise ValueError("max_same_deployment_attempts cannot exceed max_attempts")
        return self

    @classmethod
    def create(
        cls,
        *,
        binding: RunControlBinding,
        identity: RuntimeModelCallIdentity,
        purpose: Purpose,
        request_digest: str,
        requirements_digest: str,
        requirements_revision: str,
        descriptor_set_revision: str,
        route_plan: ModelRoutePlan,
        created_at: datetime | None = None,
    ) -> "ModelInvocationPlannedRecord":
        snapshot = binding.snapshot
        if (
            identity.run_id != snapshot.run_id
            or identity.snapshot_id != snapshot.snapshot_id
        ):
            raise ModelInvocationSnapshotConflict(run_id=identity.run_id)
        invocation_id = _invocation_id(
            run_id=identity.run_id,
            model_call_id=identity.model_call_id,
            purpose=purpose,
        )
        route_plan_id = _route_plan_id(invocation_id, route_plan.route_digest)
        budget = route_plan.budget
        values: dict[str, object] = {
            "record_id": _record_id(invocation_id, "invocation_planned", "root"),
            "run_id": snapshot.run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_digest": snapshot.snapshot_digest,
            "model_call_id": identity.model_call_id,
            "invocation_id": invocation_id,
            "execution_scope": identity.execution_scope,
            "model_turn": identity.model_turn,
            "purpose": purpose,
            "request_digest": request_digest,
            "requirements_digest": requirements_digest,
            "requirements_revision": requirements_revision,
            "descriptor_set_revision": descriptor_set_revision,
            "route_plan_id": route_plan_id,
            "route_digest": route_plan.route_digest,
            "route_policy_revision": route_plan.policy_revision,
            "fallback_policy": route_plan.fallback_policy,
            "max_attempts": budget.max_attempts,
            "max_same_deployment_attempts": budget.max_same_deployment_attempts,
            "max_cost_microusd": budget.max_cost_microusd,
            "max_input_tokens": budget.max_input_tokens,
            "max_output_tokens": budget.max_output_tokens,
            "deadline_at": budget.deadline_at,
            "eligible_route_count": len(route_plan.routes),
            "exclusion_count": len(route_plan.exclusions),
            "status": ModelInvocationStatus.PLANNED,
            "created_at": created_at or datetime.now(timezone.utc),
        }
        return _finish_record(cls, values)


class ModelRouteEligibleRecord(_ModelInvocationRecord):
    """One ordered, fully bound, non-secret eligible deployment."""

    record_kind: Literal["route_eligible"] = "route_eligible"
    route_plan_id: Annotated[str, Field(min_length=1, max_length=180)]
    route_digest: str = Field(pattern=_ROUTE_DIGEST_PATTERN)
    route_ordinal: PositiveInt
    deployment_id: Annotated[str, Field(min_length=1, max_length=255)]
    deployment_revision: Annotated[str, Field(min_length=1, max_length=255)]
    descriptor_revision: Annotated[str, Field(min_length=1, max_length=255)]
    endpoint_ref: str = Field(pattern=_ENDPOINT_REF_PATTERN)
    endpoint_revision: Annotated[str, Field(min_length=1, max_length=255)]
    provider: str = Field(pattern=_PROVIDER_PATTERN, max_length=64)
    model_name: Annotated[str, Field(min_length=1, max_length=200)]
    region: Annotated[str, Field(min_length=1, max_length=64)]
    credential_mode: ModelCredentialMode
    price_revision: Annotated[str, Field(min_length=1, max_length=255)]
    qualification_revision: Annotated[str, Field(min_length=1, max_length=255)]
    max_input_tokens: PositiveInt = Field(le=2_000_000)
    max_output_tokens: PositiveInt = Field(le=2_000_000)

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        route: ModelRouteEntry,
        route_ordinal: int,
        created_at: datetime | None = None,
    ) -> "ModelRouteEligibleRecord":
        values = _child_values(
            invocation,
            record_kind="route_eligible",
            natural_key=str(route_ordinal),
            created_at=created_at,
        )
        values.update(
            {
                "route_plan_id": invocation.route_plan_id,
                "route_digest": invocation.route_digest,
                "route_ordinal": route_ordinal,
                **route.model_dump(mode="python"),
            }
        )
        return _finish_record(cls, values)


class ModelRouteExcludedRecord(_ModelInvocationRecord):
    """One descriptor exclusion with closed, body-free reasons."""

    record_kind: Literal["route_excluded"] = "route_excluded"
    route_plan_id: Annotated[str, Field(min_length=1, max_length=180)]
    route_digest: str = Field(pattern=_ROUTE_DIGEST_PATTERN)
    deployment_id: Annotated[str, Field(min_length=1, max_length=255)]
    reasons: tuple[ModelRouteExclusionReason, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _reasons_are_canonical(self) -> "ModelRouteExcludedRecord":
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("route exclusion reasons must be unique")
        if self.reasons != tuple(sorted(self.reasons, key=str)):
            raise ValueError("route exclusion reasons must be canonically ordered")
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        exclusion: ModelRouteExclusion,
        created_at: datetime | None = None,
    ) -> "ModelRouteExcludedRecord":
        values = _child_values(
            invocation,
            record_kind="route_excluded",
            natural_key=exclusion.deployment_id,
            created_at=created_at,
        )
        values.update(
            {
                "route_plan_id": invocation.route_plan_id,
                "route_digest": invocation.route_digest,
                "deployment_id": exclusion.deployment_id,
                "reasons": tuple(sorted(exclusion.reasons, key=str)),
            }
        )
        return _finish_record(cls, values)


class ModelAttemptAdmissionRecord(_ModelInvocationRecord):
    """Persisted attempt decision; admitted identity exists before dispatch."""

    record_kind: Literal["attempt_admission"] = "attempt_admission"
    admission_ordinal: PositiveInt
    decision: ModelAttemptDecisionKind
    reason: ModelAttemptDecisionReason
    attempt_id: Annotated[str, Field(min_length=1, max_length=180)] | None = None
    attempt_ordinal: PositiveInt | None = None
    deployment_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    prior_attempt_count: NonNegativeInt
    external_effect_observed: bool = False
    projected_cost_microusd: NonNegativeInt | None = None
    projected_input_tokens: NonNegativeInt | None = None
    projected_output_tokens: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _decision_reconciles(self) -> "ModelAttemptAdmissionRecord":
        ModelAttemptDecision(
            kind=self.decision,
            reason=self.reason,
            deployment_id=self.deployment_id,
            ordinal=self.attempt_ordinal,
        )
        if self.decision is ModelAttemptDecisionKind.ADMIT:
            assert self.attempt_id is not None
            if self.attempt_ordinal != self.admission_ordinal:
                raise ValueError(
                    "admitted attempt ordinal must match admission ordinal"
                )
            expected_id = _attempt_id(self.invocation_id, self.admission_ordinal)
            if self.attempt_id != expected_id:
                raise ValueError("attempt_id is not stable for its invocation ordinal")
        elif self.attempt_id is not None:
            raise ValueError("denied admission cannot allocate an attempt_id")
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        decision: ModelAttemptDecision,
        admission_ordinal: int,
        prior_attempt_count: int,
        external_effect_observed: bool = False,
        projected_cost_microusd: int | None = None,
        projected_input_tokens: int | None = None,
        projected_output_tokens: int | None = None,
        created_at: datetime | None = None,
    ) -> "ModelAttemptAdmissionRecord":
        attempt_id = (
            _attempt_id(invocation.invocation_id, admission_ordinal)
            if decision.kind is ModelAttemptDecisionKind.ADMIT
            else None
        )
        values = _child_values(
            invocation,
            record_kind="attempt_admission",
            natural_key=str(admission_ordinal),
            created_at=created_at,
        )
        values.update(
            {
                "admission_ordinal": admission_ordinal,
                "decision": decision.kind,
                "reason": decision.reason,
                "attempt_id": attempt_id,
                "attempt_ordinal": decision.ordinal,
                "deployment_id": decision.deployment_id,
                "prior_attempt_count": prior_attempt_count,
                "external_effect_observed": external_effect_observed,
                "projected_cost_microusd": projected_cost_microusd,
                "projected_input_tokens": projected_input_tokens,
                "projected_output_tokens": projected_output_tokens,
            }
        )
        return _finish_record(cls, values)


class ModelAttemptStateRecord(_ModelInvocationRecord):
    """One monotonic, body-free lifecycle fact for an admitted attempt."""

    record_kind: Literal["attempt_state"] = "attempt_state"
    attempt_id: Annotated[str, Field(min_length=1, max_length=180)]
    attempt_ordinal: PositiveInt
    deployment_id: Annotated[str, Field(min_length=1, max_length=255)]
    state: ModelAttemptLifecycleState
    dispatch_state: ModelDispatchState
    stream_state: ModelStreamState
    visible_text_emitted: bool = False
    tool_call_content_emitted: bool = False
    external_effect_observed: bool = False
    provider_request_digest: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    elapsed_ms: NonNegativeInt = 0

    @model_validator(mode="after")
    def _progress_reconciles(self) -> "ModelAttemptStateRecord":
        if (
            self.visible_text_emitted
            and self.stream_state is not ModelStreamState.VISIBLE_OUTPUT
        ):
            raise ValueError("visible text requires visible-output stream state")
        if self.tool_call_content_emitted and self.dispatch_state not in {
            ModelDispatchState.ACCEPTED,
            ModelDispatchState.UNKNOWN,
        }:
            raise ValueError("tool-call content requires accepted or unknown dispatch")
        if self.state is ModelAttemptLifecycleState.VISIBLE_OUTPUT:
            if not self.visible_text_emitted:
                raise ValueError("visible-output state requires visible_text_emitted")
        if self.state is ModelAttemptLifecycleState.TOOL_CALL_CONTENT:
            if not self.tool_call_content_emitted:
                raise ValueError(
                    "tool-call-content state requires tool_call_content_emitted"
                )
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        admission: ModelAttemptAdmissionRecord,
        state: ModelAttemptLifecycleState,
        dispatch_state: ModelDispatchState,
        stream_state: ModelStreamState,
        visible_text_emitted: bool = False,
        tool_call_content_emitted: bool = False,
        external_effect_observed: bool = False,
        provider_request_digest: str | None = None,
        elapsed_ms: int = 0,
        created_at: datetime | None = None,
    ) -> "ModelAttemptStateRecord":
        attempt_id, attempt_ordinal, deployment_id = _admitted_identity(admission)
        values = _child_values(
            invocation,
            record_kind="attempt_state",
            natural_key=f"{attempt_id}:{state.value}",
            created_at=created_at,
        )
        values.update(
            {
                "attempt_id": attempt_id,
                "attempt_ordinal": attempt_ordinal,
                "deployment_id": deployment_id,
                "state": state,
                "dispatch_state": dispatch_state,
                "stream_state": stream_state,
                "visible_text_emitted": visible_text_emitted,
                "tool_call_content_emitted": tool_call_content_emitted,
                "external_effect_observed": external_effect_observed,
                "provider_request_digest": provider_request_digest,
                "elapsed_ms": elapsed_ms,
            }
        )
        return _finish_record(cls, values)


class ModelAttemptUsageRecord(_ModelInvocationRecord):
    """Exactly-once usage finalization for one admitted provider attempt."""

    record_kind: Literal["attempt_usage"] = "attempt_usage"
    attempt_id: Annotated[str, Field(min_length=1, max_length=180)]
    attempt_ordinal: PositiveInt
    deployment_id: Annotated[str, Field(min_length=1, max_length=255)]
    usage_record_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    provider_reported: bool
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0
    cost_microusd: NonNegativeInt = 0
    duration_ms: NonNegativeInt = 0
    finalized: Literal[True] = True

    @model_validator(mode="after")
    def _usage_reconciles(self) -> "ModelAttemptUsageRecord":
        if (
            self.cached_input_tokens + self.cache_creation_input_tokens
            > self.input_tokens
        ):
            raise ValueError("cache token subsets exceed provider input tokens")
        if not self.provider_reported and any(
            (
                self.input_tokens,
                self.output_tokens,
                self.cached_input_tokens,
                self.cache_creation_input_tokens,
                self.reasoning_tokens,
                self.audio_input_tokens,
                self.audio_output_tokens,
                self.cost_microusd,
            )
        ):
            raise ValueError(
                "unreported provider usage cannot carry token or cost data"
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        admission: ModelAttemptAdmissionRecord,
        usage: NormalizedTokenUsage,
        provider_reported: bool,
        usage_record_id: str | None = None,
        cost_microusd: int = 0,
        duration_ms: int = 0,
        created_at: datetime | None = None,
    ) -> "ModelAttemptUsageRecord":
        attempt_id, attempt_ordinal, deployment_id = _admitted_identity(admission)
        values = _child_values(
            invocation,
            record_kind="attempt_usage",
            natural_key=attempt_id,
            created_at=created_at,
        )
        values.update(
            {
                "attempt_id": attempt_id,
                "attempt_ordinal": attempt_ordinal,
                "deployment_id": deployment_id,
                "usage_record_id": usage_record_id,
                "provider_reported": provider_reported,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "audio_input_tokens": usage.audio_input_tokens,
                "audio_output_tokens": usage.audio_output_tokens,
                "cost_microusd": cost_microusd,
                "duration_ms": duration_ms,
                "finalized": True,
            }
        )
        return _finish_record(cls, values)


class ModelAttemptFailedRecord(_ModelInvocationRecord):
    """Sanitized terminal failure for one admitted provider attempt."""

    record_kind: Literal["attempt_failed"] = "attempt_failed"
    attempt_id: Annotated[str, Field(min_length=1, max_length=180)]
    attempt_ordinal: PositiveInt
    deployment_id: Annotated[str, Field(min_length=1, max_length=255)]
    failure_class: ModelFailureClass
    dispatch_state: ModelDispatchState
    stream_state: ModelStreamState
    provider_failure_observed: bool
    visible_text_emitted: bool = False
    tool_call_content_emitted: bool = False
    external_effect_observed: bool = False
    usage_may_be_incomplete: bool = False

    @model_validator(mode="after")
    def _failure_reconciles(self) -> "ModelAttemptFailedRecord":
        if (
            self.visible_text_emitted
            and self.stream_state is not ModelStreamState.VISIBLE_OUTPUT
        ):
            raise ValueError("visible text requires visible-output stream state")
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        admission: ModelAttemptAdmissionRecord,
        failure_class: ModelFailureClass,
        dispatch_state: ModelDispatchState,
        stream_state: ModelStreamState,
        provider_failure_observed: bool,
        visible_text_emitted: bool = False,
        tool_call_content_emitted: bool = False,
        external_effect_observed: bool = False,
        usage_may_be_incomplete: bool = False,
        created_at: datetime | None = None,
    ) -> "ModelAttemptFailedRecord":
        attempt_id, attempt_ordinal, deployment_id = _admitted_identity(admission)
        values = _child_values(
            invocation,
            record_kind="attempt_failed",
            natural_key=attempt_id,
            created_at=created_at,
        )
        values.update(
            {
                "attempt_id": attempt_id,
                "attempt_ordinal": attempt_ordinal,
                "deployment_id": deployment_id,
                "failure_class": failure_class,
                "dispatch_state": dispatch_state,
                "stream_state": stream_state,
                "provider_failure_observed": provider_failure_observed,
                "visible_text_emitted": visible_text_emitted,
                "tool_call_content_emitted": tool_call_content_emitted,
                "external_effect_observed": external_effect_observed,
                "usage_may_be_incomplete": usage_may_be_incomplete,
            }
        )
        return _finish_record(cls, values)


class ModelInvocationRecoveryRecord(_ModelInvocationRecord):
    """Retry/reroute/crash-reconciliation fact without replay authority."""

    record_kind: Literal["invocation_recovery"] = "invocation_recovery"
    recovery_ordinal: PositiveInt
    source_attempt_id: Annotated[str, Field(min_length=1, max_length=180)]
    kind: ModelRecoveryKind
    outcome: ModelRecoveryOutcome
    decision_reason: ModelAttemptDecisionReason | None = None
    target_attempt_id: Annotated[str, Field(min_length=1, max_length=180)] | None = None
    visible_text_emitted: bool = False
    tool_call_content_emitted: bool = False
    external_effect_observed: bool = False

    @model_validator(mode="after")
    def _recovery_reconciles(self) -> "ModelInvocationRecoveryRecord":
        if self.outcome is ModelRecoveryOutcome.ADMITTED:
            if self.target_attempt_id is None or self.decision_reason is None:
                raise ValueError(
                    "admitted recovery requires target attempt and decision reason"
                )
        elif self.target_attempt_id is not None:
            raise ValueError("non-admitted recovery cannot allocate a target attempt")
        if self.outcome is ModelRecoveryOutcome.ADMITTED and (
            self.visible_text_emitted
            or self.tool_call_content_emitted
            or self.external_effect_observed
        ):
            raise ValueError(
                "recovery cannot be admitted after visible/effect progress"
            )
        if (
            self.kind is ModelRecoveryKind.CRASH_RECONCILIATION
            and self.outcome is ModelRecoveryOutcome.ADMITTED
        ):
            raise ValueError("crash recovery cannot authorize a blind new attempt")
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        source_attempt_id: str,
        recovery_ordinal: int,
        kind: ModelRecoveryKind,
        outcome: ModelRecoveryOutcome,
        decision_reason: ModelAttemptDecisionReason | None = None,
        target_attempt_ordinal: int | None = None,
        visible_text_emitted: bool = False,
        tool_call_content_emitted: bool = False,
        external_effect_observed: bool = False,
        created_at: datetime | None = None,
    ) -> "ModelInvocationRecoveryRecord":
        target_attempt_id = (
            _attempt_id(invocation.invocation_id, target_attempt_ordinal)
            if target_attempt_ordinal is not None
            else None
        )
        values = _child_values(
            invocation,
            record_kind="invocation_recovery",
            natural_key=str(recovery_ordinal),
            created_at=created_at,
        )
        values.update(
            {
                "recovery_ordinal": recovery_ordinal,
                "source_attempt_id": source_attempt_id,
                "kind": kind,
                "outcome": outcome,
                "decision_reason": decision_reason,
                "target_attempt_id": target_attempt_id,
                "visible_text_emitted": visible_text_emitted,
                "tool_call_content_emitted": tool_call_content_emitted,
                "external_effect_observed": external_effect_observed,
            }
        )
        return _finish_record(cls, values)


class ModelInvocationCompletedRecord(_ModelInvocationRecord):
    """Successful terminal invocation attributed to one completed attempt."""

    record_kind: Literal["invocation_completed"] = "invocation_completed"
    terminal_attempt_id: Annotated[str, Field(min_length=1, max_length=180)]
    attempt_count: PositiveInt
    total_input_tokens: NonNegativeInt = 0
    total_output_tokens: NonNegativeInt = 0
    total_cost_microusd: NonNegativeInt = 0
    total_duration_ms: NonNegativeInt = 0
    status: Literal[ModelInvocationStatus.COMPLETED] = ModelInvocationStatus.COMPLETED

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        terminal_attempt_id: str,
        attempt_count: int,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_cost_microusd: int = 0,
        total_duration_ms: int = 0,
        created_at: datetime | None = None,
    ) -> "ModelInvocationCompletedRecord":
        values = _child_values(
            invocation,
            record_kind="invocation_completed",
            natural_key="terminal",
            created_at=created_at,
        )
        values.update(
            {
                "terminal_attempt_id": terminal_attempt_id,
                "attempt_count": attempt_count,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_cost_microusd": total_cost_microusd,
                "total_duration_ms": total_duration_ms,
                "status": ModelInvocationStatus.COMPLETED,
            }
        )
        return _finish_record(cls, values)


class ModelInvocationFailedRecord(_ModelInvocationRecord):
    """Terminal invocation failure or admission denial."""

    record_kind: Literal["invocation_failed"] = "invocation_failed"
    terminal_attempt_id: Annotated[str, Field(min_length=1, max_length=180)] | None = (
        None
    )
    attempt_count: NonNegativeInt
    reason: ModelInvocationFailureReason
    failure_class: ModelFailureClass | None = None
    total_input_tokens: NonNegativeInt = 0
    total_output_tokens: NonNegativeInt = 0
    total_cost_microusd: NonNegativeInt = 0
    total_duration_ms: NonNegativeInt = 0
    status: Literal[ModelInvocationStatus.FAILED] = ModelInvocationStatus.FAILED

    @model_validator(mode="after")
    def _failure_reconciles(self) -> "ModelInvocationFailedRecord":
        if self.attempt_count == 0 and self.terminal_attempt_id is not None:
            raise ValueError("zero-attempt failure cannot bind a terminal attempt")
        if self.attempt_count > 0 and self.terminal_attempt_id is None:
            raise ValueError("attempt failure requires a terminal attempt")
        return self

    @classmethod
    def create(
        cls,
        *,
        invocation: ModelInvocationPlannedRecord,
        attempt_count: int,
        reason: ModelInvocationFailureReason,
        terminal_attempt_id: str | None = None,
        failure_class: ModelFailureClass | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_cost_microusd: int = 0,
        total_duration_ms: int = 0,
        created_at: datetime | None = None,
    ) -> "ModelInvocationFailedRecord":
        values = _child_values(
            invocation,
            record_kind="invocation_failed",
            natural_key="terminal",
            created_at=created_at,
        )
        values.update(
            {
                "terminal_attempt_id": terminal_attempt_id,
                "attempt_count": attempt_count,
                "reason": reason,
                "failure_class": failure_class,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_cost_microusd": total_cost_microusd,
                "total_duration_ms": total_duration_ms,
                "status": ModelInvocationStatus.FAILED,
            }
        )
        return _finish_record(cls, values)


ModelInvocationRecord: TypeAlias = (
    ModelInvocationPlannedRecord
    | ModelRouteEligibleRecord
    | ModelRouteExcludedRecord
    | ModelAttemptAdmissionRecord
    | ModelAttemptStateRecord
    | ModelAttemptUsageRecord
    | ModelAttemptFailedRecord
    | ModelInvocationRecoveryRecord
    | ModelInvocationCompletedRecord
    | ModelInvocationFailedRecord
)


class SequencedModelInvocationRecord(RuntimeContract):
    sequence_no: Annotated[int, Field(ge=1)]
    record: ModelInvocationRecord = Field(discriminator="record_kind")


class ModelInvocationWrite(RuntimeContract):
    """Verified tenant/subject transport facts for one invocation append."""

    org_id: Annotated[str, Field(min_length=1, max_length=160)]
    subject_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    trace_id: Annotated[str, Field(min_length=1, max_length=160)]
    record: ModelInvocationRecord = Field(discriminator="record_kind")


class ModelInvocationStorePort(Protocol):
    async def append(
        self,
        write: ModelInvocationWrite,
    ) -> SequencedModelInvocationRecord: ...

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedModelInvocationRecord, ...]: ...

    async def list_for_invocation(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        invocation_id: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedModelInvocationRecord, ...]: ...


class ModelInvocationJournalError(RuntimeError):
    """Base error for fail-closed invocation persistence."""


class ModelInvocationConflict(ModelInvocationJournalError):
    def __init__(self, *, run_id: str, record_id: str) -> None:
        self.run_id = run_id
        self.record_id = record_id
        super().__init__(
            f"model invocation record {record_id} conflicts for run {run_id}"
        )


class ModelInvocationCorruption(ModelInvocationJournalError):
    def __init__(self, *, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"model invocation journal for run {run_id}: {reason}")


class ModelInvocationScopeConflict(ModelInvocationJournalError):
    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"model invocation scope conflict for run {run_id}")


class ModelInvocationSnapshotConflict(ModelInvocationJournalError):
    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"model invocation snapshot conflict for run {run_id}")


def route_records(
    invocation: ModelInvocationPlannedRecord,
    route_plan: ModelRoutePlan,
    *,
    created_at: datetime | None = None,
) -> tuple[ModelRouteEligibleRecord | ModelRouteExcludedRecord, ...]:
    """Project one verified route plan into canonical ordered journal records."""

    if (
        invocation.route_digest != route_plan.route_digest
        or invocation.route_policy_revision != route_plan.policy_revision
        or invocation.fallback_policy is not route_plan.fallback_policy
        or invocation.eligible_route_count != len(route_plan.routes)
        or invocation.exclusion_count != len(route_plan.exclusions)
    ):
        raise ValueError("route plan does not match invocation binding")
    eligible = tuple(
        ModelRouteEligibleRecord.create(
            invocation=invocation,
            route=route,
            route_ordinal=ordinal,
            created_at=created_at,
        )
        for ordinal, route in enumerate(route_plan.routes, start=1)
    )
    excluded = tuple(
        ModelRouteExcludedRecord.create(
            invocation=invocation,
            exclusion=exclusion,
            created_at=created_at,
        )
        for exclusion in route_plan.exclusions
    )
    return (*eligible, *excluded)


def _finish_record(
    record_type: type[_RecordT],
    values: dict[str, object],
) -> _RecordT:
    provisional = record_type.model_construct(**values, record_digest="0" * 64)
    return record_type(
        **values,
        record_digest=canonical_json_sha256(provisional.digest_payload()),
    )


def _child_values(
    invocation: ModelInvocationPlannedRecord,
    *,
    record_kind: str,
    natural_key: str,
    created_at: datetime | None,
) -> dict[str, object]:
    return {
        "record_id": _record_id(invocation.invocation_id, record_kind, natural_key),
        "run_id": invocation.run_id,
        "snapshot_id": invocation.snapshot_id,
        "snapshot_digest": invocation.snapshot_digest,
        "model_call_id": invocation.model_call_id,
        "invocation_id": invocation.invocation_id,
        "created_at": created_at or datetime.now(timezone.utc),
    }


def _admitted_identity(
    admission: ModelAttemptAdmissionRecord,
) -> tuple[str, int, str]:
    if (
        admission.decision is not ModelAttemptDecisionKind.ADMIT
        or admission.attempt_id is None
        or admission.attempt_ordinal is None
        or admission.deployment_id is None
    ):
        raise ValueError("attempt lifecycle requires an admitted attempt")
    return (
        admission.attempt_id,
        admission.attempt_ordinal,
        admission.deployment_id,
    )


def _invocation_id(*, run_id: str, model_call_id: str, purpose: Purpose) -> str:
    digest = canonical_json_sha256(
        {
            "model_call_id": model_call_id,
            "purpose": purpose.value,
            "run_id": run_id,
        }
    )
    return f"model-invocation:{digest}"


def _route_plan_id(invocation_id: str, route_digest: str) -> str:
    digest = hashlib.sha256(
        f"{invocation_id}\x00{route_digest}".encode("utf-8")
    ).hexdigest()
    return f"model-route:{digest}"


def _attempt_id(invocation_id: str, ordinal: int | None) -> str:
    if ordinal is None or ordinal < 1:
        raise ValueError("attempt ordinal must be positive")
    digest = hashlib.sha256(f"{invocation_id}\x00{ordinal}".encode("utf-8")).hexdigest()
    return f"model-attempt:{digest}"


def _record_id(invocation_id: str, record_kind: str, natural_key: str) -> str:
    digest = hashlib.sha256(
        f"{invocation_id}\x00{record_kind}\x00{natural_key}".encode("utf-8")
    ).hexdigest()
    return f"model-invocation-record:{digest}"


__all__ = (
    "ModelAttemptAdmissionRecord",
    "ModelAttemptFailedRecord",
    "ModelAttemptLifecycleState",
    "ModelAttemptStateRecord",
    "ModelAttemptUsageRecord",
    "ModelInvocationCompletedRecord",
    "ModelInvocationConflict",
    "ModelInvocationCorruption",
    "ModelInvocationFailedRecord",
    "ModelInvocationFailureReason",
    "ModelInvocationJournalError",
    "ModelInvocationPlannedRecord",
    "ModelInvocationRecord",
    "ModelInvocationRecoveryRecord",
    "ModelInvocationScopeConflict",
    "ModelInvocationSnapshotConflict",
    "ModelInvocationStatus",
    "ModelInvocationStorePort",
    "ModelInvocationWrite",
    "ModelRecoveryKind",
    "ModelRecoveryOutcome",
    "ModelRouteEligibleRecord",
    "ModelRouteExcludedRecord",
    "SequencedModelInvocationRecord",
    "route_records",
)
