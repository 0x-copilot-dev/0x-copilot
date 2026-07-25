"""Pure, fail-closed D12 repair/reconciliation candidate planning.

This module is deliberately *not* a job runner.  A later adapter may assemble a
trusted, redacted snapshot from durable stores and consume the resulting plan, but
this foundation never opens a store, resolves a reference, deletes a resource,
creates an approval, enqueues work, or calls an effect executor.

The public result is intentionally small and safe:

* opaque candidate, tenant, snapshot, and evidence identifiers only;
* closed action and reason enums only; and
* candidate/withheld decisions, never a physical cleanup or effect command.

In particular, an effect-reconcile candidate identifies an already-claimed,
terminal-stage attempt that a future runner may hand to the existing safe
reconciliation path.  It does not authorize, apply, or resend an external
effect.  Incomplete graph/evidence, an unknown reference scheme, a live (or
unknown) legal hold, a foreign tenant record, and a nonterminal uncertain effect
all fail closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Final

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceScheme


_OPAQUE_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
)
_SAFE_SCHEME: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_KNOWN_REFERENCE_SCHEMES: Final[frozenset[str]] = frozenset(
    scheme.value for scheme in LifecycleReferenceScheme
)


def _opaque_identifier(value: str) -> str:
    """Accept an opaque handle, never a path, URI, body, or display value."""

    if _OPAQUE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("identifier must be a safe opaque token")
    return value


def _aware_utc(value: datetime) -> datetime:
    """Normalize a snapshot timestamp so planning is stable across callers."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


class RepairCandidateKind(StrEnum):
    """Closed D12 repair families represented by trusted snapshot records."""

    METADATA_OUTBOX = "metadata_outbox"
    ORPHAN_ARTIFACT_OR_TEMP = "orphan_artifact_or_temp"
    STALE_PREPARED_RESOURCE = "stale_prepared_resource"
    RECEIPT_SOURCE_PROJECTION = "receipt_source_projection"
    USAGE_EDGE = "usage_edge"
    AUDIT_VERIFICATION = "audit_verification"
    EFFECT_RECONCILIATION = "effect_reconciliation"


class RepairAction(StrEnum):
    """Candidate-only actions; none is an execution, approval, or queue command."""

    METADATA_OUTBOX_REPAIR_CANDIDATE = "metadata_outbox_repair_candidate"
    ORPHAN_CLEANUP_CANDIDATE = "orphan_cleanup_candidate"
    STALE_PREPARED_CLEANUP_CANDIDATE = "stale_prepared_cleanup_candidate"
    RECEIPT_SOURCE_REBUILD_CANDIDATE = "receipt_source_rebuild_candidate"
    USAGE_EDGE_REPAIR_CANDIDATE = "usage_edge_repair_candidate"
    AUDIT_VERIFICATION_SAMPLE = "audit_verification_sample"
    EFFECT_RECONCILE_CANDIDATE = "effect_reconcile_candidate"


class RepairDecisionState(StrEnum):
    """A future runner can consume only explicit candidates, never a held row."""

    CANDIDATE = "candidate"
    WITHHELD = "withheld"


class RepairReasonCode(StrEnum):
    """Closed, content- and path-safe explanations for a decision."""

    VERIFIED_REPAIR_SIGNAL = "verified_repair_signal"
    INCOMPLETE_GRAPH = "incomplete_graph"
    UNKNOWN_REFERENCE_SCHEME = "unknown_reference_scheme"
    LIVE_LEGAL_HOLD = "live_legal_hold"
    MISSING_EVIDENCE = "missing_evidence"
    NONTERMINAL_RESOURCE = "nonterminal_resource"
    NONTERMINAL_UNCERTAIN_EFFECT = "nonterminal_uncertain_effect"
    EFFECT_NOT_UNCERTAIN = "effect_not_uncertain"


