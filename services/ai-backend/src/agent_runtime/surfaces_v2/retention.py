"""Pure, fail-closed retention planning for lifecycle-owned content (PRD-E1 D10/D11).

This module deliberately stops before persistence, authorization, jobs, and physical
deletion.  A future coordinator supplies a *trusted snapshot* of the lifecycle graph,
logical-reference state, and legal-hold coverage; this planner returns a stable,
cursor-safe instruction for that snapshot:

* retain an active candidate;
* create or preserve a logical tombstone only; or
* make a tombstoned candidate eligible for a later physical-GC executor.

The planner cannot make a body physically eligible unless it has a complete graph,
every logical reference is gone or terminal-and-expired, the tombstone grace interval
has elapsed, and no legal hold covers it.  It also refuses to infer ownership from an
unknown scheme or a tenant-local view of a shared physical blob.  Its output contains
opaque candidate identifiers and closed reason codes only -- never a path, body,
untrusted reference value, or storage key.

It intentionally does not import ``runtime_api`` or any adapter, and it never invokes
``LifecycleReferenceEnumerator`` itself.  Enumeration needs persistent owners to add
stage/effect/receipt/recovery state; wiring that I/O belongs in a later coordinator.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import logging
import re
from time import perf_counter
from typing import Final

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.lifecycle_metrics import (
    LifecycleOperationalMetrics,
    LifecyclePlanDecisionMetric,
    LifecyclePlanOutcomeLabel,
    LifecyclePlannerLabel,
    RetentionLagStageLabel,
    get_lifecycle_operational_metrics,
)
from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceScheme


_OPAQUE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
)
_SAFE_SCHEME: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_KNOWN_SCHEMES: Final[frozenset[str]] = frozenset(
    scheme.value for scheme in LifecycleReferenceScheme
)
_LOGGER = logging.getLogger(__name__)


def _opaque_identifier(value: str) -> str:
    """Accept only an opaque token, never a physical path or rendered content."""

    if _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier must be a safe opaque token")
    return value


def _aware_utc(value: datetime) -> datetime:
    """Normalize planning time inputs so comparisons are deterministic."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class RetentionCandidateKind(StrEnum):
    """Retention categories whose policy windows are resolved by a future caller."""

    ARTIFACT_BLOB = "artifact_blob"
    ARTIFACT_METADATA = "artifact_metadata"
    PREIMAGE = "preimage"
    PREPARED_TEMP = "prepared_temp"
    AUDIT_METADATA = "audit_metadata"
    USAGE_METADATA = "usage_metadata"
    OTHER = "other"


class RetentionCandidateState(StrEnum):
    """Product lifecycle state; physical deletion is never represented here."""

    ACTIVE = "active"
    LOGICALLY_TOMBSTONED = "logically_tombstoned"


class RetentionReferenceRole(StrEnum):
    """Why a logical reference exists, without including its raw reference value."""

    RUN = "run"
    STAGE = "stage"
    EFFECT = "effect"
    RECEIPT = "receipt"
    LEGAL_HOLD = "legal_hold"
    RECOVERY = "recovery"
    AUDIT = "audit"
    ARTIFACT = "artifact"
    TEMPORARY = "temporary"
    OTHER = "other"


class RetentionReferenceLifecycleState(StrEnum):
    """Terminality of the logical owner, separate from retention expiry."""

    ACTIVE = "active"
    PENDING = "pending"
    TERMINAL = "terminal"
    INDETERMINATE = "indeterminate"
    UNKNOWN = "unknown"


class RetentionReferencePresence(StrEnum):
    """Whether the logical reference still exists after its owner became terminal."""

    PRESENT = "present"
    GONE = "gone"


class RetentionEnumerationCoverage(StrEnum):
    """Completeness boundary of the snapshot supplied by the future coordinator."""

    INCOMPLETE = "incomplete"
    COMPLETE_TENANT = "complete_tenant"
    COMPLETE_GLOBAL = "complete_global"


