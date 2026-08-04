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
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import unquote, urlsplit

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
from agent_runtime.rollout_admission import E2GovernedLane
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
    EFFECT_PROJECTION_BOUND = "effect.projection_bound"
    EFFECT_REVISED = "effect.revised"
    EFFECT_DECISION_RECORDED = "effect.decision_recorded"
    EFFECT_CLAIMED = "effect.claimed"
    EFFECT_APPLIED = "effect.applied"
    EFFECT_INDETERMINATE = "effect.indeterminate"
    EFFECT_RECONCILED = "effect.reconciled"
    GATE_OPENED_V2 = "gate.opened.v2"
    GATE_RESOLVED_V2 = "gate.resolved.v2"
    EFFECT_ROW_DECISIONS_RECORDED = "effect.row_decisions_recorded"


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


class ArtifactCausalLane(StrEnum):
    """What caused an artifact mutation, and therefore what may seal it.

    Authorship and causality are independent axes. ``ArtifactAuthor`` says *who*
    wrote a revision; this says *what activity it belongs to*, which decides
    whether a run's terminal event is entitled to seal it.

    ``RUN`` — caused by agent activity inside a live run. The run's terminal
    event seals it, because ``RunTerminationCoordinator`` promises "everything
    this run caused is already in the ledger".

    ``CONVERSATION`` — caused by a user acting on the conversation's canvas,
    which is not part of any run's causal story. A conversation never seals, so
    this lane has no terminal state to violate. It deliberately produces no
    run-ledger event; the durable record is the immutable artifact revision.

    The lane is always derived server-side from authorship. It is never
    accepted from a caller, so a client cannot route a model-authored write
    into the unsealed lane.
    """

    RUN = "run"
    CONVERSATION = "conversation"


class ArtifactPresentationPreference(StrEnum):
    AUTO = "auto"
    CANVAS = "canvas"
    CHAT_CARD = "chat_card"
    NONE = "none"


class SurfaceAccent(StrEnum):
    """The identity hue a surface carries, chosen by name and never by value.

    This is a closed vocabulary on purpose. A surface's colour is presentation,
    and the standing rule is that the model supplies data and intent while the
    renderer paints — the same reason it cannot emit TSX or a URL. Accepting a
    hex, a CSS colour, or a token name here would hand model output a direct
    write into a stylesheet; accepting a NAME hands it a choice from a set the
    host already fixed.

    Members mirror ``SURFACE_HUES`` in ``chat-surface/src/surfaces/surfaceHue``
    and the ``[data-surface-hue]`` blocks in design-system's ``styles.css``.
    Adding one means adding it in all three places — deliberately, so a hue can
    never exist on the wire without a colour to resolve to.

    ``NONE`` is a real choice, not an absence: it renders a hollow ring, which
    is how a surface says it has no identity to claim rather than borrowing one.
    Leaving the field unset is the different thing — it means "no preference",
    and the client derives a hue from the artifact's kind.
    """

    JADE = "jade"
    SKY = "sky"
    INDIGO = "indigo"
    EMBER = "ember"
    VIOLET = "violet"
    PLUM = "plum"
    AMBER = "amber"
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


class EffectProposalKind(StrEnum):
    """The body-free proposal shapes the universal staging protocol accepts."""

    CANONICAL_ARGUMENTS = "canonical_arguments"
    ARTIFACT_REVISION = "artifact_revision"
    WORKSPACE_CHANGE_SET = "workspace_change_set"
    ROW_SET = "row_set"
    BROWSER_SUBMISSION = "browser_submission"
    SANDBOX_PATCH = "sandbox_patch"
    BUILTIN_PAYLOAD = "builtin_payload"


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