class RepairGraphCoverage(StrEnum):
    """The completeness assertion supplied by the trusted snapshot collector."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class RepairLegalHoldState(StrEnum):
    """Only an explicit no-hold result permits a repair candidate."""

    NONE = "none"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class RepairEvidenceState(StrEnum):
    """Whether a collector supplied enough redacted proof for this candidate."""

    VERIFIED = "verified"
    MISSING = "missing"
    CONFLICTING = "conflicting"


class RepairOwnerState(StrEnum):
    """Owner terminality used by resource cleanup and uncertain-effect guards."""

    ACTIVE = "active"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


class RepairEffectState(StrEnum):
    """Redacted durable-claim states relevant to an effect reconciliation sweep."""

    NOT_APPLICABLE = "not_applicable"
    CLAIMED = "claimed"
    INDETERMINATE = "indeterminate"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RepairPlanningErrorCode(StrEnum):
    """Safe input-boundary errors.  They never include a supplied identifier."""

    CURSOR_SCOPE_MISMATCH = "cursor_scope_mismatch"
    CANDIDATE_TENANT_MISMATCH = "candidate_tenant_mismatch"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


class RepairPlanningError(ValueError):
    """Fail-closed planning error with a stable, non-disclosing message."""

    def __init__(self, code: RepairPlanningErrorCode) -> None:
        self.code = code
        super().__init__("repair planning input is invalid")


class RepairSnapshotRecord(RuntimeContract):
    """One trusted, redacted D12 observation.

    ``reference_scheme`` remains a string rather than an enum so a newly
    introduced or unregistered scheme reaches the planner as a withheld result
    instead of being silently discarded by input parsing.  The record carries
    no resource path, URI, storage key, body, decision, or provider payload.
    """

    candidate_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    kind: RepairCandidateKind
    reference_scheme: str = Field(min_length=1, max_length=64)
    graph_coverage: RepairGraphCoverage
    legal_hold: RepairLegalHoldState
    evidence_state: RepairEvidenceState
    evidence_id: str | None = Field(default=None, min_length=1, max_length=256)
    owner_state: RepairOwnerState = RepairOwnerState.UNKNOWN
    effect_state: RepairEffectState = RepairEffectState.NOT_APPLICABLE
    reconcile_supported: bool | None = None
    quiet_period_elapsed: bool | None = None

    @field_validator("candidate_id", "tenant_id", "evidence_id")
    @classmethod
    def _safe_identifier(cls, value: str | None) -> str | None:
        return None if value is None else _opaque_identifier(value)

    @field_validator("reference_scheme")
    @classmethod
    def _safe_scheme(cls, value: str) -> str:
        if _SAFE_SCHEME.fullmatch(value) is None:
            raise ValueError("reference_scheme must be a safe canonical token")
        return value


class RepairPlanCursor(RuntimeContract):
    """Exclusive, snapshot-bound keyset cursor for retry-safe plan pages."""

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    after_candidate_id: str = Field(min_length=1, max_length=256)

    @field_validator("tenant_id", "snapshot_id", "after_candidate_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)


class RepairPlanningRequest(RuntimeContract):
    """An immutable, caller-owned snapshot input to the pure candidate fold."""

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    as_of: datetime
    records: tuple[RepairSnapshotRecord, ...] = ()
    cursor: RepairPlanCursor | None = None
    limit: int = Field(default=100, ge=1, le=500)

    @field_validator("tenant_id", "snapshot_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class RepairDecision(RuntimeContract):
    """A one-row repair candidate or withheld result; never an executable command."""

    candidate_id: str = Field(min_length=1, max_length=256)
    state: RepairDecisionState
    action: RepairAction | None = None
    reasons: tuple[RepairReasonCode, ...]

    @field_validator("candidate_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @model_validator(mode="after")
    def _state_and_action_are_consistent(self) -> RepairDecision:
        if not self.reasons:
            raise ValueError("repair decisions require at least one reason code")
        if self.state is RepairDecisionState.CANDIDATE and self.action is None:
            raise ValueError("repair candidates require an action")
        if self.state is RepairDecisionState.WITHHELD and self.action is not None:
            raise ValueError("withheld repair decisions cannot carry an action")
        return self


class RepairPlan(RuntimeContract):
    """A deterministic, opaque, keyset-paginated D12 candidate plan."""

    tenant_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    as_of: datetime
    decisions: tuple[RepairDecision, ...]
    next_cursor: RepairPlanCursor | None = None
    has_more: bool

    @field_validator("tenant_id", "snapshot_id")
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        return _opaque_identifier(value)

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime) -> datetime:
        return _aware_utc(value)


_ACTION_BY_KIND: Final[dict[RepairCandidateKind, RepairAction]] = {
    RepairCandidateKind.METADATA_OUTBOX: RepairAction.METADATA_OUTBOX_REPAIR_CANDIDATE,
    RepairCandidateKind.ORPHAN_ARTIFACT_OR_TEMP: RepairAction.ORPHAN_CLEANUP_CANDIDATE,
    RepairCandidateKind.STALE_PREPARED_RESOURCE: RepairAction.STALE_PREPARED_CLEANUP_CANDIDATE,
    RepairCandidateKind.RECEIPT_SOURCE_PROJECTION: RepairAction.RECEIPT_SOURCE_REBUILD_CANDIDATE,
    RepairCandidateKind.USAGE_EDGE: RepairAction.USAGE_EDGE_REPAIR_CANDIDATE,
    RepairCandidateKind.AUDIT_VERIFICATION: RepairAction.AUDIT_VERIFICATION_SAMPLE,
    RepairCandidateKind.EFFECT_RECONCILIATION: RepairAction.EFFECT_RECONCILE_CANDIDATE,
}
_CLEANUP_KINDS: Final[frozenset[RepairCandidateKind]] = frozenset(
    {
        RepairCandidateKind.ORPHAN_ARTIFACT_OR_TEMP,
        RepairCandidateKind.STALE_PREPARED_RESOURCE,
    }
)
_UNCERTAIN_EFFECT_STATES: Final[frozenset[RepairEffectState]] = frozenset(
    {RepairEffectState.CLAIMED, RepairEffectState.INDETERMINATE}
)


class RepairPlanner:
    """Pure D12 candidate fold over a caller-provided trusted snapshot.

    This planner intentionally makes no action durable.  A caller must run a
    separate authorization, persistence, and executor-specific reconciliation
    step before any candidate can have an effect outside the snapshot.
    """

    def plan(self, request: RepairPlanningRequest) -> RepairPlan:
        """Return one stable page without resolving or mutating any resource."""

        self._validate_request_scope(request)
        ordered = tuple(sorted(request.records, key=lambda row: row.candidate_id))
        candidate_ids = tuple(row.candidate_id for row in ordered)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise RepairPlanningError(RepairPlanningErrorCode.DUPLICATE_CANDIDATE)

        after_candidate_id = (
            request.cursor.after_candidate_id if request.cursor is not None else None
        )
        remaining = tuple(
            record
            for record in ordered
            if after_candidate_id is None or record.candidate_id > after_candidate_id
        )
        page = remaining[: request.limit]
        has_more = len(remaining) > len(page)
        next_cursor = (
            RepairPlanCursor(
                tenant_id=request.tenant_id,
                snapshot_id=request.snapshot_id,
                after_candidate_id=page[-1].candidate_id,
            )
            if has_more and page
            else None
        )
        return RepairPlan(
            tenant_id=request.tenant_id,
            snapshot_id=request.snapshot_id,
            as_of=request.as_of,
            decisions=tuple(self._decide(record) for record in page),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def _validate_request_scope(request: RepairPlanningRequest) -> None:
        if request.cursor is not None and (
            request.cursor.tenant_id != request.tenant_id
            or request.cursor.snapshot_id != request.snapshot_id
        ):
            raise RepairPlanningError(RepairPlanningErrorCode.CURSOR_SCOPE_MISMATCH)
        if any(record.tenant_id != request.tenant_id for record in request.records):
            raise RepairPlanningError(RepairPlanningErrorCode.CANDIDATE_TENANT_MISMATCH)

    def _decide(self, record: RepairSnapshotRecord) -> RepairDecision:
        reasons = self._blockers(record)
        if reasons:
            return RepairDecision(
                candidate_id=record.candidate_id,
                state=RepairDecisionState.WITHHELD,
                reasons=reasons,
            )
        return RepairDecision(
            candidate_id=record.candidate_id,
            state=RepairDecisionState.CANDIDATE,
            action=_ACTION_BY_KIND[record.kind],
            reasons=(RepairReasonCode.VERIFIED_REPAIR_SIGNAL,),
        )

    @staticmethod
    def _blockers(record: RepairSnapshotRecord) -> tuple[RepairReasonCode, ...]:
        reasons: set[RepairReasonCode] = set()
        if record.graph_coverage is not RepairGraphCoverage.COMPLETE:
            reasons.add(RepairReasonCode.INCOMPLETE_GRAPH)
        if record.reference_scheme not in _KNOWN_REFERENCE_SCHEMES:
            reasons.add(RepairReasonCode.UNKNOWN_REFERENCE_SCHEME)
        if record.legal_hold is RepairLegalHoldState.ACTIVE:
            reasons.add(RepairReasonCode.LIVE_LEGAL_HOLD)
        elif record.legal_hold is not RepairLegalHoldState.NONE:
            reasons.add(RepairReasonCode.MISSING_EVIDENCE)
        if (
            record.evidence_state is not RepairEvidenceState.VERIFIED
            or record.evidence_id is None
        ):
            reasons.add(RepairReasonCode.MISSING_EVIDENCE)

        if (
            record.kind in _CLEANUP_KINDS
            and record.owner_state is not RepairOwnerState.TERMINAL
        ):
            reasons.add(RepairReasonCode.NONTERMINAL_RESOURCE)

        if record.kind is RepairCandidateKind.EFFECT_RECONCILIATION:
            if record.effect_state not in _UNCERTAIN_EFFECT_STATES:
                reasons.add(RepairReasonCode.EFFECT_NOT_UNCERTAIN)
            if record.owner_state is not RepairOwnerState.TERMINAL:
                reasons.add(RepairReasonCode.NONTERMINAL_UNCERTAIN_EFFECT)
            if (
                record.reconcile_supported is not True
                or record.quiet_period_elapsed is not True
            ):
                reasons.add(RepairReasonCode.MISSING_EVIDENCE)

        return tuple(sorted(reasons, key=lambda reason: reason.value))


__all__ = (
    "RepairAction",
    "RepairCandidateKind",
    "RepairDecision",
    "RepairDecisionState",
    "RepairEffectState",
    "RepairEvidenceState",
    "RepairGraphCoverage",
    "RepairLegalHoldState",
    "RepairOwnerState",
    "RepairPlan",
    "RepairPlanCursor",
    "RepairPlanner",
    "RepairPlanningError",
    "RepairPlanningErrorCode",
    "RepairPlanningRequest",
    "RepairReasonCode",
    "RepairSnapshotRecord",
)