class RetentionLegalHoldState(StrEnum):
    """Only a released hold permits physical eligibility."""

    ACTIVE = "active"
    RELEASED = "released"
    UNKNOWN = "unknown"


class RetentionLegalHoldScope(StrEnum):
    """Scope metadata for a hold coverage assertion, not an authorization grant."""

    TENANT = "tenant"
    USER = "user"
    CONVERSATION = "conversation"
    REFERENCE = "reference"


class RetentionDecisionState(StrEnum):
    """The only plan states a later coordinator may consume."""

    RETAIN = "retain"
    LOGICAL_TOMBSTONE_ONLY = "logical_tombstone_only"
    PHYSICALLY_ELIGIBLE = "physically_eligible"


class RetentionReasonCode(StrEnum):
    """Closed, content- and path-safe explanations for a retention decision."""

    RETENTION_WINDOW_OPEN = "retention_window_open"
    LOGICAL_TOMBSTONE_REQUIRED = "logical_tombstone_required"
    PHYSICAL_PRECONDITIONS_MET = "physical_preconditions_met"
    ENUMERATION_INCOMPLETE = "enumeration_incomplete"
    UNKNOWN_REFERENCE_SCHEME = "unknown_reference_scheme"
    ACTIVE_OR_PENDING_RUN = "active_or_pending_run"
    ACTIVE_OR_PENDING_STAGE = "active_or_pending_stage"
    ACTIVE_OR_PENDING_EFFECT = "active_or_pending_effect"
    INDETERMINATE_OR_UNKNOWN_REFERENCE = "indeterminate_or_unknown_reference"
    LIVE_RECEIPT_REFERENCE = "live_receipt_reference"
    LIVE_HOLD_REFERENCE = "live_hold_reference"
    LIVE_RECOVERY_REFERENCE = "live_recovery_reference"
    LIVE_LOGICAL_REFERENCE = "live_logical_reference"
    CROSS_TENANT_REFERENCE = "cross_tenant_reference"
    ACTIVE_LEGAL_HOLD = "active_legal_hold"
    MISSING_TOMBSTONE_TIMESTAMP = "missing_tombstone_timestamp"
    PHYSICAL_GRACE_OPEN = "physical_grace_open"


class RetentionPlanningErrorCode(StrEnum):
    """Safe errors for malformed cursor batches; no candidate details are exposed."""

    CURSOR_SCOPE_MISMATCH = "cursor_scope_mismatch"
    CANDIDATE_TENANT_MISMATCH = "candidate_tenant_mismatch"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


class RetentionPlanningError(ValueError):
    """Fail-closed planning-input error whose message never includes caller data."""

    def __init__(self, code: RetentionPlanningErrorCode) -> None:
        self.code = code
        super().__init__("retention planning input is invalid")