class LedgerWriter(StrEnum):
    """Which writer produced a ledger row — the row's provenance stamp (``w``).

    NOT one of the ``enums`` in the SSOT JSON, deliberately. Those describe
    *what a row says*; this describes *who said it*, and it is declared under
    the contract's own ``writers`` key instead. The two are validated the same
    way and drift the same way, but conflating them would put a transport
    concern into the payload vocabulary the renderers switch on.

    Absence is meaningful and is the reason the field is nullable: a row with no
    ``w`` **and any content** was written before any writer signed its work.
    That is the entire point of the stamp. Without it, "is this record historic?"
    can only be guessed from the *shape* of the strings inside the payload —
    which is precisely what ``isLegacySurfaceCreated`` did, and it answered
    "historic" for every surface the current pipeline produces.

    The "and any content" qualifier is not hedging: a payload the allow-list
    rejected projects to exactly ``{}`` and is deliberately left unsigned
    (``_sign_ledger_writer``), because ``{}`` is that allow-list's rejection
    sentinel. Such a row can still reach the store, since its caller appends
    regardless — but it carries no id for any reader to key on, so it is inert
    rather than mis-attributed. Refusing that append is the open fix, and it
    belongs to the callers, not to the stamp.

    That claim only holds because the stamp is applied at the **append funnel**,
    not by each producer:
    :meth:`runtime_api.schemas.events.RuntimeEventPresentationProjector.payload_for_event`
    signs every ledger row on its way to the store with
    :data:`CURRENT_LEDGER_WRITER`. Producer-side stamping came first and reached
    6 of the 34 event types — two producers still sign their own rows, and the
    funnel carries those stamps through — which on its own would have made "no
    ``w``" mean "historic *or* written by one of the 28 producers that forgot".
    A reader keyed on that mis-classifies live rows, which is the same defect
    ``isLegacySurfaceCreated`` had. One seam cannot forget a branch.

    Adding a member is how a writer generation is retired: old rows keep the
    member they were written with, the transport keeps understanding them, and
    a stamp from a writer this build has never heard of is REJECTED
    (:class:`UnknownLedgerWriterError`) rather than rendered on a guess.
    """

    RUNTIME_V2_1 = "runtime.v2.1"


_WORK_LEDGER_CONTRACT = load_work_ledger_contract()
# The writer this build signs new rows with. Read from the contract's own
# ``writers.current`` rather than re-spelled here, so the JSON key is
# load-bearing: promote a generation there and every append follows, and a
# ``current`` that names a writer this enum has never heard of fails at import
# instead of stamping rows nobody can read.
CURRENT_LEDGER_WRITER: LedgerWriter = LedgerWriter(
    str(dict(_WORK_LEDGER_CONTRACT["writers"])["current"])
)
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
    """Base for all v2 ledger payloads: versioned and signed (SDR §5).

    ``v`` is required with no default on purpose — a defaulted field is dropped
    from ``model_json_schema()["required"]``, which would break the parity pin
    against the SSOT JSON (``v`` is first in every event's ``required`` array).

    ``w`` is its sibling and its opposite: the writer stamp, optional with a
    default precisely SO it stays out of ``required``, because an append-only
    log must keep validating every row written before the field existed. ``v``
    says which vocabulary a row speaks; ``w`` says who wrote it. A durable log
    read years later needs both — Kafka carries a schema id, protobuf carries
    field numbers, event sourcing carries a ``schema_version`` — and this ledger
    carried neither until a fold had to guess a row's age from the shape of the
    ids inside it and guessed wrong for every live surface.

    ``None`` on a payload MODEL means "this producer did not sign"; it is not
    what lands in the ledger, because the append funnel signs the row on the way
    past (see :class:`LedgerWriter`). ``None`` on a row read back OUT of the
    store means the row predates the stamp — never "written by nobody".
    """

    v: Literal[1]
    w: LedgerWriter | None = None


