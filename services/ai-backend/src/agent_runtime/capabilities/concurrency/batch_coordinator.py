"""Run-scoped execution of already-durable F6 batch plans.

The framework, not this module, decides *when* a tool coroutine starts.
LangGraph and Deep Agents may launch every call in a tool-call group at once,
and no amount of scheduling politeness on our side changes that. So the
coordinator does not try to prevent coroutines from starting: it makes each one
**block on a persisted segment gate** until the durable plan says its turn has
come. Concurrency control is therefore a property of where the work waits, not
of who calls it.

Five commitments make that safe:

- **Nothing runs ahead of the plan.** :meth:`BatchExecutionCoordinator.run_child`
  runs a child's body only after resolving it against a
  :class:`~agent_runtime.capabilities.concurrency.batch_journal.DurableBatchPlan`
  that was registered with :meth:`BatchExecutionCoordinator.begin`. That handle
  is only ever produced by a completed journal append (F6.2), so "the plan is
  durable before any child starts" is enforced by what the coordinator can be
  handed. An operation the plan never named, or a batch never begun, is refused
  and its body is never awaited.

- **Segments are barriers, not hints.** Segment ``n + 1`` admits nothing until
  every member of segment ``n`` has settled. A serial segment holds exactly one
  operation, so a serial segment can never observe a concurrency above one, and
  a write planned behind two reads cannot overlap them.

- **Every ceiling narrows; none widens.** Effective width is
  ``segment.allowance ∧ plan.allowance ∧ live_allowance()``, composed with
  :meth:`ConcurrencyAllowance.narrowed_by`, which is a ``min`` and cannot return
  more than any input. That width is then handed to
  :class:`~agent_runtime.capabilities.concurrency.permits.RunPermitManager` as
  the requested per-scope cap, so the permit table can only narrow it further.
  The Step-2 run-scoped serial permit
  (``agent_runtime.control_plane.context.RunSerialAdmission``) sits *outside*
  this module and is never taken, released, or bypassed here.

- **Input order and real time are different orderings, and both are kept.**
  :meth:`BatchExecutionCoordinator.results` returns children in the batch's
  input order whatever order they finished in, while every recorded timestamp is
  read from the injected clock at the moment the child actually settled.

- **Dispatch is durable before it happens.** When a child journal is wired,
  :meth:`BatchExecutionCoordinator.run_child` records that a child is about to
  start, waits for that record to be durable, and only then awaits the body — and
  refuses to dispatch at all if the record cannot be written. A crash therefore
  leaves evidence that distinguishes "never started" from "started and we lost
  the answer", which is the distinction
  :mod:`agent_runtime.capabilities.concurrency.batch_recovery` needs and cannot
  reconstruct any other way.

:meth:`BatchExecutionCoordinator.cancel` stops admission, interrupts cancellable
reads, drains the rest under a bound, and rules whatever is left indeterminate.
It never rolls anything back and never claims an outcome it did not observe;
:mod:`agent_runtime.capabilities.concurrency.batch_cancellation` holds the
vocabulary that keeps those claims honest.

Deliberately **not** in this module: per-child Operation Gateway re-entry with
its own audit identity (F6.5), kill-switch resolution (F6.7 — consumed here only
through ``live_allowance``), and any factory or worker wiring (root).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final, Self, TypeAlias

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.batch_cancellation import (
    BatchCancellationReason,
    BatchCancellationReport,
    CancelledChild,
    ChildCancellationState,
    ChildEffectCertainty,
)
from agent_runtime.capabilities.concurrency.batch_child_journal import (
    BatchChildTransitionRecorder,
)
from agent_runtime.capabilities.concurrency.batch_journal import (
    BatchChildDisposition,
    BatchJournalError,
    BatchJournalPatterns,
    DurableBatchPlan,
)
from agent_runtime.capabilities.concurrency.contracts import (
    BatchFailurePolicy,
    BatchSegment,
    ConcurrencyAllowance,
    ConcurrencyPolicy,
    PermitAcquisitionRequest,
    PermitBounds,
    PermitLease,
    PermitOutcome,
    PermitScope,
    PermitWaitMode,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.descriptor_policy import (
    CapabilityConcurrencyDeclaration,
)
from agent_runtime.capabilities.concurrency.permits import RunPermitManager
from agent_runtime.execution.contracts import RuntimeContract


class BatchCoordinatorBounds:
    """Hard, content-free bounds for one run's execution state.

    The admission wait is bounded even when a batch declares no deadline. A
    child whose coroutine the framework never starts would otherwise stall every
    later segment for the life of the run; a bounded wait turns that into a
    typed refusal instead of a hang. Callers that want a tighter bound set
    ``deadline`` on the batch, which always wins when it is nearer.
    """

    MAX_TRACKED_BATCHES: Final[int] = 32
    DEFAULT_ADMISSION_WAIT_SECONDS: Final[float] = PermitBounds.MAX_TIMEOUT_SECONDS
    # A drain waits for children the coordinator cannot cancel. It is short by
    # design: waiting longer does not make an uncancellable write finish, it
    # only delays the moment the run admits it does not know. Whatever is still
    # running when this expires is reported indeterminate, never assumed done.
    DEFAULT_DRAIN_SECONDS: Final[float] = 5.0


class BatchCoordinatorError(RuntimeError):
    """A genuine batch-execution lifecycle fault with safe public text.

    Saturation, deadlines, refusals, and child failures are never in this
    family: they are closed :class:`BatchAdmissionOutcome` and
    :class:`BatchChildStatus` members on a returned result, so a caller can
    never mistake ordinary scheduling for a programming fault.
    """

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class BatchCoordinatorMessages:
    """Public, content-free text for every coordinator fault."""

    UNKNOWN_BATCH = "Batch execution state is not registered for this run."
    PLAN_CONFLICT = "A different durable plan is already bound to this batch id."
    TRACKING_EXHAUSTED = "Too many active batches are tracked for this run."
    NAIVE_CLOCK = "Batch coordinator timestamps must be timezone-aware."
    PERMIT_SCOPES_REJECTED = "Batch child permit scopes could not be resolved."


class BatchAdmissionOutcome(StrEnum):
    """Closed, deterministic result of one child's admission attempt.

    Every refusal is a fact about *this* admission: the child's body was never
    awaited here, so this process introduced no external effect.

    ``REFUSED_WITHHELD_ON_RESTART`` is the one member for which that is the
    whole claim rather than the whole story. It is reached only when a durable
    restart decision forbade re-running the child, and the reason such a
    decision exists is that an *earlier* process may already have done the work.
    Reading it as "nothing ever happened" would invert the very finding that
    produced it, so :attr:`introduced_no_effect` refuses to say so and callers
    that want the prior-process answer must read the journal, which is the only
    thing that knows.
    """

    ADMITTED = "admitted"
    REFUSED_UNKNOWN_BATCH = "refused_unknown_batch"
    REFUSED_UNKNOWN_OPERATION = "refused_unknown_operation"
    REFUSED_ALREADY_SETTLED = "refused_already_settled"
    REFUSED_BATCH_STOPPED = "refused_batch_stopped"
    REFUSED_DEADLINE = "refused_deadline"
    REFUSED_PERMIT = "refused_permit"
    REFUSED_CALLER_CANCELLED = "refused_caller_cancelled"
    REFUSED_DISPOSED = "refused_disposed"
    REFUSED_RUN_CANCELLED = "refused_run_cancelled"
    REFUSED_UNDURABLE = "refused_undurable"
    REFUSED_WITHHELD_ON_RESTART = "refused_withheld_on_restart"

    @property
    def admitted(self) -> bool:
        """Return whether this outcome let a child body run."""

        return self is BatchAdmissionOutcome.ADMITTED

    @property
    def introduced_no_effect(self) -> bool:
        """Return whether *no* process can have acted on this child's behalf.

        Derived by exclusion rather than listed, so a member added later is
        effect-uncertain by default and has to argue its way into the safe set.
        """

        return self not in (
            BatchAdmissionOutcome.ADMITTED,
            BatchAdmissionOutcome.REFUSED_WITHHELD_ON_RESTART,
        )


class BatchChildStatus(StrEnum):
    """Closed lifecycle state of one planned child.

    ``INDETERMINATE`` is the honest answer for a child that was cancelled after
    its body started: the external effect may or may not have happened, and this
    lane refuses to guess. F6.6 owns what to do about it.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED = "refused"
    INDETERMINATE = "indeterminate"

    @property
    def settled(self) -> bool:
        """Return whether this child can no longer change state."""

        return self in (
            BatchChildStatus.SUCCEEDED,
            BatchChildStatus.FAILED,
            BatchChildStatus.REFUSED,
            BatchChildStatus.INDETERMINATE,
        )


