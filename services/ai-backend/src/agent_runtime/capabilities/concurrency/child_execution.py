"""F6.5 — an admitted batch child re-enters the *ordinary* Operation Gateway.

F6.3 built :class:`~agent_runtime.capabilities.concurrency.batch_coordinator.BatchExecutionCoordinator`,
which decides *whether and when* a planned child may run and then hands the
decision to a ``BatchChildRunner``. This module is that runner, and its single
design rule is the one F3.5 established for the capability-invoke path: **reuse
the one dispatch route, do not build a second one.**

Concretely, a child reaches the gateway through the same injected dispatcher
every model-emitted tool call already flows through
(:class:`~agent_runtime.capabilities.mcp.middleware.call_tool.CallMcpTool`).
That dispatcher — not this module — re-resolves the server card, re-checks
permissions, builds the ``OperationRequest``, persists canonical arguments,
composes the provider adapter, and calls ``gateway.invoke``. So a batched child
is not merely *equivalent* to a tool call the model made alone; it **is** one,
and classification, gate resolution, effect staging, approval, usage
accounting, citation projection, and audit identity are the same code rather
than a parallel implementation free to drift.

Consequently this module imports no gateway, adapter, request factory,
descriptor registry, stager, or MCP registry. Everything that could constitute a
second dispatch path is absent by construction, and a test asserts the import
set stays that way.

What this module *does* own is the four things that make a child's re-entry its
own operation rather than a shared one:

- **Identity derived from the parent turn.** The child's
  :class:`~agent_runtime.execution.call_identity.RuntimeToolCallIdentity` is
  rebuilt from the verified run binding plus the child's own provider tool-call
  id — the same construction
  :class:`~agent_runtime.capabilities.middleware.runtime_tool_control.RuntimeControlMiddleware`
  performs for a solo call. Binding it around the dispatch is what makes the
  gateway allocate the operation id the durable plan already named, so the
  batch journal and the work ledger cannot disagree about which operation ran.
  The derivation is checked, not assumed: an id that does not match the plan
  refuses before dispatch rather than running under a name nothing recorded.

- **A per-child deadline that only narrows.** The batch deadline and the
  child's own are composed by taking the nearer, converted to a remaining
  budget against the injected clock. A deadline already past refuses *before*
  dispatch, so "no external change was made" is a fact rather than a hope.

- **A per-child cancellation scope.** ``asyncio.timeout`` bounds the child and
  nothing else. Cancellation itself belongs to F6.6: an outer
  ``CancelledError`` is never caught, converted, or shielded here, and the
  permit release path is left entirely to the coordinator's ``async with``.

- **A real completion timestamp.** ``completed_at`` is read from the clock at
  the moment the dispatch returned. The coordinator returns results in *input*
  order; this records *completion* order. Both facts are true at once and
  neither is derived from the other.

Isolation between siblings is structural rather than defended: one child's run
touches no state another child's run can observe. The executor holds only its
dispatcher, its immutable work table, its clock, and the batch deadline — there
is no accumulator, no first-error flag, and no shared buffer through which a
failing sibling could reach a completed one's result.

The line between "returned a receipt" and "raised" is deliberate and narrow:
**a receipt exists exactly when a gateway operation exists.** A blocked, staged,
or failed *disposition* is a real operation whose answer happened to be no, and
the gateway itself returns those rather than raising — so they come back as a
receipt, exactly as they would for a solo call. Work that never reached the
gateway at all — unknown, mis-identified, out of time, or dispatch-faulted —
raises :class:`BatchChildExecutionError`, which the coordinator records as a
failed child without disturbing any sibling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from agent_runtime.capabilities.concurrency.batch_coordinator import (
    BatchChildAdmission,
    BatchClock,
)
from agent_runtime.capabilities.concurrency.batch_journal import BatchJournalLimits
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
)


class BatchChildExecutionBounds:
    """Hard, content-free bounds on one run's child work table."""

    # A run can never hold more child work than a batch may plan operations.
    MAX_TRACKED_CHILDREN: Final[int] = BatchJournalLimits.MAX_OPERATIONS
    MAX_IDENTIFIER_LENGTH: Final[int] = BatchJournalLimits.MAX_TRANSPORT_ID_LENGTH


