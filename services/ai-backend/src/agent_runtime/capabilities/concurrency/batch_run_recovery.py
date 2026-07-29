"""The production route from a run's durable journal to its restart decision.

F6.6 built two halves and joined neither to a run.  The journal already records
what happened; :class:`~agent_runtime.capabilities.concurrency.batch_recovery.BatchRestartPlanner`
already decides, from those records alone, what a restarted run may do again.
This module is the seam between them, and it exists so the decision is made in
one place rather than re-derived by each handler that resumes a run.

It adds no judgment of its own.  Everything it returns came out of the planner,
and the planner is a pure function of the recovery view — so the same journal
produces the same plan whichever process reads it, on the desktop file store and
the in-memory adapter alike.

The one judgment this module *does* make is about its own failure, and it is
made in the honest direction rather than the convenient one.  A journal that
cannot be read yields :data:`None`, not an empty plan.  The difference matters:
an empty plan is a positive statement that nothing is withheld, and returning
one for a run whose evidence is unavailable would be exactly the "absence of
evidence read as proof" mistake that
:mod:`agent_runtime.capabilities.concurrency.batch_recovery` exists to refuse.
:data:`None` says only "no decision was reached", which is what actually
happened, and leaves the run on the pre-F6 path it would have taken had F6 never
been configured.

That does mean an unreadable journal resumes work a readable one would have
withheld.  It is the same exposure the deployment has with F6 switched off, and
it is bounded by the *second* defence rather than by this one: the planner's
rule is enforced again at dispatch by
:meth:`~agent_runtime.capabilities.concurrency.batch_coordinator.BatchExecutionCoordinator.withhold_on_restart`,
so a plan that was reached is binding on execution and not merely advisory to
it.  Neither defence is asked to cover for the other's absence.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runtime.capabilities.concurrency.batch_journal import BatchRecoveryView
from agent_runtime.capabilities.concurrency.batch_recovery import (
    BatchRestartPlanner,
    RunRestartPlan,
)


@runtime_checkable
class BatchRecoveryViewPort(Protocol):
    """The one journal capability recovery needs: read this run's durable facts.

    Deliberately narrower than
    :class:`~agent_runtime.capabilities.concurrency.batch_journal.BatchPlanStorePort`:
    recovery must be able to read a run's history and must *not* be able to
    append to it.  A component that decides what may be re-run is the last one
    that should be able to edit the evidence it is deciding from.
    """

    async def load_recovery_view(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> BatchRecoveryView: ...


class BatchRunRecovery:
    """Load one run's durable batch history and decide what it may repeat.

    Constructed per worker and called per run — once when a run is claimed and
    once when an approval resumes it, because those are the two moments a
    process starts executing a run it did not necessarily start.
    """

    __slots__ = ("_planner", "_store")

    def __init__(
        self,
        *,
        store: BatchRecoveryViewPort,
        planner: BatchRestartPlanner | None = None,
    ) -> None:
        self._store = store
        self._planner = planner if planner is not None else BatchRestartPlanner()

    async def aplan(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunRestartPlan | None:
        """Return this run's restart decision, or ``None`` if none was reached.

        Total by contract.  A run that has never been planned, a journal that
        cannot be replayed, and a store that is simply unavailable all answer
        ``None`` rather than raising, because none of them is a reason to fail a
        run that is otherwise healthy — and a claim that fails is a claim that
        gets retried, which is the one thing a lane about not repeating work
        should not cause.
        """

        try:
            view = await self._store.load_recovery_view(
                org_id=org_id,
                run_id=run_id,
                subject_fingerprint=subject_fingerprint,
            )
        except Exception:  # noqa: BLE001 - an unreadable journal decides nothing.
            return None
        if not view.plans:
            # A run with no durable batch plan has no F6 history to reason
            # about. This is the overwhelmingly common case — every first
            # attempt at every run — and it is distinct from a plan that
            # withholds nothing, which is a decision rather than its absence.
            return None
        try:
            return self._planner.plan(view)
        except Exception:  # noqa: BLE001 - an unplannable view decides nothing.
            return None


def withheld_operation_ids(plan: RunRestartPlan | None) -> frozenset[str]:
    """Return every operation a restart plan forbids re-running.

    Derived from :attr:`~agent_runtime.capabilities.concurrency.batch_recovery.ChildRestartDecision.resumable`
    by negation, which is what makes this safe to extend: the planner's one
    permitting disposition is the only thing that keeps an operation out of this
    set, so a disposition added later is withheld until somebody argues
    otherwise in the planner, where the argument belongs.
    """

    if plan is None:
        return frozenset()
    return frozenset(
        child.operation_id
        for batch in plan.batches
        for child in batch.children
        if not child.resumable
    )


__all__ = (
    "BatchRecoveryViewPort",
    "BatchRunRecovery",
    "withheld_operation_ids",
)