class BatchExecutionStatus(StrEnum):
    """Closed status of one batch as a whole.

    ``CANCELLED`` outranks ``PARTIAL`` and ``FAILED`` because a cancelled batch
    that happens to contain a failure did not *fail* — it stopped. Reporting
    "failed" there would attribute an outcome to the work that belongs to the
    decision to stop it. Per-child detail stays on ``outcomes``, so nothing is
    lost by naming the batch honestly.
    """

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    FAILED = "failed"


class BatchChildIdentity(RuntimeContract):
    """Body-free identity and plan position of one child.

    ``input_index`` is the operation's position in the model-emitted order and
    ``segment_index`` its position in the durable plan. Those are the two
    orderings the coordinator has to keep straight, so both are carried
    explicitly rather than recomputed.

    All three plan fields are absent together or present together. Absent means
    *this work is in no durable plan* — an operation the journal never named, or
    a batch never begun — which is exactly the case the coordinator refuses.
    Inventing an index or a capability reference for such work would make an
    unplanned child indistinguishable from the first planned one.
    """

    # Deliberately length-bounded rather than pattern-locked: a refusal must be
    # returnable for an id the caller invented, and raising a validation error
    # at the exact moment the coordinator is saying "no durable plan names this"
    # would turn a clean refusal into a fault. Planned identities are built from
    # a durable record whose own ids are already pattern-locked.
    batch_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    capability_ref: str | None = Field(
        default=None,
        pattern=CapabilityConcurrencyDeclaration.CAPABILITY_REF_PATTERN,
    )
    input_index: int | None = Field(default=None, ge=0)
    segment_index: int | None = Field(default=None, ge=0)

    @property
    def planned(self) -> bool:
        """Return whether a durable plan accounts for this child."""

        return self.input_index is not None

    @classmethod
    def unplanned(cls, *, batch_id: str, operation_id: str) -> Self:
        """Return the identity of work no durable plan accounts for.

        Both identifiers arrive from the caller, so they are clamped rather than
        trusted: refusing unplanned work must always succeed.
        """

        return cls(
            batch_id=cls._clamped(batch_id, fallback="unknown-batch"),
            operation_id=cls._clamped(operation_id, fallback="unknown-operation"),
        )

    @staticmethod
    def _clamped(value: object, *, fallback: str) -> str:
        if not isinstance(value, str) or not value.strip():
            return fallback
        return value.strip()[:255]

    @model_validator(mode="after")
    def _plan_position_is_all_or_nothing(self) -> Self:
        present = (
            self.capability_ref is not None,
            self.input_index is not None,
            self.segment_index is not None,
        )
        if any(present) and not all(present):
            raise ValueError("a planned child requires its full plan position")
        return self


class BatchChildAdmission(RuntimeContract):
    """What one child was admitted with, handed to its runner.

    ``effective_allowance`` is the already-narrowed authority the segment ran
    under, so a runner recording audit facts sees the width that actually
    applied rather than the width the plan hoped for.
    """

    identity: BatchChildIdentity
    outcome: BatchAdmissionOutcome
    effective_allowance: ConcurrencyAllowance
    permit: PermitLease | None = None
    admitted_at: datetime | None = None

    @property
    def admitted(self) -> bool:
        """Return whether this child's body may run."""

        return self.outcome.admitted

    @model_validator(mode="after")
    def _admission_is_self_consistent(self) -> Self:
        if self.outcome.admitted:
            if self.admitted_at is None:
                raise ValueError("an admitted child requires an admission timestamp")
            if self.permit is None or not self.permit.admitted:
                raise ValueError("an admitted child requires an admitted permit")
        elif self.admitted_at is not None:
            raise ValueError("a refused child must not carry an admission timestamp")
        if self.admitted_at is not None and (
            self.admitted_at.tzinfo is None or self.admitted_at.utcoffset() is None
        ):
            raise ValueError("admitted_at must be timezone-aware")
        return self


