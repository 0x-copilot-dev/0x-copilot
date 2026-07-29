"""Durable, body-free F6 batch plans.

Ordering is decided **before** any child is dispatched, and the decision is
durable before the coordinator can see it.  That is what makes restart safe: a
run that dies mid-batch is recovered by replaying the journal the client already
streams, not by trusting a checkpoint or re-deriving intent from tool calls that
may or may not have started.

Three properties are structural here rather than conventional:

- **No second ledger.**  The records below are carried by the existing
  tenant-scoped run event stream.  The adapter lives in
  :mod:`agent_runtime.capabilities.concurrency.batch_journal_store`; this module
  holds only the vocabulary, so the transport schema can import it without a
  cycle.  There is no new table, queue, JSONL file, or checkpoint-only truth.
- **No bodies.**  Every field is an identity, a closed vocabulary member, a
  keyed digest, a count, or a timestamp.  Tool arguments, tool results,
  connector URLs, raw capability names, prompts, and user content have no field
  through which they could enter.  ``capability_ref`` is the ``cap_<32 hex>``
  shape F6.1 pattern-locks for exactly this reason, and resource scope keys
  arrive only as the ``hmac-sha256:<64 hex>`` digests
  :class:`BatchOperation` already enforces.
- **No undurable dispatch.**  The only way to obtain a
  :class:`DurableBatchPlan` — the handle the F6.3 coordinator executes — is to
  get one back from a completed append.  A caller cannot dispatch against a plan
  that was merely constructed.

The plan record carries its own inputs (the resolved per-operation
:class:`ConcurrencyPolicy`, itself a closed body-free vocabulary), so replay does
not take the stored segmentation on trust: the record refuses to validate unless
its segments are exactly what :class:`BatchPlanner` produces from those inputs.
Determinism is therefore re-checked on every parse, including every replay after
a restart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self, TypeAlias, runtime_checkable

from pydantic import (
    Field,
    PositiveInt,
    TypeAdapter,
    field_validator,
    model_validator,
)

from agent_runtime.capabilities.concurrency.contracts import (
    BatchFailurePolicy,
    BatchOperation,
    BatchPlan,
    BatchSegment,
    ConcurrencyAllowance,
    ConcurrencyPolicy,
    OperationBatch,
)
from agent_runtime.capabilities.concurrency.descriptor_policy import (
    CapabilityConcurrencyDeclaration,
    ConcurrencyPolicyResolution,
)
from agent_runtime.capabilities.concurrency.kill_switches import (
    ConcurrencyKillSwitchDecision,
    ConcurrencyKillSwitchGate,
    ConcurrencyKillSwitchReason,
    ConcurrencyKillSwitchScope,
)
from agent_runtime.capabilities.concurrency.planner import BatchPlanner
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class BatchJournalPatterns:
    """Shared, deliberately narrow shapes for every F6 journal identity."""

    DIGEST = r"^[0-9a-f]{64}$"
    IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:~-]{0,159}$"
    POLICY_DIGEST = r"^sha256:[0-9a-f]{64}$"
    REVISION = r"^[A-Za-z0-9][A-Za-z0-9._:~/-]{0,255}$"


class BatchJournalLimits:
    """Bounds that keep one journal record small enough to replay cheaply."""

    MAX_OPERATIONS = 100
    MAX_SEGMENTS = 100
    MAX_OPERATION_ID_LENGTH = 255
    MAX_TRANSPORT_ID_LENGTH = 160
    EVENT_ID_PREFIX = "operation_batch"
    STABLE_ID_PREFIX = "batch-plan"
    CHILD_STABLE_ID_PREFIX = "batch-child"


class BatchJournalRecordKind(StrEnum):
    """Closed F6 journal vocabulary.

    The union is discriminated, so the child-transition member below was added
    without touching the plan record's shape or its stable identity — exactly
    the extension point F6.2 left open.
    """

    PLAN_BOUND = "plan_bound"
    CHILD_TRANSITION = "child_transition"


class BatchChildPhase(StrEnum):
    """The two durable moments in one child's life.

    There are exactly two because exactly two are load-bearing for restart.
    ``DISPATCH_INTENT`` is appended **before** the child body is awaited and
    ``SETTLED`` after it returns, which is what makes the *absence* of an intent
    record a proof rather than a guess: a child whose intent append had not
    completed cannot have been dispatched, because the dispatching coroutine was
    still suspended on that append.

    Nothing is recorded for a child that was refused — by the gate, the permit
    table, a stopped batch, or cancellation — because a refusal means the body
    was never awaited, which is the same evidence as no record at all.
    """

    DISPATCH_INTENT = "dispatch_intent"
    SETTLED = "settled"


class BatchChildDisposition(StrEnum):
    """What a settled child's outcome is durably known to be.

    ``INDETERMINATE`` is a first-class member, not an error case: a child that
    was cancelled or drained past has an outcome nobody can determine, and the
    journal has to be able to say so.  It is deliberately *not* the absence of a
    record — absence already means the same thing, and the positive record is
    what lets an operator see that the system knew it did not know.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class PlannedOperation(RuntimeContract):
    """One already-authorized operation plus the policy that governs it.

    ``policy`` is the F6.1 *resolved* policy — the narrowest value the product
    catalog, an approved user tightening, and a trusted provider jointly
    support.  Its default is the conservative floor, so an operation whose
    metadata is missing or unknown carries a policy that cannot authorize any
    overlap.  Every field is a closed vocabulary member, a dimension-name list,
    or one scheduling integer; none can carry a body.
    """

    operation: BatchOperation
    capability_ref: str = Field(
        pattern=CapabilityConcurrencyDeclaration.CAPABILITY_REF_PATTERN
    )
    policy: ConcurrencyPolicy = Field(default_factory=ConcurrencyPolicy)
    policy_digest: str = Field(pattern=BatchJournalPatterns.POLICY_DIGEST)

    @classmethod
    def of(
        cls,
        *,
        operation: BatchOperation,
        capability_ref: str,
        policy: ConcurrencyPolicy | None = None,
    ) -> Self:
        """Bind an operation to a resolved policy and its lineage digest.

        Omitting ``policy`` is the undeclared case and yields the conservative
        floor, which the planner turns into a serial segment.
        """

        effective = policy if policy is not None else ConcurrencyPolicy()
        return cls(
            operation=operation,
            capability_ref=capability_ref,
            policy=effective,
            policy_digest=ConcurrencyPolicyResolution.digest_of(effective),
        )

    @classmethod
    def resolved(
        cls,
        *,
        operation: BatchOperation,
        resolution: ConcurrencyPolicyResolution,
    ) -> Self:
        """Bind an operation to an F6.1 resolution without restating its digest."""

        return cls(
            operation=operation,
            capability_ref=resolution.capability_ref,
            policy=resolution.policy,
            policy_digest=resolution.policy_digest,
        )

    @model_validator(mode="after")
    def _digest_matches_policy(self) -> Self:
        if self.policy_digest != ConcurrencyPolicyResolution.digest_of(self.policy):
            raise ValueError(
                "planned operation policy digest does not match its policy"
            )
        return self