class BatchChildExecutionReason(StrEnum):
    """Closed, content-free reason one child never produced a gateway receipt.

    Every member describes work that did **not** reach the connector, so a
    caller reporting any of them can say "no external change was made" without
    qualification. A disposition the gateway itself returned — blocked, staged,
    or failed — is never in this family: it is a
    :class:`BatchChildDispatchStatus` on a returned receipt.
    """

    NOT_ADMITTED = "not_admitted"
    WORK_UNAVAILABLE = "work_unavailable"
    IDENTITY_UNAVAILABLE = "identity_unavailable"
    IDENTITY_MISMATCH = "identity_mismatch"
    DEADLINE_EXPIRED = "deadline_expired"
    DISPATCH_FAILED = "dispatch_failed"
    DISPATCH_MALFORMED = "dispatch_malformed"


class BatchChildExecutionMessages:
    """Public, content-free text for every child-execution fault.

    Authored per reason rather than interpolated: the identifiers involved
    arrive from a durable plan and a connector, and neither belongs in a string
    a model or an HTTP client may read.
    """

    NOT_ADMITTED = "The batch child was not admitted and did not run."
    WORK_UNAVAILABLE = "No dispatch coordinates are registered for this batch child."
    IDENTITY_UNAVAILABLE = "The batch child has no verified run identity to run under."
    IDENTITY_MISMATCH = "The batch child's operation identity does not match its plan."
    DEADLINE_EXPIRED = "The batch child ran out of time; no external change was made."
    DISPATCH_FAILED = "The batch child could not be dispatched."
    DISPATCH_MALFORMED = "The batch child's dispatch produced an unusable result."

    NAIVE_CLOCK = "Batch child execution timestamps must be timezone-aware."
    DUPLICATE_WORK = "A batch child may register dispatch coordinates only once."
    WORK_EXHAUSTED = "Too many batch children are registered for this run."

    BY_REASON: Final[dict[BatchChildExecutionReason, str]] = {
        BatchChildExecutionReason.NOT_ADMITTED: NOT_ADMITTED,
        BatchChildExecutionReason.WORK_UNAVAILABLE: WORK_UNAVAILABLE,
        BatchChildExecutionReason.IDENTITY_UNAVAILABLE: IDENTITY_UNAVAILABLE,
        BatchChildExecutionReason.IDENTITY_MISMATCH: IDENTITY_MISMATCH,
        BatchChildExecutionReason.DEADLINE_EXPIRED: DEADLINE_EXPIRED,
        BatchChildExecutionReason.DISPATCH_FAILED: DISPATCH_FAILED,
        BatchChildExecutionReason.DISPATCH_MALFORMED: DISPATCH_MALFORMED,
    }


class BatchChildExecutionError(RuntimeError):
    """One child never became a gateway operation, for a closed reason.

    This is raised rather than returned so the coordinator records the child
    ``FAILED`` instead of ``SUCCEEDED``: a batch report that called an
    undispatched child a success would be a lie a later lane has to discover at
    runtime. The coordinator captures it on that child's result alone, so a
    sibling that already completed is untouched.
    """

    def __init__(self, reason: BatchChildExecutionReason) -> None:
        safe_message = BatchChildExecutionMessages.BY_REASON[reason]
        super().__init__(safe_message)
        self.reason = reason
        self.safe_message = safe_message