class GateOpenedPayload(LedgerPayload):
    gate_id: str
    connector: str
    purpose: str
    scopes: tuple[str, ...]
    auth_state: GateAuthState
    # The line a PERSON reads, for a WRITE gate only (a connect gate's
    # ``purpose`` is already human copy, so it omits this and the payload stays
    # the size it was). Optional with a default so it stays out of the schema's
    # ``required`` array — the SSOT parity pin reads that array, and history
    # written before this field must keep validating.
    #
    # It is NOT a second source of truth: the emitter builds it from the same
    # argument-free op + connector tokens as ``purpose``. See
    # ``_Messages.ledger_display_title``.
    display_title: str | None = None


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
    # The renderer state this surface was created with: `{spec?, source?, data}`
    # — the `SurfaceState` the projector assembled, carried whole.
    #
    # It rides the record that DECLARES the surface rather than being re-joined
    # at read time, and that is the whole point. Two reasons, one durable and
    # one structural:
    #
    # * A ledger is a compliance record, so the spec a run was shaped on must be
    #   the spec the run recorded. Re-resolving `builtin.lookup(connector, op)`
    #   during a later hydration would return whatever the packaged library says
    #   *today*, so a curated spec edited after the fact would make the stored
    #   `view.derived` (`basis: registry`) disagree with what the client renders.
    # * `spec` and `data` are two halves of ONE resolution — the spec's
    #   `items_path` was inferred from this exact payload. Shipping them apart
    #   and rejoining them by `payload_ref` meant binding a spec against a
    #   DIFFERENT representation of the same read (the model-facing content half
    #   rather than the structured artifact half), which resolved nothing and
    #   rendered a correctly-shaped table over zero rows.
    #
    # Typed as a plain mapping, not `SurfaceState`: `surfaces_v2` is the ledger
    # vocabulary and must not take a dependency on the v1 presentation models.
    # The value is validated at both ends — by the projector that produced it,
    # and again by the transport allow-list before it reaches a client — so this
    # layer only has to carry it faithfully.
    #
    # Optional because the async `surface_spec_generated` refinement still
    # arrives on its own event, because a surface may legitimately have no body
    # to draw, and because replay of a pre-PRD run carries none.
    state: dict[str, Any] | None = None


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
    # Durable E2 governed-lane mark. Missing remains valid for pre-E2 history;
    # a present value is strictly parsed and never silently downgraded.
    rollout: E2GovernedLane | None = None


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
    # Additive v2.1 writer metadata. These remain optional at the transport
    # boundary because v:1 history predates them; the A4 stager always emits the
    # complete set and an old canonical-only row is deliberately non-executable.
    capability: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    op: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    display_target: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    proposal_kind: EffectProposalKind | None = None
    proposal_content_ref: str | None = None
    proposal_media_type: (
        Annotated[
            str,
            Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$"),
        ]
        | None
    ) = None
    precondition_ref: str | None = None
    precondition_digest: Sha256Hex | None = None
    effect_class: EffectClass | None = None
    policy_snapshot_ref: str | None = None
    agent_hold: bool | None = None
    safe_summary_ref: str | None = None
    projection_required: bool | None = None
    owner_ref: str | None = None
    author_actor: EffectActor | None = None
    author_ref: str | None = None
    created_at: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @field_validator(
        "precondition_ref",
        "policy_snapshot_ref",
        "safe_summary_ref",
        "owner_ref",
        "author_ref",
    )
    @classmethod
    def _metadata_refs_are_safe(cls, value: str | None) -> str | None:
        _validate_opaque_safe_uri(value, "effect metadata reference")
        return value

    @field_validator("proposal_content_ref")
    @classmethod
    def _proposal_content_ref_is_safe(cls, value: str | None) -> str | None:
        _validate_immutable_content_ref(value)
        return value

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
    # ``author`` is the pre-v2.1 shape. The actor/ref pair is what new writers
    # persist; keeping both readable is required for v:1 replay compatibility.
    author: ArtifactAuthor | None = None
    proposal_kind: EffectProposalKind | None = None
    proposal_content_ref: str | None = None
    proposal_media_type: (
        Annotated[
            str,
            Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$"),
        ]
        | None
    ) = None
    target_ref: str | None = None
    target_digest: Sha256Hex | None = None
    display_target: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    precondition_ref: str | None = None
    precondition_digest: Sha256Hex | None = None
    safe_diff_ref: str | None = None
    author_actor: EffectActor | None = None
    author_ref: str | None = None
    created_at: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @field_validator(
        "target_ref",
        "precondition_ref",
        "safe_diff_ref",
        "author_ref",
    )
    @classmethod
    def _metadata_refs_are_safe(cls, value: str | None) -> str | None:
        _validate_opaque_safe_uri(value, "effect metadata reference")
        return value

    @field_validator("proposal_content_ref")
    @classmethod
    def _proposal_content_ref_is_safe(cls, value: str | None) -> str | None:
        _validate_immutable_content_ref(value)
        return value

    @model_validator(mode="after")
    def _proposal_ref_matches(self) -> EffectRevisedPayload:
        parsed = ProposalUriCodec.parse(self.proposal_ref)
        if parsed.stage_id != self.stage_id or parsed.revision != self.revision:
            raise ValueError(_Messages.REVISED_PROPOSAL_REF_MATCHES)
        return self


class EffectProjectionBoundPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    projection_ref: str
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    bound_at: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("projection_ref")
    @classmethod
    def _projection_ref_is_safe(cls, value: str) -> str:
        _validate_opaque_safe_uri(value, "projection_ref")
        return value


class EffectDecisionRecordedPayload(LedgerPayload):
    stage_id: EffectStageIdText
    revision: SafePositiveInt
    decision: EffectDecisionKind
    actor: EffectActor
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    actor_ref: str | None = None
    decided_at: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    row_keys: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] | None = (
        None
    )

    @field_validator("actor_ref")
    @classmethod
    def _actor_ref_is_safe(cls, value: str | None) -> str | None:
        _validate_opaque_safe_uri(value, "actor_ref")
        return value

    @field_validator("row_keys")
    @classmethod
    def _row_keys_are_unique(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is not None and (not value or len(value) != len(set(value))):
            raise ValueError("row_keys must contain unique row keys")
        return value


class EffectRowDecision(RuntimeContract):
    """One connector-neutral row review decision."""

    row_key: Annotated[str, Field(min_length=1, max_length=256)]
    decision: Literal["approve", "hold"]


class EffectRowDecisionsRecordedPayload(LedgerPayload):
    """A digest-pinned update to one row-set review selection."""

    stage_id: EffectStageIdText
    revision: SafePositiveInt
    decisions: tuple[EffectRowDecision, ...]
    actor: EffectActor
    proposal_digest: Sha256Hex
    target_digest: Sha256Hex
    actor_ref: str | None = None
    decided_at: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @field_validator("actor_ref")
    @classmethod
    def _actor_ref_is_safe(cls, value: str | None) -> str | None:
        _validate_opaque_safe_uri(value, "actor_ref")
        return value

    @model_validator(mode="after")
    def _decisions_are_unique(self) -> EffectRowDecisionsRecordedPayload:
        keys = tuple(item.row_key for item in self.decisions)
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("row decisions require unique row keys")
        return self


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
    row_results: tuple[WriteAppliedRowResult, ...] | None = None

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
    try:
        _validate_opaque_safe_uri(value, "target_ref")
    except ValueError as error:
        raise ValueError(
            "target_ref must be an opaque non-file URI reference"
        ) from error


def _validate_opaque_safe_uri(value: str | None, field_name: str) -> None:
    """Reject physical, encoded-traversal, and inline-body references.

    Event history is untrusted at replay boundaries. Decode repeatedly before
    checking path segments so ``%252e%252e`` cannot become a physical path in a
    downstream resolver.
    """

    if value is None:
        return
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be an opaque safe URI reference"
        ) from error
    decoded = value
    while isinstance(decoded, str):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    normalised = decoded.replace("\\", "/") if isinstance(decoded, str) else decoded
    try:
        decoded_parts = urlsplit(decoded)
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be an opaque safe URI reference"
        ) from error
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _REFERENCE_MAX_LENGTH
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or "://" not in value
        or value.startswith(("/", "~", "\\"))
        or not parsed.scheme
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.scheme.lower() in {"file", "filesystem", "data", "http", "https"}
        or (len(value) >= 3 and value[1:3] in {":\\", ":/"})
        or not isinstance(normalised, str)
        or normalised.startswith(("/", "~"))
        or decoded_parts.query
        or decoded_parts.fragment
        or decoded_parts.scheme.lower()
        in {"file", "filesystem", "data", "http", "https"}
        or (len(normalised) >= 3 and normalised[1:3] == ":/")
        or "\x00" in normalised
        or "\\" in decoded
        or decoded_parts.path.startswith("//")
        or any(
            segment in {".", ".."}
            for component in (
                decoded_parts.netloc,
                decoded_parts.path,
                decoded_parts.query,
                decoded_parts.fragment,
            )
            for segment in component.split("/")
        )
    ):
        raise ValueError(f"{field_name} must be an opaque safe URI reference")