class _BatchJournalRecordBase(RuntimeContract):
    """Shared stable identity and self-authenticating digest.

    The digest deliberately excludes observation time so two writers that made
    the same decision converge on the first durable event instead of
    conflicting.  Record identity stays covered, so reusing an ID for a
    different logical fact remains an idempotency conflict.
    """

    schema_version: Literal[1] = 1
    record_kind: BatchJournalRecordKind
    record_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    record_digest: str = Field(pattern=BatchJournalPatterns.DIGEST)
    run_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    snapshot_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _digest_matches(self) -> Self:
        if self.record_digest != canonical_json_sha256(self.digest_payload()):
            raise ValueError("batch journal record digest does not match its body")
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return the semantic body covered by ``record_digest``."""

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
        provisional = cls.model_construct(**payload, record_digest="0" * 64)
        return cls(
            **payload,
            record_digest=canonical_json_sha256(provisional.digest_payload()),
        )


class BatchPlanBoundRecord(_BatchJournalRecordBase):
    """The ordering decision for one tool-call group, durable before dispatch.

    The record stores both the decision (``segments``) and every input the
    decision was made from (``operations`` with their resolved policies, the
    effective allowance, the failure policy).  Storing both is what lets
    :meth:`rebuild_plan` be checked rather than trusted: the validator below
    re-plans the stored inputs and refuses the record unless it reproduces the
    stored segments exactly.  A tampered, reordered, or truncated segment list
    therefore fails at parse time — on the original append *and* on every
    replay.

    ``effective_allowance`` is the run snapshot's allowance already narrowed by
    the F6.7 kill-switch gate.  It is stored beside ``snapshot_allowance`` and
    the reason code, so a reader can tell "this batch was serial because an
    operator disabled a connector" from "this batch was serial because the run
    was never admitted to parallelism" without consulting anything else.
    """

    record_kind: Literal[BatchJournalRecordKind.PLAN_BOUND] = (
        BatchJournalRecordKind.PLAN_BOUND
    )
    batch_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    parent_operation_id: str | None = Field(
        default=None,
        pattern=BatchJournalPatterns.IDENTIFIER,
    )
    turn_ordinal: PositiveInt
    concurrency_policy_revision: str = Field(pattern=BatchJournalPatterns.REVISION)
    snapshot_allowance: ConcurrencyAllowance
    effective_allowance: ConcurrencyAllowance
    kill_switch_reason: ConcurrencyKillSwitchReason
    kill_switch_scope: ConcurrencyKillSwitchScope | None = None
    failure_policy: BatchFailurePolicy
    deadline_at: datetime | None = None
    operations: tuple[PlannedOperation, ...] = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_OPERATIONS,
    )
    segments: tuple[BatchSegment, ...] = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_SEGMENTS,
    )
    plan_digest: str = Field(pattern=BatchJournalPatterns.DIGEST)

    @field_validator("deadline_at")
    @classmethod
    def _aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline_at must be timezone-aware")
        return value

    @classmethod
    def stable_record_id(cls, batch_id: str) -> str:
        """Return the one record ID a batch's plan may ever occupy."""

        return f"{BatchJournalLimits.STABLE_ID_PREFIX}:{batch_id}"

    @classmethod
    def digest_of_plan(cls, plan: BatchPlan) -> str:
        """Return the stable digest of one planner decision."""

        return canonical_json_sha256(plan.model_dump(mode="json"))

    @model_validator(mode="after")
    def _decision_is_reproducible_and_narrowed(self) -> Self:
        if self.record_id != self.stable_record_id(self.batch_id):
            raise ValueError("a batch plan must use its batch's stable record id")
        # Reconstructing the decision proves it never broadened the snapshot:
        # ConcurrencyKillSwitchDecision refuses to exist otherwise.
        decision = ConcurrencyKillSwitchDecision(
            snapshot_allowance=self.snapshot_allowance,
            effective_allowance=self.effective_allowance,
            reason=self.kill_switch_reason,
            narrowed_by_scope=self.kill_switch_scope,
        )
        expected = BatchPlanner().plan(self.rebuild_batch(), self.rebuild_policies())
        if expected.segments != self.segments:
            raise ValueError("batch plan segments are not the planner's decision")
        if self.plan_digest != self.digest_of_plan(expected):
            raise ValueError("batch plan digest does not match its segmentation")
        if any(
            segment.allowance.effective_max_parallelism
            > decision.effective_allowance.effective_max_parallelism
            for segment in self.segments
        ):
            raise ValueError("a segment cannot exceed the effective batch allowance")
        return self

    def rebuild_batch(self) -> OperationBatch:
        """Return the ordered batch this plan was decided from."""

        return OperationBatch(
            batch_id=self.batch_id,
            parent_operation_id=self.parent_operation_id,
            operations=tuple(planned.operation for planned in self.operations),
            deadline=self.deadline_at,
            allowance=self.effective_allowance,
            failure_policy=self.failure_policy,
        )

    def rebuild_policies(self) -> dict[str, ConcurrencyPolicy]:
        """Return the resolved policy per operation, keyed by operation id."""

        return {
            planned.operation.operation_id: planned.policy
            for planned in self.operations
        }

    def rebuild_plan(self) -> BatchPlan:
        """Return the durable ordering decision this record bound."""

        return BatchPlan(
            batch_id=self.batch_id,
            operation_ids=tuple(
                planned.operation.operation_id for planned in self.operations
            ),
            segments=self.segments,
        )


