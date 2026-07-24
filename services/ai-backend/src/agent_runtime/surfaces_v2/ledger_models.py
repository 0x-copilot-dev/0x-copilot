"""Pydantic mirror of the Work Ledger vocabulary (SDR §5, PRD-A1 D1/D3).

The JSON in ``copilot_service_contracts.work_ledger`` is the single source of
truth. These models mirror it; cross-language parity tests pin the pydantic
models, that JSON, and the ``packages/api-types`` TypeScript types together so
none of the three can drift silently. ``WorkLedgerVocabulary.validate_payload``
is the single validation chokepoint: it maps an event-type string to its payload
model and validates an untrusted dict through it.

Every payload extends ``LedgerPayload`` (a ``RuntimeContract``: frozen,
``extra="forbid"`` — extra/malformed keys fail as a typed
``pydantic.ValidationError``). Nothing here is wired into the runtime yet
(PRD-A1 is contracts only; emission is PRD-A3).

Wire-shape tenancy rule: no ``org_id`` / ``user_id`` on any payload — attribution
rides the run envelope server-side (mirrors ``RuntimeEventDraft`` vs
``RuntimeEventEnvelope``).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import (
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from agent_runtime.execution.contracts import RuntimeContract


# ---------------------------------------------------------------------------
# Event-type enum (order == ``LEDGER_EVENT_TYPES`` in the SSOT JSON)
# ---------------------------------------------------------------------------


class LedgerEventType(StrEnum):
    """The 15 ledger event types, in contract order (SDR §5)."""

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


class DecisionRecordedPayload(LedgerPayload):
    stage_id: str
    decision: DecisionKind
    scope: DecisionScope
    actor: DecisionActor


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


class UsageRecordedPayload(LedgerPayload):
    purpose: UsagePurpose
    model: str
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    surface_id: str | None = None


class ReceiptEmittedPayload(LedgerPayload):
    surface_id: str
    fold_ref: str


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


__all__ = [
    "ActionClass",
    "ActionClassifiedPayload",
    "AgentHold",
    "ApplyResult",
    "ClassificationBasis",
    "DecisionActor",
    "DecisionKind",
    "DecisionRecordedPayload",
    "DecisionScope",
    "GateAuthState",
    "GateOpenedPayload",
    "GateOutcome",
    "GateResolvedPayload",
    "LedgerContractError",
    "LedgerEventType",
    "LedgerOpRef",
    "LedgerPayload",
    "ReadExecutedPayload",
    "ReceiptEmittedPayload",
    "RevisionAddedPayload",
    "RevisionAuthor",
    "RevisionAuthorshipSpan",
    "ShapeOutcome",
    "ShapeRequestedPayload",
    "ShapeResolvedPayload",
    "SurfaceCreatedPayload",
    "SurfaceKind",
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
    "WriteFailureCode",
    "WritePolicy",
    "WriteStagedPayload",
]