def _validate_immutable_content_ref(value: str | None) -> None:
    """Validate a content locator without conflating it with proposal identity."""

    if value is None:
        return
    _validate_opaque_safe_uri(value, "proposal_content_ref")
    if value.lower().startswith("proposal://"):
        raise ValueError(
            "proposal_content_ref must locate immutable content, not proposal identity"
        )


def validate_immutable_content_ref(value: str) -> str:
    """Public validator shared by body-free effect contracts and entity mirrors."""

    _validate_immutable_content_ref(value)
    return value


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


class UnknownLedgerWriterError(LedgerContractError):
    """Raised when a row carries a writer stamp this build cannot read.

    The sibling of "unknown event type": one says the row speaks a vocabulary
    this build does not know, the other says it was signed by a producer this
    build does not know. Both are fail-closed for the same reason — the only
    alternatives are to drop the stamp (handing the client a foreign row
    formatted as though this build wrote it, the exact silent mis-render the
    stamp exists to prevent) or to persist the row with an empty payload, which
    is a ghost record the client fold skips without a word.
    """

    @classmethod
    def for_writer(cls, writer: object) -> "UnknownLedgerWriterError":
        """Return the error for one unreadable stamp, with its safe message."""

        return cls(_Messages.unknown_writer(writer))


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

    @staticmethod
    def unknown_writer(writer: object) -> str:
        return f"unknown ledger writer: {writer!r}"


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
        LedgerEventType.EFFECT_PROJECTION_BOUND: EffectProjectionBoundPayload,
        LedgerEventType.EFFECT_REVISED: EffectRevisedPayload,
        LedgerEventType.EFFECT_DECISION_RECORDED: EffectDecisionRecordedPayload,
        LedgerEventType.EFFECT_CLAIMED: EffectClaimedPayload,
        LedgerEventType.EFFECT_APPLIED: EffectAppliedPayload,
        LedgerEventType.EFFECT_INDETERMINATE: EffectIndeterminatePayload,
        LedgerEventType.EFFECT_RECONCILED: EffectReconciledPayload,
        LedgerEventType.GATE_OPENED_V2: GateOpenedV2Payload,
        LedgerEventType.GATE_RESOLVED_V2: GateResolvedV2Payload,
        LedgerEventType.EFFECT_ROW_DECISIONS_RECORDED: (
            EffectRowDecisionsRecordedPayload
        ),
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
        "surface_accent": SurfaceAccent,
        "surface_subject_type": SurfaceSubjectType,
        "effect_policy": EffectPolicy,
        "effect_decision": EffectDecisionKind,
        "effect_actor": EffectActor,
        "effect_outcome": EffectOutcome,
        "effect_executor": EffectExecutorKind,
        "effect_proposal_kind": EffectProposalKind,
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
    "CURRENT_LEDGER_WRITER",
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
    "EffectRowDecision",
    "EffectRowDecisionsRecordedPayload",
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
    "LedgerWriter",
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
    "UnknownLedgerWriterError",
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