class BatchChildTransitionRecord(_BatchJournalRecordBase):
    """One durable fact about one planned child, keyed by its phase.

    This record exists for a single reason: after a crash, "never started" and
    "started and we lost the answer" are different answers with opposite safety
    consequences, and nothing else in the run can tell them apart.  The plan
    record proves what *was ordered*; only this proves what *was begun*.

    The shape is the smallest one that carries that distinction.  There is no
    attempt counter, no capability reference, no timing, and no result: the
    resolved policy already lives on the plan record this transition refers to,
    so restating it here would create a second place for the same fact to be
    wrong.  ``operation_id`` is bounded exactly as
    :class:`~agent_runtime.capabilities.concurrency.contracts.BatchOperation`
    bounds it, so a transition is never leakier than the plan that named it.

    ``record_id`` is a digest over the identity triple rather than a joined
    string.  ``batch_id`` and ``operation_id`` may both contain ``:``, so
    concatenation could let two different children collide on one identity — and
    a collision here would silently merge two children's evidence, which is the
    one failure this record cannot be allowed to have.
    """

    record_kind: Literal[BatchJournalRecordKind.CHILD_TRANSITION] = (
        BatchJournalRecordKind.CHILD_TRANSITION
    )
    batch_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    operation_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_OPERATION_ID_LENGTH,
    )
    phase: BatchChildPhase
    disposition: BatchChildDisposition | None = None

    @classmethod
    def stable_record_id(
        cls,
        *,
        batch_id: str,
        operation_id: str,
        phase: BatchChildPhase,
    ) -> str:
        """Return the one record ID this child-phase may ever occupy."""

        identity = canonical_json_sha256(
            {
                "batch_id": batch_id,
                "operation_id": operation_id,
                "phase": phase.value,
            }
        )
        return f"{BatchJournalLimits.CHILD_STABLE_ID_PREFIX}:{identity}"

    @model_validator(mode="after")
    def _identity_and_phase_agree(self) -> Self:
        expected = self.stable_record_id(
            batch_id=self.batch_id,
            operation_id=self.operation_id,
            phase=self.phase,
        )
        if self.record_id != expected:
            raise ValueError("a child transition must use its stable record id")
        settled = self.phase is BatchChildPhase.SETTLED
        if settled and self.disposition is None:
            raise ValueError("a settled child transition requires a disposition")
        if not settled and self.disposition is not None:
            raise ValueError("only a settled child transition carries a disposition")
        return self


