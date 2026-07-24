"""Projection entity twins for the Work Ledger (PRD-A1 D2/D3).

Pydantic mirrors of the six api-types entity types
(``packages/api-types/src/ledger.ts``): the projection outputs later endpoints
serve (A3 ``GET /v1/agent/runs/{id}/surfaces``, D-wave decisions, E-wave
receipt). Same field names / optionality as the TypeScript. Values reuse the
enums + value objects from ``ledger_models`` so the vocabulary is defined once.

Note (2026-07-23 close-out): ``Surface`` here is the **ledger entity** (the
richer canvas/B-E-wave entity). A3's ``SurfaceSnapshot`` (the surfaces-fold
output) is a distinct **fold projection** that coexists additively — A3 does not
edit this model. The v2.1 entities added here remain contracts-only in A1.

The legacy v2 projection entities keep tenancy on the run envelope. The v2.1
``Artifact`` is a durable cross-run entity and therefore carries explicit
``org_id`` / ``user_id`` ownership as required by PRD-A1 D3.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import field_validator, model_validator
from copilot_service_contracts.work_ledger import load_work_ledger_contract

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    EffectReceiptRefCodec,
    OperationArgsRefCodec,
    ProposalUriCodec,
    WorkspaceTargetRefCodec,
)
from agent_runtime.surfaces_v2.ledger_models import (
    AgentHold,
    ArtifactAuthor,
    ArtifactIdText,
    ArtifactKind,
    ArtifactPresentationPreference,
    DecisionActor,
    DecisionKind,
    DecisionScope,
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectOutcome,
    EffectStageIdText,
    EffectStageStatus,
    GateKind,
    LedgerEventType,
    LedgerOpRef,
    OperationIdText,
    OperationOutcome,
    OperationResultKind,
    Producer,
    RevisionAuthor,
    SafeNonNegativeInt,
    SafePositiveInt,
    Sha256Hex,
    SurfaceKind,
    SurfaceSubjectType,
    UsagePurpose,
    ViewBasis,
    ViewKeep,
    ViewTier,
)

_REFERENCE_MAX_LENGTH = int(
    dict(load_work_ledger_contract()["references"])["max_length"]
)


class ReceiptAttribution(StrEnum):
    """How a receipt row is attributed (FR-E2 wording, wire-safe; A1-defined).

    Not a ledger event type and not in the SSOT ``enums`` block — a
    receipt-format construct the E-wave receipt fold assigns per row.
    """

    AUTO_RAN = "auto_ran"
    APPROVED = "approved"
    HELD = "held"
    REJECTED = "rejected"
    AUTO_APPLIED = "auto_applied"
    NO_VIEW_FIT = "no_view_fit"


class Revision(RuntimeContract):
    """One staged-write revision (draft snapshot), folded from ``revision.added``."""

    rev: int
    author: RevisionAuthor
    diff_ref: str
    created_at: str
    ledger_id: str


class Decision(RuntimeContract):
    """One recorded decision, folded from ``decision.recorded``."""

    decision: DecisionKind
    scope: DecisionScope
    actor: DecisionActor
    decided_at: str
    ledger_id: str


class SurfaceView(RuntimeContract):
    """The current view state of a surface (folded from ``view.*`` events)."""

    tier: ViewTier
    basis: ViewBasis
    spec_ref: str | None = None
    preference: ViewKeep | None = None


class Surface(RuntimeContract):
    """A live artifact surface, folded from ``surface.created`` + ``view.*``.

    ``view`` is required-nullable (present, ``None`` until a view is derived),
    mirroring the ts ``view: {...} | null``.
    """

    surface_id: str
    run_id: str
    kind: SurfaceKind
    title: str
    source: LedgerOpRef
    payload_ref: str
    ledger_id: str
    created_at: str
    view: SurfaceView | None


class StagedWrite(RuntimeContract):
    """A staged write with its revisions + decisions, folded from ``write.*``."""

    stage_id: str
    surface_id: str
    run_id: str
    target: LedgerOpRef
    proposal_ref: str
    rows: int | None
    agent_holds: tuple[AgentHold, ...]
    revisions: tuple[Revision, ...]
    decisions: tuple[Decision, ...]
    latest_rev: int


class ArtifactIntent(RuntimeContract):
    kind: ArtifactKind
    title: str | None = None
    media_type: str | None = None
    suggested_filename: str | None = None
    presentation_preference: ArtifactPresentationPreference


class OperationRequest(RuntimeContract):
    operation_id: OperationIdText
    run_id: str
    producer: Producer
    capability: str
    op: str
    canonical_args_ref: str
    args_digest: Sha256Hex
    requested_at: str
    artifact_intent: ArtifactIntent | None = None
    effect_hint: EffectClass | None = None
    parent_operation_id: OperationIdText | None = None

    @field_validator("canonical_args_ref")
    @classmethod
    def _valid_args_ref(cls, value: str) -> str:
        OperationArgsRefCodec.parse(value)
        return value

    @model_validator(mode="after")
    def _args_ref_matches_operation(self) -> OperationRequest:
        parsed = OperationArgsRefCodec.parse(self.canonical_args_ref)
        if parsed.operation_id != self.operation_id:
            raise ValueError("canonical_args_ref must reference operation_id")
        if self.parent_operation_id == self.operation_id:
            raise ValueError("parent_operation_id must differ from operation_id")
        return self


class OperationDescriptor(RuntimeContract):
    capability: str
    op: str
    executor: EffectExecutorKind
    effect_class: EffectClass
    result_kind: OperationResultKind
    supports_prepare: bool
    supports_reconcile: bool
    required_gate_kinds: tuple[GateKind, ...]
    max_inline_result_bytes: SafeNonNegativeInt


class OperationDisposition(RuntimeContract):
    operation_id: OperationIdText
    outcome: OperationOutcome
    artifact_ids: tuple[ArtifactIdText, ...]
    stage_ids: tuple[EffectStageIdText, ...]
    activity_ref: str | None = None
    agent_summary: str
    retryable: bool

    @field_validator("activity_ref")
    @classmethod
    def _activity_ref_not_physical_path(cls, value: str | None) -> str | None:
        _reject_physical_reference(value, "activity_ref")
        return value


class Artifact(RuntimeContract):
    artifact_id: ArtifactIdText
    org_id: str
    user_id: str
    conversation_id: str
    run_id: str
    kind: ArtifactKind
    title: str
    media_type: str
    current_revision: SafePositiveInt
    created_by: ArtifactAuthor
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class ArtifactRevision(RuntimeContract):
    artifact_id: ArtifactIdText
    revision: SafePositiveInt
    parent_revision: SafePositiveInt | None = None
    content_ref: str
    content_digest: Sha256Hex
    byte_size: SafeNonNegativeInt
    author: ArtifactAuthor
    source_ref: str | None = None
    created_at: str

    @field_validator("content_ref")
    @classmethod
    def _valid_content_ref(cls, value: str) -> str:
        ArtifactContentRefCodec.parse(value)
        return value

    @field_validator("source_ref")
    @classmethod
    def _source_ref_not_physical_path(cls, value: str | None) -> str | None:
        _reject_physical_reference(value, "source_ref")
        return value

    @model_validator(mode="after")
    def _revision_refs_match(self) -> ArtifactRevision:
        parsed = ArtifactContentRefCodec.parse(self.content_ref)
        if parsed.artifact_id != self.artifact_id or parsed.revision != self.revision:
            raise ValueError("content_ref must reference artifact_id and revision")
        if self.parent_revision is not None and self.parent_revision >= self.revision:
            raise ValueError("parent_revision must be less than revision")
        return self


class SurfaceSubject(RuntimeContract):
    subject_type: SurfaceSubjectType
    subject_id: str


class EffectTarget(RuntimeContract):
    executor: EffectExecutorKind
    capability: str
    op: str
    target_ref: str
    precondition_ref: str | None = None
    display_label: str

    @field_validator("target_ref", "precondition_ref")
    @classmethod
    def _reference_not_physical_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _validate_target_reference(value)
        return value


class ProposalRef(RuntimeContract):
    proposal_ref: str
    proposal_digest: Sha256Hex
    media_type: str
    byte_size: SafeNonNegativeInt | None = None

    @field_validator("proposal_ref")
    @classmethod
    def _valid_proposal_ref(cls, value: str) -> str:
        ProposalUriCodec.parse(value)
        return value


class EffectStage(RuntimeContract):
    stage_id: EffectStageIdText
    operation_id: OperationIdText
    run_id: str
    executor: EffectExecutorKind
    target: EffectTarget
    proposal: ProposalRef
    revision: SafePositiveInt
    status: EffectStageStatus
    policy_snapshot_ref: str
    created_at: str
    updated_at: str

    @field_validator("policy_snapshot_ref")
    @classmethod
    def _policy_ref_not_physical_path(cls, value: str) -> str:
        _reject_physical_reference(value, "policy_snapshot_ref")
        return value

    @model_validator(mode="after")
    def _stage_refs_match(self) -> EffectStage:
        parsed = ProposalUriCodec.parse(self.proposal.proposal_ref)
        if parsed.stage_id != self.stage_id or parsed.revision != self.revision:
            raise ValueError("proposal_ref must reference stage_id and revision")
        if self.target.executor is not self.executor:
            raise ValueError("target executor must match stage executor")
        if self.executor is EffectExecutorKind.WORKSPACE:
            WorkspaceTargetRefCodec.parse(self.target.target_ref)
        return self


class EffectDecision(RuntimeContract):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    decision: EffectDecisionKind
    actor: EffectActor
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    decided_at: str
    ledger_id: str


class EffectExecutionRequest(RuntimeContract):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    idempotency_key: str
    target_ref: str
    target_digest: Sha256Hex
    proposal_ref: str
    proposal_digest: Sha256Hex
    actor: EffectActor
    decision_ledger_id: str

    @field_validator("proposal_ref")
    @classmethod
    def _valid_proposal_ref(cls, value: str) -> str:
        ProposalUriCodec.parse(value)
        return value

    @field_validator("target_ref")
    @classmethod
    def _target_ref_not_physical_path(cls, value: str) -> str:
        _reject_physical_reference(value, "target_ref")
        return value

    @model_validator(mode="after")
    def _proposal_matches_stage(self) -> EffectExecutionRequest:
        parsed = ProposalUriCodec.parse(self.proposal_ref)
        if parsed.stage_id != self.stage_id or parsed.revision != self.revision:
            raise ValueError("proposal_ref must reference stage_id and revision")
        return self


class EffectExecutionResult(RuntimeContract):
    outcome: EffectOutcome
    receipt_ref: str | None = None
    result_digest: Sha256Hex | None = None
    retryable: bool
    safe_message: str | None = None

    @field_validator("receipt_ref")
    @classmethod
    def _valid_receipt_ref(cls, value: str | None) -> str | None:
        if value is not None:
            EffectReceiptRefCodec.parse(value)
        return value


def _reject_physical_reference(value: str | None, field_name: str) -> None:
    """Keep host filesystem paths out of immutable public wire entities."""

    if value is None:
        return
    lowered = value.lower()
    if (
        not value
        or len(value) > _REFERENCE_MAX_LENGTH
        or value != value.strip()
        or value.startswith(("/", "~", "\\"))
        or lowered.startswith(("file://", "filesystem://"))
        or (len(value) >= 3 and value[1:3] in {":\\", ":/"})
    ):
        raise ValueError(f"{field_name} must not contain a physical host path")


def _validate_target_reference(value: str) -> None:
    if (
        not value
        or len(value) > _REFERENCE_MAX_LENGTH
        or value != value.strip()
        or "://" not in value
        or value.startswith(("/", "~", "\\"))
        or value.lower().startswith(("file://", "filesystem://"))
        or (len(value) >= 3 and value[1:3] in {":\\", ":/"})
        or any(segment in {".", ".."} for segment in value.split("/"))
    ):
        raise ValueError("effect references must be opaque URI references")


class UsageRecord(RuntimeContract):
    """One metered usage row, folded from ``usage.recorded`` (FR-G)."""

    purpose: UsagePurpose
    model: str
    tokens_in: int
    tokens_out: int
    run_id: str
    conversation_id: str
    surface_id: str | None = None
    created_at: str
    ledger_id: str


class RunReceiptTiles(RuntimeContract):
    """The receipt's headline counters."""

    reads_auto_ran: int
    writes_proposed: int
    writes_approved: int
    holds_untouched: int


class RunReceiptRow(RuntimeContract):
    """One line of a run receipt (fold, not narrative)."""

    ledger_id: str
    event_type: LedgerEventType
    title: str
    attribution: ReceiptAttribution
    at: str


class RunReceipt(RuntimeContract):
    """The folded run receipt (E-wave), mirroring the ts ``RunReceipt``."""

    run_id: str
    surface_id: str
    fold_ref: str
    generated_at: str
    tiles: RunReceiptTiles
    rows: tuple[RunReceiptRow, ...]


__all__ = [
    "Artifact",
    "ArtifactIntent",
    "ArtifactRevision",
    "Decision",
    "EffectDecision",
    "EffectExecutionRequest",
    "EffectExecutionResult",
    "EffectStage",
    "EffectTarget",
    "OperationDescriptor",
    "OperationDisposition",
    "OperationRequest",
    "ProposalRef",
    "ReceiptAttribution",
    "Revision",
    "RunReceipt",
    "RunReceiptRow",
    "RunReceiptTiles",
    "StagedWrite",
    "Surface",
    "SurfaceSubject",
    "SurfaceView",
    "UsageRecord",
]