class BatchChildOutcome(RuntimeContract):
    """Body-free ordering, admission, and timing record for one child.

    Every field is an identity, a closed vocabulary member, an index, or a
    timestamp. The child's actual value and exception live on
    :class:`BatchChildResult`, which is runtime-only and never persisted from
    here.
    """

    identity: BatchChildIdentity
    status: BatchChildStatus
    admission: BatchAdmissionOutcome | None = None
    permit_outcome: PermitOutcome | None = None
    admitted_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether this child produced a result."""

        return self.status is BatchChildStatus.SUCCEEDED

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> Self:
        for moment in (self.admitted_at, self.completed_at):
            if moment is not None and (
                moment.tzinfo is None or moment.utcoffset() is None
            ):
                raise ValueError("batch child timestamps must be timezone-aware")
        return self


@dataclass(frozen=True, slots=True)
class BatchChildResult:
    """One child's body-free outcome plus its runtime payload.

    The payload is deliberately outside the Pydantic contract: a tool result is
    a body, and bodies do not belong in a type that anything downstream might be
    tempted to journal.
    """

    outcome: BatchChildOutcome
    value: object | None = None
    error: BaseException | None = None

    @property
    def operation_id(self) -> str:
        """Return the operation this result belongs to."""

        return self.outcome.identity.operation_id

    @property
    def succeeded(self) -> bool:
        """Return whether the child produced a result."""

        return self.outcome.succeeded


class BatchExecutionReport(RuntimeContract):
    """Body-free execution status of one batch, in input order.

    This is PRD-AR-F6's ``BatchResult`` minus the payloads: ``ordered_results``
    lives on :meth:`BatchExecutionCoordinator.results`, because only that half
    carries bodies.
    """

    batch_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)
    status: BatchExecutionStatus
    started_at: datetime
    completed_at: datetime | None = None
    outcomes: tuple[BatchChildOutcome, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> Self:
        for moment in (self.started_at, self.completed_at):
            if moment is not None and (
                moment.tzinfo is None or moment.utcoffset() is None
            ):
                raise ValueError("batch report timestamps must be timezone-aware")
        return self


BatchChildRunner: TypeAlias = Callable[[BatchChildAdmission], Awaitable[object]]
BatchPermitScopeFactory: TypeAlias = Callable[
    [BatchChildIdentity], tuple[PermitScope, ...]
]
BatchAllowanceSupplier: TypeAlias = Callable[[], ConcurrencyAllowance]
BatchClock: TypeAlias = Callable[[], datetime]


class _ChildState:
    """Mutable execution state for one planned child."""

    __slots__ = (
        "admission",
        "admitted_at",
        "cancel_requested",
        "completed_at",
        "error",
        "holds_slot",
        "identity",
        "input_index",
        "permit_outcome",
        "running_noted",
        "segment_index",
        "side_effect",
        "status",
        "task",
        "value",
    )

    def __init__(
        self,
        identity: BatchChildIdentity,
        *,
        input_index: int,
        segment_index: int,
        side_effect: SideEffectKind,
    ) -> None:
        self.identity = identity
        self.input_index = input_index
        self.segment_index = segment_index
        self.side_effect = side_effect
        self.status = BatchChildStatus.PENDING
        self.admission: BatchAdmissionOutcome | None = None
        self.permit_outcome: PermitOutcome | None = None
        self.admitted_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.holds_slot = False
        self.running_noted = False
        self.cancel_requested = False
        self.task: asyncio.Task[object] | None = None
        self.value: object | None = None
        self.error: BaseException | None = None

    @property
    def cancellable(self) -> bool:
        """Return whether interrupting this child cannot change the world.

        Only a declared read or an explicitly effect-free operation qualifies.
        An undeclared effect class is F6.1's conservative floor and is *not*
        cancellable here: interrupting an operation nobody classified could
        abandon a half-finished external change, and abandoning it is not the
        same as preventing it.
        """

        return self.side_effect in (SideEffectKind.READ, SideEffectKind.NONE)

    def outcome(self) -> BatchChildOutcome:
        """Return the body-free record of this child's current state."""

        return BatchChildOutcome(
            identity=self.identity,
            status=self.status,
            admission=self.admission,
            permit_outcome=self.permit_outcome,
            admitted_at=self.admitted_at,
            completed_at=self.completed_at,
        )

    def result(self) -> BatchChildResult:
        """Return this child's outcome together with its runtime payload."""

        return BatchChildResult(
            outcome=self.outcome(),
            value=self.value,
            error=self.error,
        )


def _cancellation_state(
    state: _ChildState,
    *,
    settled_before: bool,
) -> ChildCancellationState:
    """Classify one child for the cancellation report.

    Status is consulted before timing on purpose: an indeterminate child that
    happened to settle inside the drain window is still indeterminate, and
    checking "did it settle during the drain" first would quietly promote it to
    a completion.
    """

    if state.status is BatchChildStatus.INDETERMINATE:
        if state.cancel_requested:
            return ChildCancellationState.CANCELLED_IN_PLACE
        # Already unknowable before anyone asked to cancel — the drain neither
        # caused this nor could have resolved it, so it must not be reported as
        # work the drain failed to finish.
        return (
            ChildCancellationState.INDETERMINATE_BEFORE_CANCEL
            if settled_before
            else ChildCancellationState.IN_FLIGHT_INDETERMINATE
        )
    if state.status is BatchChildStatus.REFUSED:
        return ChildCancellationState.NOT_STARTED
    if not state.status.settled:
        return ChildCancellationState.IN_FLIGHT_INDETERMINATE
    return (
        ChildCancellationState.SETTLED_BEFORE_CANCEL
        if settled_before
        else ChildCancellationState.SETTLED_DURING_DRAIN
    )


class _SegmentWaiter:
    """One child parked on its segment gate until the plan admits it."""

    __slots__ = ("future", "outcome", "state")

    def __init__(self, state: _ChildState, future: asyncio.Future[None]) -> None:
        self.state = state
        self.future = future
        self.outcome: BatchAdmissionOutcome | None = None


class _BatchState:
    """Bounded execution state for one durable plan."""

    __slots__ = (
        "cancelled",
        "children",
        "cursor",
        "deadline",
        "failure_policy",
        "order",
        "plan",
        "running",
        "segments",
        "settled",
        "started_at",
        "stopped",
        "waiters",
    )

    def __init__(self, plan: DurableBatchPlan, started_at: datetime) -> None:
        batch = plan.batch
        self.plan = plan
        self.segments: tuple[BatchSegment, ...] = plan.record.segments
        self.deadline: datetime | None = batch.deadline
        self.failure_policy: BatchFailurePolicy = batch.failure_policy
        self.started_at = started_at
        self.stopped = False
        self.cancelled = False
        self.cursor = 0
        self.running = [0] * len(self.segments)
        self.settled = [0] * len(self.segments)
        self.waiters: list[_SegmentWaiter] = []
        self.order: tuple[str, ...] = plan.plan.operation_ids
        self.children: dict[str, _ChildState] = {}
        segment_of = {
            operation_id: segment.segment_index
            for segment in self.segments
            for operation_id in segment.operation_ids
        }
        for input_index, operation_id in enumerate(self.order):
            segment_index = segment_of[operation_id]
            # The effect class comes from the durable plan, not from a caller
            # and not from a live lookup: whether a child may be interrupted or
            # replayed has to be decided from the same frozen policy the
            # ordering was decided from, or the two could disagree.
            policy = plan.policy_for(operation_id) or ConcurrencyPolicy()
            self.children[operation_id] = _ChildState(
                BatchChildIdentity(
                    batch_id=plan.batch_id,
                    operation_id=operation_id,
                    capability_ref=plan.capability_ref_for(operation_id),
                    input_index=input_index,
                    segment_index=segment_index,
                ),
                input_index=input_index,
                segment_index=segment_index,
                side_effect=policy.side_effect,
            )

    @property
    def all_settled(self) -> bool:
        """Return whether every planned child has reached a terminal state."""

        return all(child.status.settled for child in self.children.values())