BatchJournalRecord: TypeAlias = Annotated[
    BatchPlanBoundRecord | BatchChildTransitionRecord,
    Field(discriminator="record_kind"),
]
BATCH_JOURNAL_RECORD_ADAPTER = TypeAdapter(BatchJournalRecord)


def validate_batch_journal_record(value: object) -> BatchJournalRecord:
    """Parse one strict discriminated F6 journal record."""

    return BATCH_JOURNAL_RECORD_ADAPTER.validate_python(value)


class BatchJournalError(RuntimeError):
    """Base class for every typed F6 journal failure."""


class BatchJournalConflict(BatchJournalError):
    """A stable F6 record ID was reused for a different semantic body."""

    def __init__(self, *, run_id: str, record_id: str) -> None:
        self.run_id = run_id
        self.record_id = record_id
        super().__init__(f"run {run_id} batch record {record_id} conflicts")


class BatchJournalScopeConflict(BatchJournalError):
    """A run's F6 journal belongs to another verified subject."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} batch state is outside requested scope")


class BatchJournalSnapshotConflict(BatchJournalError):
    """An F6 record does not bind the run's immutable control snapshot."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run {run_id} batch snapshot binding conflicts")


class BatchJournalCorruption(BatchJournalError):
    """Canonical F6 events cannot be replayed as a valid ordered journal."""

    def __init__(self, *, run_id: str, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        super().__init__(f"run {run_id} batch journal is invalid: {reason}")


class BatchJournalWrite(RuntimeContract):
    """Verified transport facts needed for one canonical append."""

    org_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )
    trace_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )
    subject_fingerprint: str = Field(pattern=BatchJournalPatterns.DIGEST)
    record: BatchJournalRecord


