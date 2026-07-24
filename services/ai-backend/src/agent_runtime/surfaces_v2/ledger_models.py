"""Pydantic mirror of the Work Ledger vocabulary (SDR §5, PRD-A1 D1/D3).

The JSON in ``copilot_service_contracts.work_ledger`` is the single source of
truth. These models mirror it; cross-language parity tests pin the pydantic
models, that JSON, and the ``packages/api-types`` TypeScript types together so
none of the three can drift silently. ``WorkLedgerVocabulary.validate_payload``
is the single validation chokepoint: it maps an event-type string to its payload
model and validates an untrusted dict through it.

Every payload extends ``LedgerPayload`` (a ``RuntimeContract``: frozen,
``extra="forbid"`` — extra/malformed keys fail as a typed
``pydantic.ValidationError``). Existing v2 producers consume this contract;
PRD-A1 v2.1 adds vocabulary only and does not add a new producer.

Wire-shape tenancy rule: no ``org_id`` / ``user_id`` on any payload — attribution
rides the run envelope server-side (mirrors ``RuntimeEventDraft`` vs
``RuntimeEventEnvelope``).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from copilot_service_contracts.work_ledger import load_work_ledger_contract

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactContentRefCodec,
    EffectReceiptRefCodec,
    ProposalUriCodec,
    WorkspaceTargetRefCodec,
)


# ---------------------------------------------------------------------------
# Event-type enum (order == ``LEDGER_EVENT_TYPES`` in the SSOT JSON)
# ---------------------------------------------------------------------------


class LedgerEventType(StrEnum):
    """Ledger event types, in append-only contract order."""

    GATE_OPENED = "gate.opened"
    GATE_RESOLVED = "gate.resolved"
    ACTION_CLASSIFIED = "action.classified"
    READ_EXECUTED = "read.executed"
    SURFACE_CREATED = "surface.created"
    VIEW_DERIVED = "view.derived"
    VIEW_PREFERENCE = "view.preference"
    SHAPE_REQUESTED = "shape.requested"
    SHAPE_RESOLVED = "shape.resolved"
    WRITE_STAGED = "write.staged"
    REVISION_ADDED = "revision.added"
    DECISION_RECORDED = "decision.recorded"
    WRITE_APPLIED = "write.applied"
    USAGE_RECORDED = "usage.recorded"
    RECEIPT_EMITTED = "receipt.emitted"
    OPERATION_REQUESTED = "operation.requested"
    OPERATION_CLASSIFIED = "operation.classified"
    OPERATION_COMPLETED = "operation.completed"
    OPERATION_FAILED = "operation.failed"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_REVISED = "artifact.revised"
    ARTIFACT_PROMOTED = "artifact.promoted"
    ARTIFACT_PRESENTATION_DECIDED = "artifact.presentation_decided"
    EFFECT_STAGED = "effect.staged"
    EFFECT_REVISED = "effect.revised"
    EFFECT_DECISION_RECORDED = "effect.decision_recorded"
    EFFECT_CLAIMED = "effect.claimed"
    EFFECT_APPLIED = "effect.applied"
    EFFECT_INDETERMINATE = "effect.indeterminate"
    EFFECT_RECONCILED = "effect.reconciled"
    GATE_OPENED_V2 = "gate.opened.v2"
    GATE_RESOLVED_V2 = "gate.resolved.v2"


# ---------------------------------------------------------------------------
# Value enums (one StrEnum per ``enums`` key in the SSOT JSON, values verbatim)
# ---------------------------------------------------------------------------


class GateAuthState(StrEnum):
    MISSING = "missing"
    EXPIRED = "expired"
    INSUFFICIENT = "insufficient"


class GateOutcome(StrEnum):
    CONNECTED = "connected"
    CANCELLED = "cancelled"


class WritePolicy(StrEnum):
    ASK_FIRST = "ask_first"
    ALLOW_ALWAYS = "allow_always"


class ActionClass(StrEnum):
    READ = "read"
    WRITE = "write"
    UNKNOWN = "unknown"


class ClassificationBasis(StrEnum):
    CATALOG = "catalog"
    ANNOTATION = "annotation"
    DEFAULT = "default"


class SurfaceKind(StrEnum):
    RECORD = "record"
    MESSAGE = "message"
    TABLE = "table"
    CALL = "call"
    RAW = "raw"
    RECEIPT = "receipt"
    GATE = "gate"


class ViewTier(StrEnum):
    RAW = "raw"
    GENERIC = "generic"
    SHAPED = "shaped"


class ViewBasis(StrEnum):
    SCHEMA = "schema"
    REGISTRY = "registry"
    GENERATED = "generated"


class ViewKeep(StrEnum):
    GENERIC = "generic"
    SHAPED = "shaped"


class ShapeOutcome(StrEnum):
    """Outcome of a user-invited ``shape.requested`` attempt (PRD-B4, SDR §5)."""

    SHAPED = "shaped"
    NO_FIT = "no_fit"


class RevisionAuthor(StrEnum):
    AGENT = "agent"
    USER = "user"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    HOLD = "hold"
    RESTORE = "restore"


class DecisionActor(StrEnum):
    USER = "user"
    POLICY = "policy"


class ApplyResult(StrEnum):
    APPLIED = "applied"
    PARTIAL = "partial"
    FAILED = "failed"


class UsagePurpose(StrEnum):
    RUN = "run"
    SUBAGENT = "subagent"
    VIEW_SHAPING = "view_shaping"
    SHAPE_REQUEST = "shape_request"


class Producer(StrEnum):
    MODEL = "model"
    SUBAGENT = "subagent"
    USER = "user"
    SYSTEM = "system"


class EffectClass(StrEnum):
    NONE = "none"
    INTERNAL_REVERSIBLE = "internal_reversible"
    EXTERNAL_REVERSIBLE = "external_reversible"
    EXTERNAL_DESTRUCTIVE = "external_destructive"
    UNKNOWN = "unknown"


class OperationClassificationBasis(StrEnum):
    DESCRIPTOR = "descriptor"
    CATALOG = "catalog"
    PROVIDER_ANNOTATION = "provider_annotation"
    POLICY_OVERRIDE = "policy_override"
    DEFAULT = "default"


class OperationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    STAGED = "staged"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OperationResultKind(StrEnum):
    NONE = "none"
    ARTIFACT = "artifact"
    ACTIVITY = "activity"
    ARTIFACT_AND_ACTIVITY = "artifact_and_activity"


class ArtifactKind(StrEnum):
    CODE = "code"
    DOCUMENT = "document"
    DATASET = "dataset"
    FILE = "file"


class ArtifactAuthor(StrEnum):
    MODEL = "model"
    SUBAGENT = "subagent"
    USER = "user"
    SYSTEM = "system"
    IMPORT = "import"


class ArtifactPresentationPreference(StrEnum):
    AUTO = "auto"
    CANVAS = "canvas"
    CHAT_CARD = "chat_card"
    NONE = "none"


class PresentationDecision(StrEnum):
    CANVAS = "canvas"
    CHAT_CARD = "chat_card"
    ACTIVITY_ONLY = "activity_only"
    NONE = "none"


class SurfaceSubjectType(StrEnum):
    ARTIFACT = "artifact"
    STAGE = "stage"
    RECORD = "record"
    RECEIPT = "receipt"
    GATE = "gate"


class EffectPolicy(StrEnum):
    AUTO = "auto"
    ASK = "ask"
    REQUIRE = "require"
    BLOCK = "block"


class EffectDecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    RESTORE = "restore"
    CANCEL = "cancel"


class EffectActor(StrEnum):
    USER = "user"
    POLICY = "policy"
    SYSTEM = "system"


class EffectOutcome(StrEnum):
    APPLIED = "applied"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"
    ALREADY_APPLIED = "already_applied"
    PRECONDITION_DRIFT = "precondition_drift"


class EffectExecutorKind(StrEnum):
    MCP = "mcp"
    WORKSPACE = "workspace"
    BROWSER = "browser"
    SANDBOX = "sandbox"
    BUILTIN = "builtin"


class EffectStageStatus(StrEnum):
    STAGED = "staged"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    CLAIMED = "claimed"
    APPLIED = "applied"
    PARTIAL = "partial"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"
    PRECONDITION_DRIFT = "precondition_drift"


class GateKind(StrEnum):
    AUTHENTICATION = "authentication"
    GRANT = "grant"
    CAPABILITY = "capability"
    POLICY = "policy"


class GateDecision(StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    CANCELLED = "cancelled"


_WORK_LEDGER_CONTRACT = load_work_ledger_contract()
_CROSS_LANGUAGE_MAX_SAFE_INTEGER = int(
    dict(_WORK_LEDGER_CONTRACT["digests"])["max_safe_integer"]
)
_REFERENCE_MAX_LENGTH = int(dict(_WORK_LEDGER_CONTRACT["references"])["max_length"])
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafePositiveInt = Annotated[int, Field(ge=1, le=_CROSS_LANGUAGE_MAX_SAFE_INTEGER)]
SafeNonNegativeInt = Annotated[int, Field(ge=0, le=_CROSS_LANGUAGE_MAX_SAFE_INTEGER)]
OperationIdText = Annotated[
    str,
    Field(
        pattern=r"^op_[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
ArtifactIdText = Annotated[
    str,
    Field(
        pattern=r"^art_[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
EffectStageIdText = Annotated[
    str,
    Field(
        pattern=r"^stg_[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]


def _claim_id_without_traversal(value: str) -> str:
    if ".." in value:
        raise ValueError("claim_id must not contain traversal")
    return value


ClaimIdText = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$"),
    AfterValidator(_claim_id_without_traversal),
]


# ---------------------------------------------------------------------------
# Shared value objects (reused by payloads and by the entity twins)
# ---------------------------------------------------------------------------


class LedgerOpRef(RuntimeContract):
    """The connector server + operation an action / surface targets."""

    connector: str
    op: str


class AgentHold(RuntimeContract):
    """A row the agent staged but deliberately withheld, with its reason."""

    row_key: str
    reason: str


class RowOutcome(StrEnum):
    APPLIED = "applied"
    FAILED = "failed"


class RowFieldChange(RuntimeContract):
    field: str
    old: object | None = None
    new: object | None = None


class StagedRow(RuntimeContract):
    row_key: str
    title: str
    target_args: dict[str, object] | None = None
    changes: tuple[RowFieldChange, ...]


class RevisionRowset(RuntimeContract):
    rows: tuple[StagedRow, ...]


class WriteAppliedRowResult(RuntimeContract):
    row_key: str
    outcome: RowOutcome
    detail: str | None = None


class ViewGen(RuntimeContract):
    """Generation provenance for a shaped view (``view.derived.gen``)."""

    model: str
    ms: NonNegativeInt


class DecisionScope(RuntimeContract):
    """Exactly one of ``{rev}`` (single artifact) or ``{row_keys}`` (row set)."""

    rev: PositiveInt | None = None
    row_keys: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> DecisionScope:
        has_rev = self.rev is not None
        has_rows = self.row_keys is not None
        if has_rev == has_rows:
            raise ValueError(_Messages.DECISION_SCOPE_ONE_OF)
        return self


# ---------------------------------------------------------------------------
# Payload models (one per event type; fields in SSOT ``required`` order)
# ---------------------------------------------------------------------------


class LedgerPayload(RuntimeContract):
    """Base for all v2 ledger payloads: versioned from day one (SDR §5).

    ``v`` is required with no default on purpose — a defaulted field is dropped
    from ``model_json_schema()["required"]``, which would break the parity pin
    against the SSOT JSON (``v`` is first in every event's ``required`` array).
    """

    v: Literal[1]


class GateOpenedPayload(LedgerPayload):
    gate_id: str
    connector: str
    purpose: str
    scopes: tuple[str, ...]
    auth_state: GateAuthState


class GateResolvedPayload(LedgerPayload):
    gate_id: str
    outcome: GateOutcome
    write_policy: WritePolicy | None = None


class ActionClassifiedPayload(LedgerPayload):
    # ``class`` is a Python keyword; the wire key stays SDR-verbatim via alias.
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    call_id: str
    connector: str
    op: str
    action_class: ActionClass = Field(alias="class")
    basis: ClassificationBasis


class ReadExecutedPayload(LedgerPayload):
    call_id: str
    connector: str
    op: str
    latency_ms: NonNegativeInt
    payload_ref: str


class SurfaceCreatedPayload(LedgerPayload):
    surface_id: str
    kind: SurfaceKind
    source: LedgerOpRef
    title: str
    payload_ref: str


class ViewDerivedPayload(LedgerPayload):
    surface_id: str
    tier: ViewTier
    basis: ViewBasis
    spec_ref: str | None = None
    gen: ViewGen | None = None


class ViewPreferencePayload(LedgerPayload):
    surface_id: str
    keep: ViewKeep
    # SDR §5 pins ``actor`` to the constant ``"user"`` here (not the
    # ``decision_actor`` enum, which also permits ``policy``).
    actor: Literal["user"]


class ShapeRequestedPayload(LedgerPayload):
    surface_id: str
    # SDR §5 pins ``actor`` to the constant ``"user"`` here.
    actor: Literal["user"]


class ShapeResolvedPayload(LedgerPayload):
    """Outcome of a user-invited shaping attempt (PRD-B4, additive to SDR §5).

    ``reason`` is the safe lint/validation summary on a ``no_fit`` (never raw
    model output); omitted on a ``shaped`` outcome.
    """

    surface_id: str
    outcome: ShapeOutcome
    reason: str | None = None


class WriteStagedPayload(LedgerPayload):
    stage_id: str
    surface_id: str
    target: LedgerOpRef
    proposal_ref: str
    rows: NonNegativeInt | None = None
    agent_holds: tuple[AgentHold, ...] | None = None


class RevisionAuthorshipSpan(RuntimeContract):
    """A half-open ``[start, end)`` char range of the NEW text and its author.

    The pydantic mirror of ``revision_diff.AuthorshipSpan`` for the additive
    ``revision.added.authorship_spans`` payload key (PRD-D1). Offsets index code
    points into the new revision's text.
    """

    start: NonNegativeInt
    end: NonNegativeInt
    author: RevisionAuthor


class RevisionAddedPayload(LedgerPayload):
    stage_id: str
    rev: PositiveInt
    author: RevisionAuthor
    diff_ref: str
    # Additive (SDR §5 note, PRD-D1). Optional so the required-list parity with
    # the SSOT JSON is unchanged; the fold + client read them when present.
    proposal_ref: str | None = None
    authorship_spans: tuple[RevisionAuthorshipSpan, ...] | None = None
    rowset: RevisionRowset | None = None


class DecisionRecordedPayload(LedgerPayload):
    stage_id: str
    decision: DecisionKind
    scope: DecisionScope
    actor: DecisionActor
    apply: bool | None = None


class WriteFailureCode(StrEnum):
    """Why an apply refused / failed (PRD-D2, additive to SDR §5)."""

    PRECONDITION_DRIFT = "precondition_drift"
    CONNECTOR_ERROR = "connector_error"
    ATTEMPT_INDETERMINATE = "attempt_indeterminate"


class WriteAppliedFailure(RuntimeContract):
    """The ``write.applied.failure`` object — present only on a ``failed`` result."""

    code: WriteFailureCode
    detail: str | None = None


class WriteAppliedDecidedBy(RuntimeContract):
    """The ``write.applied.decided_by`` object — the receipt-row attribution."""

    # SDR §5 pins ``actor`` to the constant ``"user"`` here (a user approve is
    # the only thing that authorizes a commit in D2).
    actor: Literal["user"]
    decision_seq: NonNegativeInt


class WriteAppliedPayload(LedgerPayload):
    stage_id: str
    rev: PositiveInt
    result: ApplyResult
    row_keys: tuple[str, ...] | None = None
    connector_receipt_ref: str | None = None
    # Additive (SDR §5 note, PRD-D2). Optional so the required-list parity with
    # the SSOT JSON is unchanged; ``failure`` rides only on ``failed`` results,
    # ``decided_by`` names the approving decision for the receipt fold (E1).
    failure: WriteAppliedFailure | None = None
    decided_by: WriteAppliedDecidedBy | None = None
    row_results: tuple[WriteAppliedRowResult, ...] | None = None


class UsageRecordedPayload(LedgerPayload):
    purpose: UsagePurpose
    model: str
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    surface_id: str | None = None


class ReceiptEmittedPayload(LedgerPayload):
    surface_id: str
    fold_ref: str


class OperationRequestedPayload(LedgerPayload):
    operation_id: OperationIdText
    producer: Producer
    capability: str
    op: str
    args_digest: Sha256Hex
    parent_operation_id: OperationIdText | None = None


class OperationClassifiedPayload(LedgerPayload):
    operation_id: OperationIdText
    effect_class: EffectClass
    basis: OperationClassificationBasis
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class OperationCompletedPayload(LedgerPayload):
    operation_id: OperationIdText
    outcome: OperationOutcome
    result_ref: str | None = None
    latency_ms: SafeNonNegativeInt | None = None

    @field_validator("result_ref")
    @classmethod
    def _result_ref_not_physical_path(cls, value: str | None) -> str | None:
        _validate_non_physical_reference(value, "result_ref")
        return value


class OperationFailedPayload(LedgerPayload):
    operation_id: OperationIdText
    failure_code: Annotated[str, Field(min_length=1, max_length=128)]
    retryable: bool


class ArtifactCreatedPayload(LedgerPayload):
    artifact_id: ArtifactIdText
    kind: ArtifactKind
    revision: SafePositiveInt
    content_ref: str
    content_digest: Sha256Hex
    author: ArtifactAuthor

    @model_validator(mode="after")
    def _content_ref_matches(self) -> ArtifactCreatedPayload:
        parsed = ArtifactContentRefCodec.parse(self.content_ref)
        if parsed.artifact_id != self.artifact_id or parsed.revision != self.revision:
            raise ValueError(_Messages.ARTIFACT_CONTENT_REF_MATCHES)
        return self


class ArtifactRevisedPayload(LedgerPayload):
    artifact_id: ArtifactIdText
    revision: SafePositiveInt
    parent_revision: SafePositiveInt
    content_ref: str
    content_digest: Sha256Hex
    author: ArtifactAuthor

    @model_validator(mode="after")
    def _parent_precedes_revision(self) -> ArtifactRevisedPayload:
        if self.parent_revision >= self.revision:
            raise ValueError(_Messages.ARTIFACT_PARENT_PRECEDES)
        parsed = ArtifactContentRefCodec.parse(self.content_ref)
        if parsed.artifact_id != self.artifact_id or parsed.revision != self.revision:
            raise ValueError(_Messages.ARTIFACT_CONTENT_REF_MATCHES)
        return self


class ArtifactPromotedPayload(LedgerPayload):
    artifact_id: ArtifactIdText
    source_ref: str
    kind: ArtifactKind
    revision: SafePositiveInt

    @field_validator("source_ref")
    @classmethod
    def _source_ref_not_physical_path(cls, value: str) -> str:
        _validate_non_physical_reference(value, "source_ref")
        return value


class ArtifactPresentationDecidedPayload(LedgerPayload):
    artifact_id: ArtifactIdText
    decision: PresentationDecision
    basis: Annotated[str, Field(min_length=1, max_length=128)]
    surface_id: str | None = None


class EffectStagedPayload(LedgerPayload):
    stage_id: EffectStageIdText
    operation_id: OperationIdText
    executor: EffectExecutorKind
    target_ref: str
    target_digest: Sha256Hex
    proposal_ref: str
    proposal_digest: Sha256Hex
    policy: EffectPolicy

    @model_validator(mode="after")
    def _references_match(self) -> EffectStagedPayload:
        _validate_target_ref(self.target_ref)
        if self.executor is EffectExecutorKind.WORKSPACE:
            WorkspaceTargetRefCodec.parse(self.target_ref)
        parsed = ProposalUriCodec.parse(self.proposal_ref)
        if parsed.stage_id != self.stage_id or parsed.revision != 1:
            raise ValueError(_Messages.STAGED_PROPOSAL_REF_MATCHES)
        return self


class EffectRevisedPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    proposal_ref: str
    proposal_digest: Sha256Hex
    author: ArtifactAuthor

    @model_validator(mode="after")
    def _proposal_ref_matches(self) -> EffectRevisedPayload:
        parsed = ProposalUriCodec.parse(self.proposal_ref)
        if parsed.stage_id != self.stage_id or parsed.revision != self.revision:
            raise ValueError(_Messages.REVISED_PROPOSAL_REF_MATCHES)
        return self


class EffectDecisionRecordedPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    decision: EffectDecisionKind
    actor: EffectActor
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex


class EffectClaimedPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    claim_id: ClaimIdText
    executor: EffectExecutorKind
    attempt: SafePositiveInt


class EffectAppliedPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    outcome: EffectOutcome
    receipt_ref: str | None = None
    result_digest: Sha256Hex | None = None

    @model_validator(mode="after")
    def _receipt_ref_matches(self) -> EffectAppliedPayload:
        if self.receipt_ref is not None:
            parsed = EffectReceiptRefCodec.parse(self.receipt_ref)
            if parsed.stage_id != self.stage_id:
                raise ValueError(_Messages.RECEIPT_REF_MATCHES)
        return self


class EffectIndeterminatePayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    claim_id: ClaimIdText
    reason: Annotated[str, Field(min_length=1, max_length=512)]


class EffectReconciledPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    claim_id: ClaimIdText
    outcome: EffectOutcome
    receipt_ref: str | None = None

    @model_validator(mode="after")
    def _receipt_ref_matches(self) -> EffectReconciledPayload:
        if self.receipt_ref is not None:
            parsed = EffectReceiptRefCodec.parse(self.receipt_ref)
            if parsed.stage_id != self.stage_id or parsed.claim_id != self.claim_id:
                raise ValueError(_Messages.RECONCILED_RECEIPT_REF_MATCHES)
        return self


class GateOpenedV2Payload(LedgerPayload):
    gate_id: str
    operation_id: OperationIdText
    gate_kind: GateKind
    capability: str
    reason: Annotated[str, Field(min_length=1, max_length=512)]


class GateResolvedV2Payload(LedgerPayload):
    gate_id: str
    decision: GateDecision
    actor: EffectActor


def _validate_target_ref(value: str) -> None:
    """Reject physical paths while permitting executor-specific opaque URIs."""

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
        raise ValueError("target_ref must be an opaque non-file URI reference")


def _validate_non_physical_reference(value: str | None, field_name: str) -> None:
    """Reject host paths without constraining the owning subsystem's ref scheme."""

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


# ---------------------------------------------------------------------------
# Errors, messages, and the validation chokepoint
# ---------------------------------------------------------------------------


class LedgerContractError(ValueError):
    """Raised for an unknown ledger event type.

    Carries only a safe, actionable message — never internal detail.
    """


class _Messages:
    """Safe, actionable messages surfaced through the typed errors above."""

    DECISION_SCOPE_ONE_OF = "decision scope must set exactly one of 'rev' or 'row_keys'"
    ARTIFACT_PARENT_PRECEDES = "parent_revision must be less than revision"
    ARTIFACT_CONTENT_REF_MATCHES = "content_ref must reference artifact_id and revision"
    STAGED_PROPOSAL_REF_MATCHES = (
        "initial proposal_ref must reference stage_id and revision 1"
    )
    REVISED_PROPOSAL_REF_MATCHES = "proposal_ref must reference stage_id and revision"
    RECEIPT_REF_MATCHES = "receipt_ref must reference stage_id"
    RECONCILED_RECEIPT_REF_MATCHES = "receipt_ref must reference stage_id and claim_id"

    @staticmethod
    def unknown_event_type(event_type: object) -> str:
        return f"unknown ledger event type: {event_type!r}"


class WorkLedgerVocabulary:
    """Event-type → payload-model registry; the single validation chokepoint."""

    PAYLOAD_MODELS: ClassVar[Mapping[LedgerEventType, type[LedgerPayload]]] = {
        LedgerEventType.GATE_OPENED: GateOpenedPayload,
        LedgerEventType.GATE_RESOLVED: GateResolvedPayload,
        LedgerEventType.ACTION_CLASSIFIED: ActionClassifiedPayload,
        LedgerEventType.READ_EXECUTED: ReadExecutedPayload,
        LedgerEventType.SURFACE_CREATED: SurfaceCreatedPayload,
        LedgerEventType.VIEW_DERIVED: ViewDerivedPayload,
        LedgerEventType.VIEW_PREFERENCE: ViewPreferencePayload,
        LedgerEventType.SHAPE_REQUESTED: ShapeRequestedPayload,
        LedgerEventType.SHAPE_RESOLVED: ShapeResolvedPayload,
        LedgerEventType.WRITE_STAGED: WriteStagedPayload,
        LedgerEventType.REVISION_ADDED: RevisionAddedPayload,
        LedgerEventType.DECISION_RECORDED: DecisionRecordedPayload,
        LedgerEventType.WRITE_APPLIED: WriteAppliedPayload,
        LedgerEventType.USAGE_RECORDED: UsageRecordedPayload,
        LedgerEventType.RECEIPT_EMITTED: ReceiptEmittedPayload,
        LedgerEventType.OPERATION_REQUESTED: OperationRequestedPayload,
        LedgerEventType.OPERATION_CLASSIFIED: OperationClassifiedPayload,
        LedgerEventType.OPERATION_COMPLETED: OperationCompletedPayload,
        LedgerEventType.OPERATION_FAILED: OperationFailedPayload,
        LedgerEventType.ARTIFACT_CREATED: ArtifactCreatedPayload,
        LedgerEventType.ARTIFACT_REVISED: ArtifactRevisedPayload,
        LedgerEventType.ARTIFACT_PROMOTED: ArtifactPromotedPayload,
        LedgerEventType.ARTIFACT_PRESENTATION_DECIDED: (
            ArtifactPresentationDecidedPayload
        ),
        LedgerEventType.EFFECT_STAGED: EffectStagedPayload,
        LedgerEventType.EFFECT_REVISED: EffectRevisedPayload,
        LedgerEventType.EFFECT_DECISION_RECORDED: EffectDecisionRecordedPayload,
        LedgerEventType.EFFECT_CLAIMED: EffectClaimedPayload,
        LedgerEventType.EFFECT_APPLIED: EffectAppliedPayload,
        LedgerEventType.EFFECT_INDETERMINATE: EffectIndeterminatePayload,
        LedgerEventType.EFFECT_RECONCILED: EffectReconciledPayload,
        LedgerEventType.GATE_OPENED_V2: GateOpenedV2Payload,
        LedgerEventType.GATE_RESOLVED_V2: GateResolvedV2Payload,
    }

    # enum-key (SSOT ``enums`` key) → StrEnum. Single source for the parity test.
    ENUM_TYPES: ClassVar[Mapping[str, type[StrEnum]]] = {
        "auth_state": GateAuthState,
        "gate_outcome": GateOutcome,
        "write_policy": WritePolicy,
        "action_class": ActionClass,
        "classification_basis": ClassificationBasis,
        "surface_kind": SurfaceKind,
        "view_tier": ViewTier,
        "view_basis": ViewBasis,
        "view_keep": ViewKeep,
        "revision_author": RevisionAuthor,
        "decision_kind": DecisionKind,
        "decision_actor": DecisionActor,
        "apply_result": ApplyResult,
        "usage_purpose": UsagePurpose,
        "shape_outcome": ShapeOutcome,
        "producer": Producer,
        "effect_class": EffectClass,
        "operation_classification_basis": OperationClassificationBasis,
        "operation_outcome": OperationOutcome,
        "operation_result_kind": OperationResultKind,
        "artifact_kind": ArtifactKind,
        "artifact_author": ArtifactAuthor,
        "artifact_presentation_preference": ArtifactPresentationPreference,
        "presentation_decision": PresentationDecision,
        "surface_subject_type": SurfaceSubjectType,
        "effect_policy": EffectPolicy,
        "effect_decision": EffectDecisionKind,
        "effect_actor": EffectActor,
        "effect_outcome": EffectOutcome,
        "effect_executor": EffectExecutorKind,
        "effect_stage_status": EffectStageStatus,
        "gate_kind": GateKind,
        "gate_decision": GateDecision,
    }

    _CONTRACT: ClassVar[dict[str, object]] = load_work_ledger_contract()
    _COMPATIBILITY: ClassVar[dict[str, object]] = dict(
        _CONTRACT.get("compatibility") or {}
    )
    COMPATIBILITY_EVENT_TYPES: ClassVar[Mapping[str, str]] = {
        str(old): str(new)
        for old, new in dict(_COMPATIBILITY.get("event_mappings") or {}).items()
    }

    @classmethod
    def validate_payload(
        cls, event_type: str, payload: Mapping[str, object]
    ) -> LedgerPayload:
        """Validate an untrusted payload dict against its event-type model.

        Unknown ``event_type`` raises ``LedgerContractError``; a malformed
        payload (extra keys, wrong enum, ``v != 1``, both/neither decision
        scope) raises ``pydantic.ValidationError`` — never a silent pass.
        """

        model = cls.model_for(event_type)
        return model.model_validate(dict(payload))

    @classmethod
    def model_for(cls, event_type: str) -> type[LedgerPayload]:
        """Return the payload model for ``event_type`` or raise a typed error."""

        try:
            key = LedgerEventType(event_type)
        except ValueError as exc:
            raise LedgerContractError(_Messages.unknown_event_type(event_type)) from exc
        return cls.PAYLOAD_MODELS[key]

    @classmethod
    def compatibility_event_type(cls, event_type: str) -> LedgerEventType | None:
        """Return the read-side v2.1 semantic event for a legacy event.

        This does not transform payloads and must never be used by writers.
        Existing gates deliberately return ``None`` because their payload
        meaning is not a generalized gate write contract.
        """

        mapped = cls.COMPATIBILITY_EVENT_TYPES.get(event_type)
        return LedgerEventType(mapped) if mapped is not None else None


__all__ = [
    "ActionClass",
    "ActionClassifiedPayload",
    "AgentHold",
    "ApplyResult",
    "ArtifactAuthor",
    "ArtifactCreatedPayload",
    "ArtifactIdText",
    "ArtifactKind",
    "ArtifactPresentationDecidedPayload",
    "ArtifactPresentationPreference",
    "ArtifactPromotedPayload",
    "ArtifactRevisedPayload",
    "ClaimIdText",
    "ClassificationBasis",
    "DecisionActor",
    "DecisionKind",
    "DecisionRecordedPayload",
    "DecisionScope",
    "EffectActor",
    "EffectAppliedPayload",
    "EffectClaimedPayload",
    "EffectClass",
    "EffectDecisionKind",
    "EffectDecisionRecordedPayload",
    "EffectExecutorKind",
    "EffectIndeterminatePayload",
    "EffectOutcome",
    "EffectPolicy",
    "EffectReconciledPayload",
    "EffectRevisedPayload",
    "EffectStageIdText",
    "EffectStageStatus",
    "EffectStagedPayload",
    "GateAuthState",
    "GateDecision",
    "GateKind",
    "GateOpenedPayload",
    "GateOpenedV2Payload",
    "GateOutcome",
    "GateResolvedPayload",
    "GateResolvedV2Payload",
    "LedgerContractError",
    "LedgerEventType",
    "LedgerOpRef",
    "LedgerPayload",
    "OperationClassificationBasis",
    "OperationClassifiedPayload",
    "OperationCompletedPayload",
    "OperationFailedPayload",
    "OperationIdText",
    "OperationOutcome",
    "OperationRequestedPayload",
    "OperationResultKind",
    "PresentationDecision",
    "Producer",
    "ReadExecutedPayload",
    "ReceiptEmittedPayload",
    "RevisionRowset",
    "RevisionAddedPayload",
    "RevisionAuthor",
    "RevisionAuthorshipSpan",
    "RowFieldChange",
    "RowOutcome",
    "SafeNonNegativeInt",
    "SafePositiveInt",
    "Sha256Hex",
    "ShapeOutcome",
    "ShapeRequestedPayload",
    "ShapeResolvedPayload",
    "StagedRow",
    "SurfaceCreatedPayload",
    "SurfaceKind",
    "SurfaceSubjectType",
    "UsagePurpose",
    "UsageRecordedPayload",
    "ViewBasis",
    "ViewDerivedPayload",
    "ViewGen",
    "ViewKeep",
    "ViewPreferencePayload",
    "ViewTier",
    "WorkLedgerVocabulary",
    "WriteAppliedDecidedBy",
    "WriteAppliedFailure",
    "WriteAppliedPayload",
    "WriteAppliedRowResult",
    "WriteFailureCode",
    "WritePolicy",
    "WriteStagedPayload",
]