class BatchChildExecutorMisconfigured(RuntimeError):
    """A genuine construction fault, distinct from any child's outcome.

    The split matches the one
    :mod:`agent_runtime.capabilities.concurrency.errors` already draws: a
    duplicated child, an oversized work table, and a naive clock are all
    programming faults raised before any child runs, so they must never be
    mistakable for a per-child refusal. Checking the clock here rather than at
    settlement matters for honesty too — a timestamp fault discovered *after* a
    dispatch could not truthfully report that no external change was made.
    """

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class BatchChildDispatchStatus(StrEnum):
    """Closed projection of the gateway disposition one child produced.

    ``UNKNOWN`` is the deliberate landing place for a disposition this
    vocabulary does not recognize. Mapping an unrecognized status onto
    ``COMPLETED`` would let a future gateway outcome be read as a finished
    external effect, so the unknown case is never successful.
    """

    COMPLETED = "completed"
    STAGED = "staged"
    BLOCKED = "blocked"
    FAILED = "failed"
    HELD = "held"
    UNKNOWN = "unknown"

    @property
    def produced_result(self) -> bool:
        """Return whether this child's operation produced a stored result."""

        return self is BatchChildDispatchStatus.COMPLETED

    @classmethod
    def of(cls, raw: object) -> "BatchChildDispatchStatus":
        """Map a dispatcher status onto this closed vocabulary."""

        if not isinstance(raw, str):
            return cls.UNKNOWN
        normalized = raw.strip().casefold()
        if normalized in {"completed", "succeeded"}:
            return cls.COMPLETED
        try:
            return cls(normalized)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class BatchChildWork:
    """The dispatch coordinates and turn position of one planned child.

    Deliberately a runtime dataclass rather than a ``RuntimeContract``, for the
    same reason
    :class:`~agent_runtime.capabilities.concurrency.batch_coordinator.BatchChildResult`
    is: ``arguments`` is a body, and bodies do not belong in a type anything
    downstream might be tempted to journal. The durable plan names this child by
    ``operation_id`` and nothing else.

    ``model_tool_call_id`` is the provider's own id for the model tool call this
    child *is*. It is the seed of the derived identity and it is also handed to
    the dispatcher, so a batched child's citations bind to the same tool call a
    solo call's would.
    """

    operation_id: str
    server_name: str
    tool_name: str
    arguments: Mapping[str, Any]
    model_tool_call_id: str
    model_turn: int
    execution_scope: str = "supervisor"
    deadline_at: datetime | None = None

    def identity(self) -> RuntimeToolCallIdentity | None:
        """Return this child's identity, derived from the active parent turn.

        ``None`` means no verified run binding is active, which the executor
        treats as fail-closed: without it the gateway would allocate a random
        operation id the durable plan never named.
        """

        return RuntimeToolCallIdentity.from_current(
            execution_scope=self.execution_scope,
            model_turn=self.model_turn,
            model_tool_call_id=self.model_tool_call_id,
        )

    def dispatch_input(self) -> dict[str, Any]:
        """Return the dispatcher input a solo call for this child would carry."""

        return {
            "server_name": self.server_name,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "tool_call_id": self.model_tool_call_id,
        }

    def deadline_with(self, batch_deadline: datetime | None) -> datetime | None:
        """Return the nearer of this child's deadline and the batch's.

        Composition is ``min``, so a child's time budget can only ever narrow
        relative to the batch it belongs to.
        """

        if self.deadline_at is None:
            return batch_deadline
        if batch_deadline is None:
            return self.deadline_at
        return min(self.deadline_at, batch_deadline)


@dataclass(frozen=True, slots=True)
class BatchChildDispatch:
    """One child's body-free gateway receipt plus the dispatcher's own result.

    ``result`` is the dispatcher's output **verbatim**. Reshaping it here would
    reintroduce exactly the drift this lane exists to prevent: whatever a solo
    call would have handed the framework is what a batched child hands back.

    ``admitted_at`` and ``effective_max_parallelism`` are copied from the
    admission so an auditor reads the width that actually applied rather than
    the width the plan hoped for, and ``completed_at`` is the moment the
    dispatch returned — never a position in any ordering.
    """

    operation_id: str
    status: BatchChildDispatchStatus
    admitted_at: datetime
    completed_at: datetime
    effective_max_parallelism: int
    result_ref: str | None
    result: Mapping[str, Any]

    @property
    def succeeded(self) -> bool:
        """Return whether this child's operation completed with a result."""

        return self.status.produced_result


@runtime_checkable
class BatchChildDispatchPort(Protocol):
    """The one dispatcher an admitted child re-enters.

    Structural on purpose: the executor is composed with the real
    ``CallMcpTool`` in production without this module importing it, which is
    what keeps a second dispatch path from existing.
    """

    async def ainvoke(self, raw_input: Mapping[str, Any], /) -> Mapping[str, Any]:
        """Run one ordinary tool call and return its result envelope."""


@runtime_checkable
class BatchChildWorkPort(Protocol):
    """Resolve the dispatch coordinates a durable plan's child id refers to."""

    def work_for(self, operation_id: str) -> BatchChildWork | None:
        """Return the registered work for ``operation_id``, or ``None``."""