class BatchChildTransitionWrite(RuntimeContract):
    """Verified transport facts needed for one canonical child-transition append.

    Narrower than :class:`BatchJournalWrite` on purpose: ``record`` is the
    concrete transition type rather than the union, so a plan record cannot be
    handed to the transition path by mistake.
    """

    org_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )
    trace_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )
    subject_fingerprint: str = Field(pattern=BatchJournalPatterns.DIGEST)
    record: BatchChildTransitionRecord


class SequencedBatchJournalRecord(RuntimeContract):
    """One F6 fact plus its canonical per-run event sequence."""

    sequence_no: PositiveInt
    record: BatchJournalRecord


class DurableBatchPlan(RuntimeContract):
    """A plan the journal has already accepted — the F6.3 dispatch handle.

    This type is only ever produced by a completed append, so "the plan is
    durable before any child starts" is enforced by what the coordinator is
    handed rather than by remembering to persist first.
    """

    sequence_no: PositiveInt
    record: BatchPlanBoundRecord

    @property
    def batch_id(self) -> str:
        """Return the batch this plan orders."""

        return self.record.batch_id

    @property
    def plan(self) -> BatchPlan:
        """Return the durable ordering decision."""

        return self.record.rebuild_plan()

    @property
    def batch(self) -> OperationBatch:
        """Return the ordered batch the decision was made from."""

        return self.record.rebuild_batch()

    @property
    def allowance(self) -> ConcurrencyAllowance:
        """Return the kill-switch-narrowed authority this batch may use."""

        return self.record.effective_allowance

    def capability_ref_for(self, operation_id: str) -> str | None:
        """Return the opaque capability reference for one planned operation."""

        return next(
            (
                planned.capability_ref
                for planned in self.record.operations
                if planned.operation.operation_id == operation_id
            ),
            None,
        )

    def policy_for(self, operation_id: str) -> ConcurrencyPolicy | None:
        """Return the resolved policy for one planned operation."""

        return self.record.rebuild_policies().get(operation_id)


class DurableChildTransition(RuntimeContract):
    """A child-lifecycle fact the journal has already accepted."""

    sequence_no: PositiveInt
    record: BatchChildTransitionRecord

    @property
    def batch_id(self) -> str:
        """Return the batch this child belongs to."""

        return self.record.batch_id

    @property
    def operation_id(self) -> str:
        """Return the child this fact is about."""

        return self.record.operation_id

    @property
    def phase(self) -> BatchChildPhase:
        """Return which durable moment this fact records."""

        return self.record.phase