class RetentionLogicalReference(RuntimeContract):
    """One trusted, redacted lifecycle reference relevant to a candidate.

    ``reference_id`` is an opaque owner-side handle.  It is intentionally not a
    URI, physical key, path, body, or display string.  ``scheme`` stays a string
    so the planner can turn a newly introduced/unregistered scheme into a
    fail-closed decision rather than treating it as impossible at model-parse time.
    """

    reference_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    scheme: str = Field(min_length=1, max_length=64)
    role: RetentionReferenceRole
    lifecycle_state: RetentionReferenceLifecycleState
    presence: RetentionReferencePresence
    expires_at: datetime | None = None

    @field_validator("reference_id", "tenant_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @field_validator("scheme")
    @classmethod
    def _safe_scheme(cls, value: str) -> str:
        if _SAFE_SCHEME.fullmatch(value) is None:
            raise ValueError("scheme must be a safe canonical token")
        return value

    @field_validator("expires_at")
    @classmethod
    def _aware_expiry(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value)

    @model_validator(mode="after")
    def _gone_requires_terminal_owner(self) -> RetentionLogicalReference:
        if (
            self.presence is RetentionReferencePresence.GONE
            and self.lifecycle_state is not RetentionReferenceLifecycleState.TERMINAL
        ):
            raise ValueError("a gone reference must have a terminal owner")
        return self

    def is_released_as_of(self, as_of: datetime) -> bool:
        """A ref only releases after its owner is terminal and it is gone/expired."""

        if self.lifecycle_state is not RetentionReferenceLifecycleState.TERMINAL:
            return False
        return self.presence is RetentionReferencePresence.GONE or (
            self.expires_at is not None and self.expires_at <= as_of
        )


class RetentionReferenceEnumeration(RuntimeContract):
    """A future owner-coordinator's complete-or-explicitly-incomplete snapshot."""

    coverage: RetentionEnumerationCoverage
    references: tuple[RetentionLogicalReference, ...] = ()

    @model_validator(mode="after")
    def _references_are_unique(self) -> RetentionReferenceEnumeration:
        identities = tuple(
            (reference.tenant_id, reference.reference_id)
            for reference in self.references
        )
        if len(identities) != len(set(identities)):
            raise ValueError("reference enumeration contains duplicate opaque ids")
        return self


class RetentionLegalHoldCoverage(RuntimeContract):
    """A trusted assertion that one legal hold currently covers this candidate."""

    hold_id: str = Field(min_length=1, max_length=256)
    scope: RetentionLegalHoldScope
    state: RetentionLegalHoldState

    @field_validator("hold_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)


class RetentionCandidate(RuntimeContract):
    """One logical/physical deletion candidate, never a storage command."""

    candidate_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    kind: RetentionCandidateKind
    state: RetentionCandidateState
    retention_expires_at: datetime
    tombstoned_at: datetime | None = None
    enumeration: RetentionReferenceEnumeration
    legal_hold_coverage: tuple[RetentionLegalHoldCoverage, ...] = ()

    @field_validator("candidate_id", "tenant_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @field_validator("retention_expires_at", "tombstoned_at")
    @classmethod
    def _aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value)

    @model_validator(mode="after")
    def _state_has_consistent_tombstone(self) -> RetentionCandidate:
        if (
            self.state is RetentionCandidateState.ACTIVE
            and self.tombstoned_at is not None
        ):
            raise ValueError("an active candidate cannot have a tombstone timestamp")
        if (
            self.state is RetentionCandidateState.LOGICALLY_TOMBSTONED
            and self.tombstoned_at is None
        ):
            raise ValueError("a tombstoned candidate requires a tombstone timestamp")
        return self


class RetentionPlanningPolicy(RuntimeContract):
    """Per-tenant/deployment policy input; this foundation owns no settings lookup."""

    physical_grace_period: timedelta

    @field_validator("physical_grace_period")
    @classmethod
    def _non_negative_grace(cls, value: timedelta) -> timedelta:
        if value < timedelta():
            raise ValueError("physical grace period must not be negative")
        return value


class RetentionPlanCursor(RuntimeContract):
    """Exclusive, snapshot-bound keyset cursor for retry-safe planning pages."""

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    after_candidate_id: str = Field(min_length=1, max_length=256)

    @field_validator("tenant_id", "snapshot_id", "after_candidate_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)


class RetentionPlanningRequest(RuntimeContract):
    """An immutable deterministic planning batch from a caller-held snapshot."""

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    as_of: datetime
    policy: RetentionPlanningPolicy
    candidates: tuple[RetentionCandidate, ...]
    cursor: RetentionPlanCursor | None = None
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("tenant_id", "snapshot_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class RetentionDecision(RuntimeContract):
    """A safe decision for one opaque candidate; no path/reference details escape."""

    candidate_id: str = Field(min_length=1, max_length=256)
    state: RetentionDecisionState
    reasons: tuple[RetentionReasonCode, ...]

    @field_validator("candidate_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @model_validator(mode="after")
    def _decision_has_reason(self) -> RetentionDecision:
        if not self.reasons:
            raise ValueError("retention decisions require at least one reason code")
        return self


class RetentionPlan(RuntimeContract):
    """One stable, keyset-paginated pure planning result."""

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    as_of: datetime
    decisions: tuple[RetentionDecision, ...]
    next_cursor: RetentionPlanCursor | None = None
    has_more: bool

    @field_validator("tenant_id", "snapshot_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class RetentionPlanner:
    """D10/D11 decision engine over a caller-provided immutable snapshot.

    The returned plan remains pure and deterministic.  The optional D13
    metrics façade observes only closed, redacted aggregate facts and is
    strictly best-effort; it never changes a planning result or failure.
    """

    def __init__(self, *, metrics: LifecycleOperationalMetrics | None = None) -> None:
        self._metrics = (
            metrics if metrics is not None else get_lifecycle_operational_metrics()
        )

    def plan(self, request: RetentionPlanningRequest) -> RetentionPlan:
        """Return a deterministic page without issuing, persisting, or deleting anything."""

        started_at = perf_counter()
        try:
            self._validate_request_scope(request)
            ordered = tuple(
                sorted(request.candidates, key=lambda row: row.candidate_id)
            )
            candidate_ids = tuple(row.candidate_id for row in ordered)
            if len(candidate_ids) != len(set(candidate_ids)):
                raise RetentionPlanningError(
                    RetentionPlanningErrorCode.DUPLICATE_CANDIDATE
                )

            after_candidate_id = (
                request.cursor.after_candidate_id
                if request.cursor is not None
                else None
            )
            remaining = tuple(
                candidate
                for candidate in ordered
                if after_candidate_id is None
                or candidate.candidate_id > after_candidate_id
            )
            page = remaining[: request.limit]
            has_more = len(remaining) > len(page)
            next_cursor = (
                RetentionPlanCursor(
                    tenant_id=request.tenant_id,
                    snapshot_id=request.snapshot_id,
                    after_candidate_id=page[-1].candidate_id,
                )
                if has_more and page
                else None
            )
            decisions = tuple(
                self._decide(candidate=candidate, request=request) for candidate in page
            )
            plan = RetentionPlan(
                tenant_id=request.tenant_id,
                snapshot_id=request.snapshot_id,
                as_of=request.as_of,
                decisions=decisions,
                next_cursor=next_cursor,
                has_more=has_more,
            )
        except RetentionPlanningError:
            self._record_planning_failure(
                LifecyclePlanOutcomeLabel.REJECTED_INPUT,
                elapsed_seconds=perf_counter() - started_at,
            )
            raise
        except Exception:
            self._record_planning_failure(
                LifecyclePlanOutcomeLabel.FAILED,
                elapsed_seconds=perf_counter() - started_at,
            )
            raise

        self._record_plan_metrics(
            request=request,
            page=page,
            plan=plan,
            elapsed_seconds=perf_counter() - started_at,
        )
        return plan

    def _record_planning_failure(self, outcome: str, *, elapsed_seconds: float) -> None:
        try:
            self._metrics.record_plan_failure(
                planner=LifecyclePlannerLabel.RETENTION,
                outcome=outcome,
                elapsed_seconds=elapsed_seconds,
            )
        except Exception:  # pragma: no cover - injected metrics must not affect D10
            _LOGGER.debug("retention_planner.metrics_failed", exc_info=True)

    def _record_plan_metrics(
        self,
        *,
        request: RetentionPlanningRequest,
        page: Sequence[RetentionCandidate],
        plan: RetentionPlan,
        elapsed_seconds: float,
    ) -> None:
        """Publish aggregate D13 facts only after a complete plan is available."""

        try:
            self._metrics.record_plan_success(
                planner=LifecyclePlannerLabel.RETENTION,
                decisions=tuple(
                    LifecyclePlanDecisionMetric(
                        candidate_kind=candidate.kind.value,
                        disposition=decision.state.value,
                    )
                    for candidate, decision in zip(page, plan.decisions, strict=True)
                ),
                elapsed_seconds=elapsed_seconds,
            )
            for candidate, decision in zip(page, plan.decisions, strict=True):
                if (
                    candidate.state is RetentionCandidateState.ACTIVE
                    and decision.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
                ):
                    self._metrics.record_retention_lag(
                        candidate_kind=candidate.kind.value,
                        stage=RetentionLagStageLabel.TOMBSTONE_DUE,
                        elapsed_seconds=(
                            request.as_of - candidate.retention_expires_at
                        ).total_seconds(),
                    )
                elif (
                    candidate.tombstoned_at is not None
                    and decision.state is RetentionDecisionState.PHYSICALLY_ELIGIBLE
                ):
                    due_at = (
                        candidate.tombstoned_at + request.policy.physical_grace_period
                    )
                    self._metrics.record_retention_lag(
                        candidate_kind=candidate.kind.value,
                        stage=RetentionLagStageLabel.PHYSICAL_GC_DUE,
                        elapsed_seconds=(request.as_of - due_at).total_seconds(),
                    )
        except Exception:  # pragma: no cover - metrics never change a plan
            _LOGGER.debug("retention_planner.metrics_failed", exc_info=True)

    @staticmethod
    def _validate_request_scope(request: RetentionPlanningRequest) -> None:
        if request.cursor is not None and (
            request.cursor.tenant_id != request.tenant_id
            or request.cursor.snapshot_id != request.snapshot_id
        ):
            raise RetentionPlanningError(
                RetentionPlanningErrorCode.CURSOR_SCOPE_MISMATCH
            )
        if any(
            candidate.tenant_id != request.tenant_id for candidate in request.candidates
        ):
            raise RetentionPlanningError(
                RetentionPlanningErrorCode.CANDIDATE_TENANT_MISMATCH
            )

    def _decide(
        self,
        *,
        candidate: RetentionCandidate,
        request: RetentionPlanningRequest,
    ) -> RetentionDecision:
        if candidate.state is RetentionCandidateState.ACTIVE:
            if request.as_of < candidate.retention_expires_at:
                return self._decision(
                    candidate.candidate_id,
                    RetentionDecisionState.RETAIN,
                    (RetentionReasonCode.RETENTION_WINDOW_OPEN,),
                )
            return self._decision(
                candidate.candidate_id,
                RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY,
                (RetentionReasonCode.LOGICAL_TOMBSTONE_REQUIRED,),
            )

        reasons = self._physical_blockers(candidate=candidate, request=request)
        if reasons:
            return self._decision(
                candidate.candidate_id,
                RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY,
                reasons,
            )
        return self._decision(
            candidate.candidate_id,
            RetentionDecisionState.PHYSICALLY_ELIGIBLE,
            (RetentionReasonCode.PHYSICAL_PRECONDITIONS_MET,),
        )

    def _physical_blockers(
        self,
        *,
        candidate: RetentionCandidate,
        request: RetentionPlanningRequest,
    ) -> tuple[RetentionReasonCode, ...]:
        reasons: set[RetentionReasonCode] = set()
        enumeration = candidate.enumeration
        if enumeration.coverage is RetentionEnumerationCoverage.INCOMPLETE:
            reasons.add(RetentionReasonCode.ENUMERATION_INCOMPLETE)

        for reference in enumeration.references:
            if reference.scheme not in _KNOWN_SCHEMES:
                reasons.add(RetentionReasonCode.UNKNOWN_REFERENCE_SCHEME)
            if reference.tenant_id != candidate.tenant_id and (
                enumeration.coverage is not RetentionEnumerationCoverage.COMPLETE_GLOBAL
                or not reference.is_released_as_of(request.as_of)
            ):
                reasons.add(RetentionReasonCode.CROSS_TENANT_REFERENCE)
            reason = self._reference_blocker(reference, request.as_of)
            if reason is not None:
                reasons.add(reason)

        if any(
            coverage.state is not RetentionLegalHoldState.RELEASED
            for coverage in candidate.legal_hold_coverage
        ):
            reasons.add(RetentionReasonCode.ACTIVE_LEGAL_HOLD)

        tombstoned_at = candidate.tombstoned_at
        if tombstoned_at is None:
            # ``model_copy(update=...)`` does not re-run Pydantic validators. A
            # future coordinator must never turn such a corrupt in-memory model
            # into eligibility merely because its normal constructor is strict.
            reasons.add(RetentionReasonCode.MISSING_TOMBSTONE_TIMESTAMP)
        elif request.as_of < tombstoned_at + request.policy.physical_grace_period:
            reasons.add(RetentionReasonCode.PHYSICAL_GRACE_OPEN)

        return tuple(sorted(reasons, key=lambda reason: reason.value))

    @staticmethod
    def _reference_blocker(
        reference: RetentionLogicalReference,
        as_of: datetime,
    ) -> RetentionReasonCode | None:
        if reference.lifecycle_state in {
            RetentionReferenceLifecycleState.INDETERMINATE,
            RetentionReferenceLifecycleState.UNKNOWN,
        }:
            return RetentionReasonCode.INDETERMINATE_OR_UNKNOWN_REFERENCE
        if reference.lifecycle_state in {
            RetentionReferenceLifecycleState.ACTIVE,
            RetentionReferenceLifecycleState.PENDING,
        }:
            return {
                RetentionReferenceRole.RUN: RetentionReasonCode.ACTIVE_OR_PENDING_RUN,
                RetentionReferenceRole.STAGE: RetentionReasonCode.ACTIVE_OR_PENDING_STAGE,
                RetentionReferenceRole.EFFECT: RetentionReasonCode.ACTIVE_OR_PENDING_EFFECT,
            }.get(reference.role, RetentionReasonCode.LIVE_LOGICAL_REFERENCE)
        if reference.is_released_as_of(as_of):
            return None
        return {
            RetentionReferenceRole.RECEIPT: RetentionReasonCode.LIVE_RECEIPT_REFERENCE,
            RetentionReferenceRole.LEGAL_HOLD: RetentionReasonCode.LIVE_HOLD_REFERENCE,
            RetentionReferenceRole.RECOVERY: RetentionReasonCode.LIVE_RECOVERY_REFERENCE,
        }.get(reference.role, RetentionReasonCode.LIVE_LOGICAL_REFERENCE)

    @staticmethod
    def _decision(
        candidate_id: str,
        state: RetentionDecisionState,
        reasons: Sequence[RetentionReasonCode],
    ) -> RetentionDecision:
        return RetentionDecision(
            candidate_id=candidate_id,
            state=state,
            reasons=tuple(sorted(set(reasons), key=lambda reason: reason.value)),
        )


__all__ = (
    "RetentionCandidate",
    "RetentionCandidateKind",
    "RetentionCandidateState",
    "RetentionDecision",
    "RetentionDecisionState",
    "RetentionEnumerationCoverage",
    "RetentionLegalHoldCoverage",
    "RetentionLegalHoldScope",
    "RetentionLegalHoldState",
    "RetentionLogicalReference",
    "RetentionPlan",
    "RetentionPlanCursor",
    "RetentionPlanner",
    "RetentionPlanningError",
    "RetentionPlanningErrorCode",
    "RetentionPlanningPolicy",
    "RetentionPlanningRequest",
    "RetentionReasonCode",
    "RetentionReferenceEnumeration",
    "RetentionReferenceLifecycleState",
    "RetentionReferencePresence",
    "RetentionReferenceRole",
)