class BatchExecutionCoordinator:
    """Execute one run's durable batch plans without ever widening them.

    One instance per run, on one event loop, which matches the desktop's single
    in-process worker. State is bounded by ``max_tracked_batches`` and dropped
    entirely by :meth:`dispose`, so nothing survives a run.

    The permit scope factory supplies only the *identity* half of a permit
    request — which pools a child acquires at. Wait mode, timeout, and requested
    width are chosen here from the plan and the batch deadline, so a factory has
    no field through which it could widen a child's concurrency.

    ``child_journal`` is what makes a crash recoverable. When it is supplied,
    every child's dispatch is made durable *before* its body is awaited, and a
    failure to record that is a refusal rather than an unrecorded dispatch — so
    a child that ran always left a trace. When it is omitted the coordinator
    still executes correctly, but it leaves a run whose children a restart
    cannot classify;
    :class:`~agent_runtime.capabilities.concurrency.batch_recovery.BatchRestartPlanner`
    reads that absence as "unknown", never as "nothing happened".
    """

    def __init__(
        self,
        *,
        permits: RunPermitManager,
        permit_scopes: BatchPermitScopeFactory,
        live_allowance: BatchAllowanceSupplier | None = None,
        clock: BatchClock | None = None,
        max_tracked_batches: int = BatchCoordinatorBounds.MAX_TRACKED_BATCHES,
        child_journal: BatchChildTransitionRecorder | None = None,
        drain_seconds: float = BatchCoordinatorBounds.DEFAULT_DRAIN_SECONDS,
    ) -> None:
        self._permits = permits
        self._permit_scopes = permit_scopes
        self._live_allowance = live_allowance
        self._clock = clock if clock is not None else self._utc_now
        self._max_tracked_batches = max(1, max_tracked_batches)
        self._child_journal = child_journal
        self._drain_seconds = max(0.0, drain_seconds)
        self._batches: dict[str, _BatchState] = {}
        self._disposed = False
        self._cancelled = False
        self._withheld: frozenset[str] = frozenset()
        self._running_children = 0
        self._quiescent = asyncio.Event()
        self._quiescent.set()

    @property
    def disposed(self) -> bool:
        """Return whether this run's coordinator has been torn down."""

        return self._disposed

    @property
    def cancelled(self) -> bool:
        """Return whether this run has stopped admitting new children."""

        return self._cancelled

    @property
    def running_children(self) -> int:
        """Return how many child bodies are currently inside the coordinator."""

        return self._running_children

    @property
    def tracked_batches(self) -> int:
        """Return how many batches currently hold execution state."""

        return len(self._batches)

    @property
    def withheld_operations(self) -> frozenset[str]:
        """Return every operation a durable restart decision forbade re-running."""

        return self._withheld

    def withhold_on_restart(self, operation_ids: Iterable[str]) -> None:
        """Forbid re-running the named children, whatever a replayed turn asks.

        This is the *execution-side* half of F6.6's never-replay-a-started-write
        rule. The planner establishes the rule from durable facts; this makes it
        binding on the only path a child can reach a connector, so a restarted
        graph re-emitting an identical tool call cannot route around the finding
        by simply asking again.

        Union rather than replacement, and there is deliberately no way to
        un-withhold. A restart decision is derived from durable evidence that
        does not improve while the run is going, so every route that could
        narrow this set is a route by which a withheld write becomes runnable —
        and none of them is worth the throughput.

        Refusal happens *before* the segment gate and *before* any dispatch
        intent is journalled, which is what keeps a withheld child's evidence
        intact: it still reads as never-started to the next restart rather than
        being downgraded to indeterminate by the act of refusing it.
        """

        self._withheld |= frozenset(
            operation_id for operation_id in operation_ids if operation_id
        )

    def begin(self, plan: DurableBatchPlan) -> None:
        """Register one already-durable plan as executable.

        Re-registering the identical durable plan is idempotent, matching the
        journal's own idempotency: a retried turn converges rather than
        conflicting. Binding a *different* plan to a live batch id is a fault.

        On a disposed coordinator this registers nothing, so every later
        :meth:`run_child` refuses with ``REFUSED_DISPOSED`` instead of raising
        into a shutdown race.
        """

        if self._disposed:
            return
        existing = self._batches.get(plan.batch_id)
        if existing is not None:
            if existing.plan.record.plan_digest != plan.record.plan_digest:
                raise BatchCoordinatorError(BatchCoordinatorMessages.PLAN_CONFLICT)
            return
        self._prune()
        if len(self._batches) >= self._max_tracked_batches:
            raise BatchCoordinatorError(BatchCoordinatorMessages.TRACKING_EXHAUSTED)
        self._batches[plan.batch_id] = _BatchState(plan, self._now())

    async def run_child(
        self,
        *,
        batch_id: str,
        operation_id: str,
        runner: BatchChildRunner,
    ) -> BatchChildResult:
        """Gate, admit, and run one framework-started child coroutine.

        This is the whole execution surface. ``runner`` is awaited only when the
        durable plan, the segment gate, and the permit table have all admitted
        the child, so a caller cannot accidentally overlap work by forgetting to
        check an admission flag.

        Ordinary refusals come back as a :class:`BatchChildResult` whose status
        is ``REFUSED``; a child exception is captured on the result so one
        child's failure cannot erase a sibling's success. ``CancelledError``
        always propagates after the child is recorded ``INDETERMINATE``.
        """

        batch = self._batches.get(batch_id)
        if batch is None:
            return self._detached_refusal(
                batch_id,
                operation_id,
                BatchAdmissionOutcome.REFUSED_UNKNOWN_BATCH,
            )
        state = batch.children.get(operation_id)
        if state is None:
            return self._detached_refusal(
                batch_id,
                operation_id,
                BatchAdmissionOutcome.REFUSED_UNKNOWN_OPERATION,
            )
        if state.status is not BatchChildStatus.PENDING:
            return self._refused_copy(
                state,
                BatchAdmissionOutcome.REFUSED_ALREADY_SETTLED,
            )
        if operation_id in self._withheld:
            # Settled here rather than raised so the batch's segment accounting
            # stays whole: a withheld child that never reached a terminal state
            # would hold its segment open and strand every sibling behind it.
            self._settle(
                batch,
                state,
                BatchChildStatus.REFUSED,
                admission=BatchAdmissionOutcome.REFUSED_WITHHELD_ON_RESTART,
            )
            return state.result()

        gate = await self._wait_for_gate(batch, state)
        if not gate.admitted:
            self._settle(batch, state, BatchChildStatus.REFUSED, admission=gate)
            return state.result()
        return await self._run_admitted(batch, state, runner)

    def identities(self, batch_id: str) -> tuple[BatchChildIdentity, ...]:
        """Return every planned child of one batch, in input order."""

        batch = self._require(batch_id)
        return tuple(batch.children[key].identity for key in batch.order)

    def planned_allowance(
        self,
        *,
        batch_id: str,
        operation_id: str,
    ) -> ConcurrencyAllowance:
        """Return the width one planned child may overlap at, before admission.

        :class:`BatchChildAdmission` also carries an ``effective_allowance``, but
        only *after* the child has been gated and permitted — which is too late
        for a caller that must decide the width of the seam the child will be
        admitted *through*. That is the position the graph tool seam is in: the
        Step-2 admission gate runs before this coordinator sees the child at all,
        so it needs the number now.

        This is the same fold :meth:`_run_admitted` uses — segment ∧ plan ∧ live
        kill switch — deliberately reusing ``_effective_allowance`` rather than
        restating it, so a pre-admission answer and an admission answer cannot
        drift apart. It excludes only the permit table, which narrows again
        inside :meth:`run_child`; both narrow, and neither can widen the other.

        An unknown batch, an unknown operation, or a settled child answers
        serial. A width this method cannot positively establish is never one a
        caller may overlap on.
        """

        batch = self._batches.get(batch_id)
        if batch is None:
            return ConcurrencyAllowance.serial()
        state = batch.children.get(operation_id)
        if state is None or state.status is not BatchChildStatus.PENDING:
            return ConcurrencyAllowance.serial()
        return self._effective_allowance(batch, state.segment_index)

    def results(self, batch_id: str) -> tuple[BatchChildResult, ...]:
        """Return one batch's results in **input order**.

        Completion order is preserved separately, on each outcome's
        ``completed_at``. Both orderings are true at once and neither is derived
        from the other.
        """

        batch = self._require(batch_id)
        return tuple(batch.children[key].result() for key in batch.order)

    def report(self, batch_id: str) -> BatchExecutionReport:
        """Return one batch's body-free execution status."""

        batch = self._require(batch_id)
        outcomes = tuple(batch.children[key].outcome() for key in batch.order)
        return BatchExecutionReport(
            batch_id=batch_id,
            status=self._status_of(batch),
            started_at=batch.started_at,
            completed_at=self._completed_at(batch),
            outcomes=outcomes,
        )

    async def cancel(
        self,
        *,
        reason: BatchCancellationReason = BatchCancellationReason.RUN_CANCELLED,
        drain_seconds: float | None = None,
    ) -> BatchCancellationReport:
        """Stop this run's batches and report, honestly, what that achieved.

        The four steps happen in this order for reasons that are not stylistic:

        1. **Admission stops first, and synchronously.** There is no ``await``
           between entering this method and every batch being marked stopped, so
           no child can slip through the gate while cancellation is "in
           progress". A parked child resolves to ``REFUSED_RUN_CANCELLED``; it
           never ran, so nothing about it is in doubt.
        2. **Only cancellable reads are interrupted.** Interrupting a write does
           not undo it — it abandons it at an unknown point, which is strictly
           worse than letting it finish, because a finished write at least has a
           knowable outcome.
        3. **The drain is bounded.** Waiting longer never makes an uncancellable
           operation finish; it only postpones admitting we do not know. The
           bound is a real deadline, and :attr:`BatchCancellationReport.drained`
           records whether it was reached rather than inferring it.
        4. **Whatever is still running becomes indeterminate.** Not failed, not
           cancelled, not rolled back. The run stopped watching; the work may
           still be happening, and the report says exactly that.

        No external effect is undone here, and nothing in the returned report
        can be read as claiming one was. Cancellation is idempotent: a second
        call re-reports the same settled state.
        """

        requested_at = self._now()
        settled_before = self._settled_children()
        cancelled_in_place = self._stop_admission()
        for state in cancelled_in_place:
            if state.task is not None and not state.task.done():
                state.task.cancel()
        bound = (
            self._drain_seconds if drain_seconds is None else max(0.0, drain_seconds)
        )
        drained = await self._drain(bound)
        self._mark_in_flight_indeterminate()
        await self._record_indeterminate_children()
        return BatchCancellationReport(
            reason=reason,
            requested_at=requested_at,
            completed_at=self._now(),
            drained=drained,
            children=self._cancellation_children(settled_before),
        )

    def release(self, batch_id: str) -> None:
        """Drop one batch's execution state once its results are consumed."""

        batch = self._batches.pop(batch_id, None)
        if batch is not None:
            self._refuse_waiters(batch, BatchAdmissionOutcome.REFUSED_DISPOSED)

    def dispose(self) -> None:
        """Drop every batch when the run ends.

        Parked children resolve to ``REFUSED_DISPOSED`` rather than hanging, and
        later calls are refused rather than raised, so a shutdown race cannot
        look like a tool failure. Disposal is idempotent. The run's permit table
        is *not* disposed here: it is shared with the rest of the run and is
        owned by whoever created it.
        """

        self._disposed = True
        for batch in list(self._batches.values()):
            self._refuse_waiters(batch, BatchAdmissionOutcome.REFUSED_DISPOSED)
        self._batches.clear()

    async def _wait_for_gate(
        self,
        batch: _BatchState,
        state: _ChildState,
    ) -> BatchAdmissionOutcome:
        """Block this coroutine until the durable plan admits it."""

        immediate = self._immediate_refusal(batch)
        if immediate is not None:
            return immediate
        segment_index = state.segment_index
        if segment_index == batch.cursor and batch.running[segment_index] < self._width(
            batch, segment_index
        ):
            batch.running[segment_index] += 1
            state.holds_slot = True
            return BatchAdmissionOutcome.ADMITTED

        wait_seconds = self._wait_seconds(batch)
        if wait_seconds <= 0.0:
            return BatchAdmissionOutcome.REFUSED_DEADLINE
        waiter = _SegmentWaiter(state, asyncio.get_running_loop().create_future())
        batch.waiters.append(waiter)
        try:
            async with asyncio.timeout(wait_seconds):
                await waiter.future
        except TimeoutError:
            self._discard(batch, waiter)
            return BatchAdmissionOutcome.REFUSED_DEADLINE
        except asyncio.CancelledError:
            self._discard(batch, waiter)
            self._settle(
                batch,
                state,
                BatchChildStatus.REFUSED,
                admission=BatchAdmissionOutcome.REFUSED_CALLER_CANCELLED,
            )
            raise
        return waiter.outcome or BatchAdmissionOutcome.REFUSED_DISPOSED

    async def _run_admitted(
        self,
        batch: _BatchState,
        state: _ChildState,
        runner: BatchChildRunner,
    ) -> BatchChildResult:
        """Acquire a permit for an already-gated child and run its body."""

        allowance = self._effective_allowance(batch, state.segment_index)
        try:
            request = self._permit_request(batch, state, allowance)
        except BatchCoordinatorError:
            self._settle(
                batch,
                state,
                BatchChildStatus.REFUSED,
                admission=BatchAdmissionOutcome.REFUSED_PERMIT,
            )
            raise
        async with self._permits.acquire(request) as lease:
            if not lease.admitted:
                self._settle(
                    batch,
                    state,
                    BatchChildStatus.REFUSED,
                    admission=BatchAdmissionOutcome.REFUSED_PERMIT,
                    permit_outcome=lease.outcome,
                )
                return state.result()
            if not await self._authorize_dispatch(batch, state, lease):
                return state.result()
            state.status = BatchChildStatus.RUNNING
            state.admitted_at = self._now()
            state.permit_outcome = lease.outcome
            self._note_running(state)
            # The task is captured so cancellation can reach a child the
            # framework started and this module merely gated. Only cancellable
            # reads are ever cancelled through it.
            state.task = asyncio.current_task()
            admission = BatchChildAdmission(
                identity=state.identity,
                outcome=BatchAdmissionOutcome.ADMITTED,
                effective_allowance=allowance,
                permit=lease,
                admitted_at=state.admitted_at,
            )
            try:
                value = await runner(admission)
            except asyncio.CancelledError as exc:
                # No journal append here on purpose. This coroutine is being
                # torn down, so any await it starts is cancelled too — and the
                # record it would write is the one whose absence already means
                # "indeterminate". :meth:`cancel` writes it from a healthy task
                # instead, where the write can actually finish.
                self._settle(
                    batch,
                    state,
                    BatchChildStatus.INDETERMINATE,
                    admission=BatchAdmissionOutcome.ADMITTED,
                    permit_outcome=lease.outcome,
                    error=exc,
                )
                raise
            except Exception as exc:
                if self._settle(
                    batch,
                    state,
                    BatchChildStatus.FAILED,
                    admission=BatchAdmissionOutcome.ADMITTED,
                    permit_outcome=lease.outcome,
                    error=exc,
                ):
                    await self._record_settled(state, BatchChildDisposition.FAILED)
            else:
                if self._settle(
                    batch,
                    state,
                    BatchChildStatus.SUCCEEDED,
                    admission=BatchAdmissionOutcome.ADMITTED,
                    permit_outcome=lease.outcome,
                    value=value,
                ):
                    await self._record_settled(state, BatchChildDisposition.SUCCEEDED)
        return state.result()

    async def _authorize_dispatch(
        self,
        batch: _BatchState,
        state: _ChildState,
        lease: PermitLease,
    ) -> bool:
        """Make one child's dispatch durable, or refuse to dispatch it.

        This is the single ordering the whole recovery story rests on: the
        intent append is awaited to completion *before* the body, so a process
        that dies with no intent record cannot have run the body. A journal that
        cannot record the intent therefore has to refuse the dispatch — running
        anyway would produce exactly the child a restart wrongly believes never
        started, which is the one mistake this lane exists to prevent.

        The re-check afterwards closes the window the append itself opens: a
        cancellation that arrives while the record is being written settles the
        child before it starts, and the body must not run behind it.
        """

        if self._child_journal is not None:
            try:
                await self._child_journal.record_dispatch_intent(
                    batch_id=state.identity.batch_id,
                    operation_id=state.identity.operation_id,
                )
            except BatchJournalError:
                self._settle(
                    batch,
                    state,
                    BatchChildStatus.REFUSED,
                    admission=BatchAdmissionOutcome.REFUSED_UNDURABLE,
                    permit_outcome=lease.outcome,
                )
                return False
        if state.status.settled:
            return False
        stop = self._immediate_refusal(batch)
        if stop is not None:
            self._settle(
                batch,
                state,
                BatchChildStatus.REFUSED,
                admission=stop,
                permit_outcome=lease.outcome,
            )
            return False
        return True

    async def _record_settled(
        self,
        state: _ChildState,
        disposition: BatchChildDisposition,
    ) -> None:
        """Record one child's outcome, best-effort and deliberately so.

        Losing this append costs observability, not correctness: a child with a
        durable intent and no durable outcome reads as indeterminate, which is
        precisely what an unrecorded outcome is. Nothing downstream has to
        compensate for a failure here, which is why it is swallowed rather than
        raised into a tool result that has already been produced.
        """

        if self._child_journal is None:
            return
        try:
            await self._child_journal.record_settled(
                batch_id=state.identity.batch_id,
                operation_id=state.identity.operation_id,
                disposition=disposition,
            )
        except BatchJournalError:
            return

    def _permit_request(
        self,
        batch: _BatchState,
        state: _ChildState,
        allowance: ConcurrencyAllowance,
    ) -> PermitAcquisitionRequest:
        """Build the one permit request this child is allowed to make.

        The factory chooses pools only. Width comes from the already-narrowed
        segment allowance and the wait budget from the batch deadline, so a
        permit request can never authorize more overlap than the plan did.
        """

        wait_seconds = self._wait_seconds(batch)
        try:
            scopes = self._permit_scopes(state.identity)
            return PermitAcquisitionRequest(
                scopes=scopes,
                wait_mode=PermitWaitMode.QUEUE,
                timeout_seconds=wait_seconds,
                max_parallelism=allowance.effective_max_parallelism,
            )
        except Exception as exc:
            raise BatchCoordinatorError(
                BatchCoordinatorMessages.PERMIT_SCOPES_REJECTED
            ) from exc

    def _settle(
        self,
        batch: _BatchState,
        state: _ChildState,
        status: BatchChildStatus,
        *,
        admission: BatchAdmissionOutcome,
        permit_outcome: PermitOutcome | None = None,
        value: object | None = None,
        error: BaseException | None = None,
    ) -> bool:
        """Record one child's terminal state and release its segment slot.

        Returns whether this call is the one that settled the child. A late
        completion for a child cancellation already ruled indeterminate returns
        ``False``: the first durable answer stands, and a body that finished
        after the run stopped waiting does not get to rewrite it.
        """

        if state.status.settled:
            return False
        segment_index = state.segment_index
        if state.holds_slot:
            batch.running[segment_index] -= 1
            state.holds_slot = False
        state.status = status
        state.admission = admission
        if permit_outcome is not None:
            state.permit_outcome = permit_outcome
        state.value = value
        state.error = error
        state.completed_at = self._now()
        batch.settled[segment_index] += 1
        self._clear_running(state)
        if (
            status
            in (
                BatchChildStatus.FAILED,
                BatchChildStatus.INDETERMINATE,
            )
            and batch.failure_policy is not BatchFailurePolicy.COLLECT_ALL
        ):
            self._stop(batch)
        self._pump(batch)
        return True

    def _note_running(self, state: _ChildState) -> None:
        """Count one child body as inside the coordinator."""

        state.running_noted = True
        self._running_children += 1
        self._quiescent.clear()

    def _clear_running(self, state: _ChildState) -> None:
        """Stop counting one child body, waking a drain once none are left."""

        if not state.running_noted:
            return
        state.running_noted = False
        self._running_children -= 1
        if self._running_children <= 0:
            self._running_children = 0
            self._quiescent.set()

    def _stop(
        self,
        batch: _BatchState,
        *,
        outcome: BatchAdmissionOutcome = BatchAdmissionOutcome.REFUSED_BATCH_STOPPED,
    ) -> None:
        """Stop admitting new children, leaving running ones alone.

        Both the failure-policy stop and the cancellation stop go through here,
        because "admit nothing further" is the same operation either way. What
        differs is only the outcome a refused child reports, so a caller can
        tell a sibling's failure from an operator's cancellation without
        inspecting anything else, and whether in-flight work is then cancelled —
        which :meth:`cancel` does afterwards and this method never does.
        """

        if batch.stopped:
            return
        batch.stopped = True
        self._refuse_waiters(batch, outcome)
        for state in batch.children.values():
            if state.status is BatchChildStatus.PENDING:
                self._settle(
                    batch,
                    state,
                    BatchChildStatus.REFUSED,
                    admission=outcome,
                )

    def _pump(self, batch: _BatchState) -> None:
        """Admit every child the plan now allows, in input order."""

        if self._disposed or batch.stopped:
            return
        while batch.cursor < len(batch.segments) and batch.settled[batch.cursor] == len(
            batch.segments[batch.cursor].operation_ids
        ):
            batch.cursor += 1
        if batch.cursor >= len(batch.segments):
            return
        width = self._width(batch, batch.cursor)
        for waiter in sorted(
            batch.waiters,
            key=lambda entry: entry.state.input_index,
        ):
            if waiter.state.segment_index != batch.cursor:
                continue
            if batch.running[batch.cursor] >= width:
                break
            batch.waiters.remove(waiter)
            batch.running[batch.cursor] += 1
            waiter.state.holds_slot = True
            waiter.outcome = BatchAdmissionOutcome.ADMITTED
            if not waiter.future.done():
                waiter.future.set_result(None)

    def _discard(self, batch: _BatchState, waiter: _SegmentWaiter) -> None:
        """Drop a timed-out or cancelled waiter without leaking a segment slot."""

        if waiter in batch.waiters:
            batch.waiters.remove(waiter)
        if waiter.state.holds_slot:
            batch.running[waiter.state.segment_index] -= 1
            waiter.state.holds_slot = False
        self._pump(batch)

    def _refuse_waiters(
        self,
        batch: _BatchState,
        outcome: BatchAdmissionOutcome,
    ) -> None:
        """Resolve every parked child with a typed refusal instead of a hang."""

        for waiter in list(batch.waiters):
            batch.waiters.remove(waiter)
            waiter.outcome = outcome
            if not waiter.future.done():
                waiter.future.set_result(None)

    def _stop_admission(self) -> tuple[_ChildState, ...]:
        """Close every gate synchronously and return the reads worth cancelling.

        Nothing in here awaits, which is the property that makes "stop admitting
        new children immediately" literally true rather than approximately true.
        """

        self._cancelled = True
        cancellable: list[_ChildState] = []
        for batch in self._batches.values():
            batch.cancelled = True
            self._stop(batch, outcome=BatchAdmissionOutcome.REFUSED_RUN_CANCELLED)
            for state in batch.children.values():
                if state.status is not BatchChildStatus.RUNNING:
                    continue
                if not state.cancellable:
                    continue
                state.cancel_requested = True
                cancellable.append(state)
        return tuple(cancellable)

    async def _drain(self, seconds: float) -> bool:
        """Wait, boundedly, for every running child to settle.

        A zero bound is a real answer, not a degenerate one: it means "report
        what is true now". It also makes the bound observable without any test
        having to spend wall-clock time proving that a deadline exists.
        """

        if self._running_children == 0:
            return True
        if seconds <= 0.0:
            return False
        try:
            async with asyncio.timeout(seconds):
                while self._running_children:
                    await self._quiescent.wait()
        except TimeoutError:
            return False
        return True

    def _mark_in_flight_indeterminate(self) -> None:
        """Rule every child still running after the drain indeterminate."""

        for batch in self._batches.values():
            for state in list(batch.children.values()):
                if state.status is BatchChildStatus.RUNNING:
                    self._settle(
                        batch,
                        state,
                        BatchChildStatus.INDETERMINATE,
                        admission=BatchAdmissionOutcome.ADMITTED,
                    )

    async def _record_indeterminate_children(self) -> None:
        """Durably record every child whose outcome nobody can determine."""

        if self._child_journal is None:
            return
        for batch in self._batches.values():
            for state in batch.children.values():
                if state.status is BatchChildStatus.INDETERMINATE:
                    await self._record_settled(
                        state,
                        BatchChildDisposition.INDETERMINATE,
                    )

    def _settled_children(self) -> frozenset[tuple[str, str]]:
        """Return every child already terminal before cancellation began."""

        return frozenset(
            (batch_id, operation_id)
            for batch_id, batch in self._batches.items()
            for operation_id, state in batch.children.items()
            if state.status.settled
        )

    def _cancellation_children(
        self,
        settled_before: frozenset[tuple[str, str]],
    ) -> tuple[CancelledChild, ...]:
        """Describe every tracked child as cancellation actually left it."""

        return tuple(
            self._cancellation_child(batch_id, state, settled_before)
            for batch_id, batch in self._batches.items()
            for state in (batch.children[key] for key in batch.order)
        )

    @staticmethod
    def _cancellation_child(
        batch_id: str,
        state: _ChildState,
        settled_before: frozenset[tuple[str, str]],
    ) -> CancelledChild:
        """Describe one child, reading its state rather than its timing.

        The classification is driven by ``status`` first and timing second. A
        child that settled during the drain but settled *indeterminate* is not
        a completion, and ordering the checks the other way round would let a
        cancelled write be reported as one.
        """

        operation_id = state.identity.operation_id
        return CancelledChild(
            batch_id=batch_id,
            operation_id=operation_id,
            state=_cancellation_state(
                state,
                settled_before=(batch_id, operation_id) in settled_before,
            ),
            effect_certainty=ChildEffectCertainty.of(state.side_effect),
            cancel_requested=state.cancel_requested,
        )

    def _immediate_refusal(self, batch: _BatchState) -> BatchAdmissionOutcome | None:
        """Return the refusal that applies before a child may even wait."""

        if self._disposed:
            return BatchAdmissionOutcome.REFUSED_DISPOSED
        if batch.cancelled:
            return BatchAdmissionOutcome.REFUSED_RUN_CANCELLED
        if batch.stopped:
            return BatchAdmissionOutcome.REFUSED_BATCH_STOPPED
        if self._wait_seconds(batch) <= 0.0:
            return BatchAdmissionOutcome.REFUSED_DEADLINE
        return None

    def _effective_allowance(
        self,
        batch: _BatchState,
        segment_index: int,
    ) -> ConcurrencyAllowance:
        """Return the narrowest of the segment, plan, and live allowances.

        ``narrowed_by`` is a ``min`` over mode rank and width, so this fold can
        only ever lower a ceiling. A live source that reports a wider allowance
        than the durable plan changes nothing.

        The plan term is deliberately belt-and-braces and **no test can observe
        it**: :class:`BatchPlanner` already folds the batch allowance into every
        segment allowance it emits, and ``BatchPlanBoundRecord`` refuses to parse
        a record whose segment exceeds the effective batch allowance. Removing it
        is a mutation nothing catches. It stays because it is the one line that
        keeps the batch ceiling authoritative if a segment ever reaches this
        coordinator from a source other than that planner.
        """

        allowance = batch.segments[segment_index].allowance.narrowed_by(
            batch.plan.allowance
        )
        if self._live_allowance is not None:
            allowance = allowance.narrowed_by(self._live_allowance())
        return allowance

    def _width(self, batch: _BatchState, segment_index: int) -> int:
        """Return how many children of one segment may overlap right now."""

        return self._effective_allowance(batch, segment_index).effective_max_parallelism

    def _wait_seconds(self, batch: _BatchState) -> float:
        """Return the bounded admission budget for this batch."""

        if batch.deadline is None:
            return BatchCoordinatorBounds.DEFAULT_ADMISSION_WAIT_SECONDS
        remaining = (batch.deadline - self._now()).total_seconds()
        return min(remaining, BatchCoordinatorBounds.DEFAULT_ADMISSION_WAIT_SECONDS)

    def _status_of(self, batch: _BatchState) -> BatchExecutionStatus:
        """Return the batch status implied by its children."""

        if not batch.all_settled:
            return BatchExecutionStatus.IN_PROGRESS
        statuses = [child.status for child in batch.children.values()]
        if all(status is BatchChildStatus.SUCCEEDED for status in statuses):
            return BatchExecutionStatus.COMPLETED
        if batch.cancelled:
            return BatchExecutionStatus.CANCELLED
        if any(status is BatchChildStatus.SUCCEEDED for status in statuses):
            return BatchExecutionStatus.PARTIAL
        return BatchExecutionStatus.FAILED

    @staticmethod
    def _completed_at(batch: _BatchState) -> datetime | None:
        """Return when the last child actually settled, once all of them have."""

        if not batch.all_settled:
            return None
        moments = [
            child.completed_at
            for child in batch.children.values()
            if child.completed_at is not None
        ]
        return max(moments) if moments else None

    def _require(self, batch_id: str) -> _BatchState:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise BatchCoordinatorError(BatchCoordinatorMessages.UNKNOWN_BATCH)
        return batch

    def _prune(self) -> None:
        """Drop fully settled batches so state cannot accumulate in a long run."""

        for batch_id, batch in list(self._batches.items()):
            if batch.all_settled and not batch.waiters:
                del self._batches[batch_id]

    def _detached_refusal(
        self,
        batch_id: str,
        operation_id: str,
        outcome: BatchAdmissionOutcome,
    ) -> BatchChildResult:
        """Return a refusal for work no durable plan accounts for."""

        return BatchChildResult(
            outcome=BatchChildOutcome(
                identity=BatchChildIdentity.unplanned(
                    batch_id=batch_id,
                    operation_id=operation_id,
                ),
                status=BatchChildStatus.REFUSED,
                admission=outcome,
                completed_at=self._now(),
            )
        )

    def _refused_copy(
        self,
        state: _ChildState,
        outcome: BatchAdmissionOutcome,
    ) -> BatchChildResult:
        """Refuse a second coroutine for a child that already has one."""

        return BatchChildResult(
            outcome=BatchChildOutcome(
                identity=state.identity,
                status=BatchChildStatus.REFUSED,
                admission=outcome,
                completed_at=self._now(),
            )
        )

    def _now(self) -> datetime:
        moment = self._clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise BatchCoordinatorError(BatchCoordinatorMessages.NAIVE_CLOCK)
        return moment

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)


__all__ = (
    "BatchAdmissionOutcome",
    "BatchAllowanceSupplier",
    "BatchChildAdmission",
    "BatchChildIdentity",
    "BatchChildOutcome",
    "BatchChildResult",
    "BatchChildRunner",
    "BatchChildStatus",
    "BatchClock",
    "BatchCoordinatorBounds",
    "BatchCoordinatorError",
    "BatchCoordinatorMessages",
    "BatchExecutionCoordinator",
    "BatchExecutionReport",
    "BatchExecutionStatus",
    "BatchPermitScopeFactory",
)