class BatchRecoveryView(RuntimeContract):
    """Every durable batch fact for one run, in canonical journal order.

    Restart recovery reads this and nothing else.  It answers two questions and
    refuses to answer a third: which batches had a durable plan, and which of
    their children have durable lifecycle evidence.  It does not say what should
    happen next — that is
    :class:`~agent_runtime.capabilities.concurrency.batch_recovery.BatchRestartPlanner`'s
    job — and it never infers a child's fate from the absence of a record.
    """

    run_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    snapshot_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    records: tuple[SequencedBatchJournalRecord, ...] = ()

    @property
    def plans(self) -> tuple[DurableBatchPlan, ...]:
        """Return every durable plan in journal order."""

        return tuple(
            DurableBatchPlan(sequence_no=item.sequence_no, record=item.record)
            for item in self.records
            if isinstance(item.record, BatchPlanBoundRecord)
        )

    @property
    def transitions(self) -> tuple[DurableChildTransition, ...]:
        """Return every durable child-lifecycle fact in journal order."""

        return tuple(
            DurableChildTransition(sequence_no=item.sequence_no, record=item.record)
            for item in self.records
            if isinstance(item.record, BatchChildTransitionRecord)
        )

    def plan_for(self, batch_id: str) -> DurableBatchPlan | None:
        """Return the durable plan for one batch, if the run recorded it."""

        return next(
            (plan for plan in self.plans if plan.batch_id == batch_id),
            None,
        )

    def transitions_for(self, batch_id: str) -> tuple[DurableChildTransition, ...]:
        """Return one batch's durable child facts, in journal order."""

        return tuple(
            transition
            for transition in self.transitions
            if transition.batch_id == batch_id
        )


@runtime_checkable
class BatchPlanStorePort(Protocol):
    """Append-only F6 fold over the canonical run event stream.

    This is the complete PRD §9.1 port.  ``append_child_transition`` was left
    unimplemented by F6.2 because nothing consumed it yet; F6.6 consumes it, so
    it is here now — on the same store, in the same event family, with the same
    stable-identity idempotency.  There is still exactly one ledger.
    """

    async def put_plan(self, write: BatchJournalWrite) -> DurableBatchPlan: ...

    async def append_child_transition(
        self,
        write: BatchChildTransitionWrite,
    ) -> DurableChildTransition: ...

    async def load_recovery_view(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> BatchRecoveryView: ...


class BatchPlanRequest(RuntimeContract):
    """Everything ``aafter_model`` knows about one tool-call group.

    The caller supplies operations in model-emitted order and the resolved
    policy for each.  It deliberately does *not* supply a parallelism decision:
    ordering is derived from those policies, narrowed by the live kill switch,
    and can only ever be at most what the run snapshot already allowed.
    """

    org_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )
    trace_id: str = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )
    subject_fingerprint: str = Field(pattern=BatchJournalPatterns.DIGEST)
    run_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    batch_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    parent_operation_id: str | None = Field(
        default=None,
        pattern=BatchJournalPatterns.IDENTIFIER,
    )
    turn_ordinal: PositiveInt
    operations: tuple[PlannedOperation, ...] = Field(
        min_length=1,
        max_length=BatchJournalLimits.MAX_OPERATIONS,
    )
    deadline_at: datetime | None = None
    failure_policy: BatchFailurePolicy = BatchFailurePolicy.STOP_NEW
    connector_id: str | None = Field(
        default=None,
        max_length=BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH,
    )

    @field_validator("deadline_at")
    @classmethod
    def _aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _operation_ids_are_unique(self) -> Self:
        operation_ids = [planned.operation.operation_id for planned in self.operations]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("batch operation_id values must be unique")
        return self

    def policies(self) -> dict[str, ConcurrencyPolicy]:
        """Return the resolved policy per operation, keyed by operation id."""

        return {
            planned.operation.operation_id: planned.policy
            for planned in self.operations
        }


