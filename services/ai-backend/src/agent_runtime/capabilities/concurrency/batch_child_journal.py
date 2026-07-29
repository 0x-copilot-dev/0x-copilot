"""Durable child lifecycle for one run — the input restart reasons from.

F6.2 made the *ordering* durable before any child could start.  This module
makes the *dispatch* durable before any child body is awaited, which is the only
way a later process can tell two states apart that look identical from the
outside:

- a child that never started, whose work is still safe to do; and
- a child that started and whose answer was lost, whose work may already have
  changed something in the world.

Conflating those is the failure this whole lane exists to prevent, and no amount
of in-memory bookkeeping survives the crash that creates the ambiguity.

The discipline is one rule with one direction:

    append the intent, wait for it to be durable, *then* await the child body.

Everything else follows from it.  If the process dies before the append
completes, the dispatching coroutine was still suspended on that append, so the
body never ran and the missing record is a proof.  If it dies after, the record
is there and the honest answer is "may have started".  The asymmetry is
deliberate: the mode that costs us a redone read is reachable, and the mode that
costs a user a duplicated write is not.

Losing the *settled* append is safe in the same direction.  A child with an
intent and no settled record reads as indeterminate, which is exactly what a
child whose completion record was lost actually is — so the second append is
observability, not correctness, and it never needs heroics to survive
cancellation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.batch_journal import (
    BatchChildDisposition,
    BatchChildPhase,
    BatchChildTransitionRecord,
    BatchChildTransitionWrite,
    BatchJournalLimits,
    BatchJournalPatterns,
    DurableChildTransition,
)
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.execution.contracts import RuntimeContract


@runtime_checkable
class ChildTransitionJournalPort(Protocol):
    """The one journal capability the coordinator needs to dispatch safely.

    Deliberately narrower than
    :class:`~agent_runtime.capabilities.concurrency.batch_journal.BatchPlanStorePort`:
    the execution coordinator must be able to record that a child is starting,
    and must *not* be able to bind a plan or read another run's recovery view.
    """

    async def append_child_transition(
        self,
        write: BatchChildTransitionWrite,
    ) -> DurableChildTransition: ...


class BatchRunBinding(RuntimeContract):
    """The verified per-run identity every F6 child fact is written under.

    Carrying this as one immutable value rather than five loose arguments is
    what stops a child transition from ever being written under a mismatched
    org, subject, or snapshot: the coordinator is handed a binding it cannot
    edit, and the store re-checks the snapshot half against durable state.
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
    snapshot_id: str = Field(pattern=BatchJournalPatterns.IDENTIFIER)

    @classmethod
    def of(
        cls,
        *,
        org_id: str,
        trace_id: str,
        snapshot: RunControlSnapshot,
    ) -> Self:
        """Bind to a run by reading identity off its frozen control snapshot."""

        return cls(
            org_id=org_id,
            trace_id=trace_id,
            subject_fingerprint=snapshot.subject_fingerprint,
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
        )

    @model_validator(mode="after")
    def _binding_is_complete(self) -> Self:
        if not self.run_id or not self.snapshot_id:
            raise ValueError("a run binding requires both run and snapshot identity")
        return self


class BatchChildTransitionRecorder:
    """Record one run's child lifecycle on the canonical journal.

    The two methods are asymmetric on purpose, and the asymmetry is the safety
    property rather than an implementation detail:

    - :meth:`record_dispatch_intent` is awaited *before* the child body and its
      failure must stop the dispatch, because a child that runs without a
      durable intent is a child a later restart will wrongly believe never ran.
    - :meth:`record_settled` is awaited after, and its failure changes nothing
      that matters: the child degrades to indeterminate, which is what an
      unrecorded outcome is.

    Neither method invents a timestamp policy.  ``created_at`` comes from the
    record factory, which is the same clock discipline F6.2's plan record uses.
    """

    def __init__(
        self,
        *,
        journal: ChildTransitionJournalPort,
        binding: BatchRunBinding,
    ) -> None:
        self._journal = journal
        self._binding = binding

    @property
    def binding(self) -> BatchRunBinding:
        """Return the immutable run identity every append is written under."""

        return self._binding

    async def record_dispatch_intent(
        self,
        *,
        batch_id: str,
        operation_id: str,
        created_at: datetime | None = None,
    ) -> DurableChildTransition:
        """Durably declare that one child's body is about to be awaited."""

        return await self._append(
            batch_id=batch_id,
            operation_id=operation_id,
            phase=BatchChildPhase.DISPATCH_INTENT,
            disposition=None,
            created_at=created_at,
        )

    async def record_settled(
        self,
        *,
        batch_id: str,
        operation_id: str,
        disposition: BatchChildDisposition,
        created_at: datetime | None = None,
    ) -> DurableChildTransition:
        """Durably record what one already-dispatched child turned out to be."""

        return await self._append(
            batch_id=batch_id,
            operation_id=operation_id,
            phase=BatchChildPhase.SETTLED,
            disposition=disposition,
            created_at=created_at,
        )

    async def _append(
        self,
        *,
        batch_id: str,
        operation_id: str,
        phase: BatchChildPhase,
        disposition: BatchChildDisposition | None,
        created_at: datetime | None,
    ) -> DurableChildTransition:
        record = BatchChildTransitionRecord.create(
            record_id=BatchChildTransitionRecord.stable_record_id(
                batch_id=batch_id,
                operation_id=operation_id,
                phase=phase,
            ),
            run_id=self._binding.run_id,
            snapshot_id=self._binding.snapshot_id,
            batch_id=batch_id,
            operation_id=operation_id,
            phase=phase,
            disposition=disposition,
            **({} if created_at is None else {"created_at": created_at}),
        )
        return await self._journal.append_child_transition(
            BatchChildTransitionWrite(
                org_id=self._binding.org_id,
                trace_id=self._binding.trace_id,
                subject_fingerprint=self._binding.subject_fingerprint,
                record=record,
            )
        )


__all__ = (
    "BatchChildTransitionRecorder",
    "BatchRunBinding",
    "ChildTransitionJournalPort",
)