class RunScopedBatchChildWork:
    """The only dispatch coordinates one run's children may run from.

    Immutable after construction and bounded by
    :class:`BatchChildExecutionBounds`, so nothing can grow the table — or swap
    a child's target — between the moment the plan became durable and the moment
    the child is admitted.
    """

    __slots__ = ("_by_operation",)

    def __init__(self, work: Iterable[BatchChildWork]) -> None:
        registered: dict[str, BatchChildWork] = {}
        for item in work:
            if item.operation_id in registered:
                raise BatchChildExecutorMisconfigured(
                    BatchChildExecutionMessages.DUPLICATE_WORK
                )
            if len(registered) >= BatchChildExecutionBounds.MAX_TRACKED_CHILDREN:
                raise BatchChildExecutorMisconfigured(
                    BatchChildExecutionMessages.WORK_EXHAUSTED
                )
            registered[item.operation_id] = item
        self._by_operation = registered

    def __len__(self) -> int:
        return len(self._by_operation)

    def work_for(self, operation_id: str) -> BatchChildWork | None:
        """Return the registered work for ``operation_id``, or ``None``."""

        return self._by_operation.get(operation_id)


class GatewayBatchChildExecutor:
    """Run one admitted batch child as an ordinary Operation Gateway call.

    :meth:`run` satisfies
    :data:`~agent_runtime.capabilities.concurrency.batch_coordinator.BatchChildRunner`
    exactly, so composition is a single hand-off::

        executor = GatewayBatchChildExecutor(
            dispatcher=call_mcp_tool,
            work=RunScopedBatchChildWork(children),
            deadline_at=plan.record.deadline_at,
        )
        result = await coordinator.run_child(
            batch_id=batch_id,
            operation_id=operation_id,
            runner=executor.run,
        )

    One instance per run and per batch deadline. The executor is stateless
    between children by construction: it never records what a previous child
    did, which is why one child's failure cannot reach another child's result.
    """

    __slots__ = ("_clock", "_deadline_at", "_dispatcher", "_work")

    def __init__(
        self,
        *,
        dispatcher: BatchChildDispatchPort,
        work: BatchChildWorkPort,
        clock: BatchClock | None = None,
        deadline_at: datetime | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._work = work
        self._clock = clock if clock is not None else self._utc_now
        self._deadline_at = deadline_at
        self._require_aware_clock()

    async def run(self, admission: BatchChildAdmission) -> BatchChildDispatch:
        """Re-enter the gateway for one admitted child and receipt the result."""

        work, identity = self._resolve(admission)
        deadline = work.deadline_with(self._deadline_at)
        timeout_seconds = self._remaining_seconds(deadline)
        current = RuntimeCallContext.current()
        if current is not None and current.operation_id == identity.operation_id:
            # The coordinator was entered from inside this child's own tool-call
            # wrapper, which already bound this identity. Re-binding would
            # restart the inner-operation ordinal and hand the gateway an id the
            # durable plan never named.
            result = await self._dispatch(work, timeout_seconds)
        else:
            with RuntimeCallContext.bind(identity):
                result = await self._dispatch(work, timeout_seconds)
        return self._receipt(
            admission=admission,
            identity=identity,
            result=result,
            completed_at=self._now(),
        )

    def _resolve(
        self,
        admission: BatchChildAdmission,
    ) -> tuple[BatchChildWork, RuntimeToolCallIdentity]:
        """Return the child's work and parent-derived identity, or refuse.

        Every check here happens before anything is dispatched, so each refusal
        is a fact about work that never left the process.
        """

        if not admission.admitted:
            raise BatchChildExecutionError(BatchChildExecutionReason.NOT_ADMITTED)
        planned_id = admission.identity.operation_id
        work = self._work.work_for(planned_id)
        if work is None:
            raise BatchChildExecutionError(BatchChildExecutionReason.WORK_UNAVAILABLE)
        if work.operation_id != planned_id:
            raise BatchChildExecutionError(BatchChildExecutionReason.IDENTITY_MISMATCH)
        identity = work.identity()
        if identity is None:
            raise BatchChildExecutionError(
                BatchChildExecutionReason.IDENTITY_UNAVAILABLE
            )
        if identity.operation_id != planned_id:
            # The id the parent turn derives is not the id the durable plan
            # named. Running anyway would put an operation in the ledger that
            # the journal never accounted for.
            raise BatchChildExecutionError(BatchChildExecutionReason.IDENTITY_MISMATCH)
        return work, identity

    def _remaining_seconds(self, deadline: datetime | None) -> float | None:
        """Return the child's remaining budget, refusing one already spent."""

        if deadline is None:
            return None
        remaining = (deadline - self._now()).total_seconds()
        if remaining <= 0.0:
            raise BatchChildExecutionError(BatchChildExecutionReason.DEADLINE_EXPIRED)
        return remaining

    async def _dispatch(
        self,
        work: BatchChildWork,
        timeout_seconds: float | None,
    ) -> Mapping[str, Any]:
        """Enter the ordinary gateway through the one injected dispatcher."""

        payload = work.dispatch_input()
        try:
            if timeout_seconds is None:
                result = await self._dispatcher.ainvoke(payload)
            else:
                async with asyncio.timeout(timeout_seconds):
                    result = await self._dispatcher.ainvoke(payload)
        except TimeoutError as exc:
            raise BatchChildExecutionError(
                BatchChildExecutionReason.DEADLINE_EXPIRED
            ) from exc
        except asyncio.CancelledError:
            # Cancellation is F6.6's. Never absorb it, never relabel it, and
            # never delay the coordinator's permit release by handling it here.
            raise
        except Exception as exc:  # noqa: BLE001 - dispatcher detail stays internal.
            raise BatchChildExecutionError(
                BatchChildExecutionReason.DISPATCH_FAILED
            ) from exc
        if not isinstance(result, Mapping):
            raise BatchChildExecutionError(BatchChildExecutionReason.DISPATCH_MALFORMED)
        return result

    @classmethod
    def _receipt(
        cls,
        *,
        admission: BatchChildAdmission,
        identity: RuntimeToolCallIdentity,
        result: Mapping[str, Any],
        completed_at: datetime,
    ) -> BatchChildDispatch:
        """Project the dispatcher's result into this child's audit receipt."""

        body = result.get("output")
        body = body if isinstance(body, Mapping) else {}
        cls._require_planned_operation(body, identity=identity)
        admitted_at = admission.admitted_at
        if admitted_at is None:  # pragma: no cover - admission invariant
            raise BatchChildExecutionError(BatchChildExecutionReason.NOT_ADMITTED)
        status = BatchChildDispatchStatus.of(body.get("status"))
        return BatchChildDispatch(
            operation_id=identity.operation_id,
            status=status,
            admitted_at=admitted_at,
            completed_at=completed_at,
            effective_max_parallelism=(
                admission.effective_allowance.effective_max_parallelism
            ),
            result_ref=cls._result_ref(body),
            result=result,
        )

    @staticmethod
    def _require_planned_operation(
        body: Mapping[str, Any],
        *,
        identity: RuntimeToolCallIdentity,
    ) -> None:
        """Refuse a receipt whose operation is not the one the plan named.

        The dispatcher reports the operation id the gateway actually allocated.
        When it disagrees with the derived identity, the child ran under a name
        the durable plan never recorded, and claiming otherwise would make the
        journal and the ledger silently inconsistent.
        """

        reported = body.get("operation_id")
        if isinstance(reported, str) and reported and reported != identity.operation_id:
            raise BatchChildExecutionError(BatchChildExecutionReason.IDENTITY_MISMATCH)

    @staticmethod
    def _result_ref(body: Mapping[str, Any]) -> str | None:
        """Return the opaque handle the child's stored result lives behind."""

        candidate = body.get("result_ref")
        if not isinstance(candidate, str) or not candidate:
            return None
        if len(candidate) > BatchChildExecutionBounds.MAX_IDENTIFIER_LENGTH:
            return None
        return candidate

    def _require_aware_clock(self) -> None:
        """Reject a naive clock once, before any child can be dispatched."""

        moment = self._clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise BatchChildExecutorMisconfigured(
                BatchChildExecutionMessages.NAIVE_CLOCK
            )

    def _now(self) -> datetime:
        return self._clock()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)


__all__ = (
    "BatchChildDispatch",
    "BatchChildDispatchPort",
    "BatchChildDispatchStatus",
    "BatchChildExecutionBounds",
    "BatchChildExecutionError",
    "BatchChildExecutionMessages",
    "BatchChildExecutionReason",
    "BatchChildExecutorMisconfigured",
    "BatchChildWork",
    "BatchChildWorkPort",
    "GatewayBatchChildExecutor",
    "RunScopedBatchChildWork",
)