class BatchPlanRecorder:
    """Decide and durably record one batch's ordering before any child starts.

    This is the ``aafter_model`` entry point, and it is the only producer of
    :class:`DurableBatchPlan`, so a caller cannot dispatch against an ordering
    that was decided but never written down.

    The three narrowing steps compose in one direction only: the run snapshot
    fixes a ceiling, the live kill switch may lower it, and the planner may only
    place operations into segments no wider than what survives.  Nothing on this
    path can raise a ceiling, so a missing policy, an unreadable switch, and an
    undeclared descriptor all land on a fully serial plan.
    """

    def __init__(
        self,
        *,
        journal: BatchPlanStorePort,
        gate: ConcurrencyKillSwitchGate,
        planner: BatchPlanner | None = None,
    ) -> None:
        self._journal = journal
        self._gate = gate
        self._planner = planner or BatchPlanner()

    async def record(
        self,
        request: BatchPlanRequest,
        *,
        snapshot: RunControlSnapshot,
        created_at: datetime | None = None,
    ) -> DurableBatchPlan:
        """Build, narrow, plan, and persist one batch — then return the handle."""

        decision = self._gate.admit(connector_id=request.connector_id)
        record = self.build_record(
            request,
            snapshot=snapshot,
            decision=decision,
            created_at=created_at,
        )
        return await self._journal.put_plan(
            BatchJournalWrite(
                org_id=request.org_id,
                trace_id=request.trace_id,
                subject_fingerprint=request.subject_fingerprint,
                record=record,
            )
        )

    def build_record(
        self,
        request: BatchPlanRequest,
        *,
        snapshot: RunControlSnapshot,
        decision: ConcurrencyKillSwitchDecision,
        created_at: datetime | None = None,
    ) -> BatchPlanBoundRecord:
        """Return the record one decision produces, without persisting it.

        Exposed so the decision itself can be exercised without a store. It is
        a pure function of its arguments, which is what makes two runs with the
        same inputs produce byte-identical records.
        """

        batch = OperationBatch(
            batch_id=request.batch_id,
            parent_operation_id=request.parent_operation_id,
            operations=tuple(planned.operation for planned in request.operations),
            deadline=request.deadline_at,
            allowance=decision.effective_allowance,
            failure_policy=request.failure_policy,
        )
        plan = self._planner.plan(batch, request.policies())
        return BatchPlanBoundRecord.create(
            record_id=BatchPlanBoundRecord.stable_record_id(request.batch_id),
            run_id=request.run_id,
            snapshot_id=snapshot.snapshot_id,
            batch_id=request.batch_id,
            parent_operation_id=request.parent_operation_id,
            turn_ordinal=request.turn_ordinal,
            concurrency_policy_revision=snapshot.policy_revisions.concurrency,
            snapshot_allowance=decision.snapshot_allowance,
            effective_allowance=decision.effective_allowance,
            kill_switch_reason=decision.reason,
            kill_switch_scope=decision.narrowed_by_scope,
            failure_policy=request.failure_policy,
            deadline_at=request.deadline_at,
            operations=request.operations,
            segments=plan.segments,
            plan_digest=BatchPlanBoundRecord.digest_of_plan(plan),
            **({} if created_at is None else {"created_at": created_at}),
        )


__all__ = (
    "BATCH_JOURNAL_RECORD_ADAPTER",
    "BatchChildDisposition",
    "BatchChildPhase",
    "BatchChildTransitionRecord",
    "BatchChildTransitionWrite",
    "BatchJournalConflict",
    "BatchJournalCorruption",
    "BatchJournalError",
    "BatchJournalLimits",
    "BatchJournalPatterns",
    "BatchJournalRecord",
    "BatchJournalRecordKind",
    "BatchJournalScopeConflict",
    "BatchJournalSnapshotConflict",
    "BatchJournalWrite",
    "BatchPlanBoundRecord",
    "BatchPlanRecorder",
    "BatchPlanRequest",
    "BatchPlanStorePort",
    "BatchRecoveryView",
    "DurableBatchPlan",
    "DurableChildTransition",
    "PlannedOperation",
    "SequencedBatchJournalRecord",
    "validate_batch_journal_record",
)
