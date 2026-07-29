"""Deciding, from durable facts only, what a restarted run may do again.

Every decision here is made from the journal and nothing else.  No in-memory
state survives the crash that makes recovery necessary, so anything this module
believed before the restart is exactly as unavailable as the answer it is trying
to reconstruct.

The rule is one-directional and it is the whole module:

    resume requires positive proof that the child never started.

Absence of evidence is never that proof — except in the one case where absence
*is* evidence, which has to be established rather than assumed.  The
:class:`BatchEvidence` distinction below is how it gets established:

- A batch with **at least one** durable child record proves the writer was
  journaling child transitions.  In that batch, a child with no dispatch intent
  provably never started, because the dispatching coroutine appends the intent
  and waits for it before awaiting the body.
- A batch with **no** durable child records proves nothing.  Either nothing
  started, or transitions were never being written at all.  Those are
  indistinguishable, so every child in such a batch is indeterminate.

That second rule is what makes the module safe when it is wired up wrong.  A
coordinator running without a journal produces batches with no child records,
and the honest consequence is that its work is never resumed — rather than the
catastrophic one, where every started write looks never-started and gets
replayed.  The failure mode of a misconfiguration is lost throughput, not
duplicated effects.

The second rule of the module is that a proof of "never started" still is not a
licence to re-run.  Only declared reads and effect-free operations are resumed;
a never-started *write* is withheld too.  Replaying it would very probably be
correct, and "very probably correct" is not the standard for repeating something
that changes the world.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.batch_journal import (
    BatchChildDisposition,
    BatchChildPhase,
    BatchRecoveryView,
    DurableBatchPlan,
    DurableChildTransition,
)
from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyPolicy,
    SideEffectKind,
)
from agent_runtime.execution.contracts import RuntimeContract


class BatchEvidence(StrEnum):
    """Whether a batch's journal can support an argument from silence."""

    CHILD_RECORDS_PRESENT = "child_records_present"
    NO_CHILD_RECORDS = "no_child_records"

    @property
    def supports_never_started(self) -> bool:
        """Return whether a missing child record proves the child never ran."""

        return self is BatchEvidence.CHILD_RECORDS_PRESENT


class ChildRestartEvidence(StrEnum):
    """Exactly what the journal says about one child, before any judgment.

    Kept separate from :class:`ChildRestartDisposition` so the record of *what
    was known* cannot be rewritten by a change to *what was decided*.  A reader
    auditing a bad resume can see which of the two went wrong.
    """

    NO_BATCH_EVIDENCE = "no_batch_evidence"
    NO_DISPATCH_INTENT = "no_dispatch_intent"
    DISPATCH_INTENT_ONLY = "dispatch_intent_only"
    SETTLED_SUCCEEDED = "settled_succeeded"
    SETTLED_FAILED = "settled_failed"
    SETTLED_INDETERMINATE = "settled_indeterminate"


class ChildRestartDisposition(StrEnum):
    """What a restarted run may do about one planned child.

    Exactly one member permits re-execution, and :attr:`resumable` is the only
    way to ask.  A caller cannot accidentally treat "we know it failed" or "we
    know it never started but it writes" as permission by reading a boolean that
    happened to be true for several members.
    """

    RESUME_SAFE_READ = "resume_safe_read"
    WITHHELD_UNSAFE_TO_REPLAY = "withheld_unsafe_to_replay"
    WITHHELD_ALREADY_SUCCEEDED = "withheld_already_succeeded"
    WITHHELD_ALREADY_FAILED = "withheld_already_failed"
    INDETERMINATE = "indeterminate"

    @property
    def resumable(self) -> bool:
        """Return whether this child's body may be run again."""

        return self is ChildRestartDisposition.RESUME_SAFE_READ


class ChildRestartDecision(RuntimeContract):
    """One child's durable evidence and the single decision it licenses.

    The invariant below is the lane's central claim expressed as a type
    constraint rather than a convention: a resumable decision cannot be
    constructed except from evidence that the child never started, and never for
    an operation whose declared effect class permits a change to the world.
    Breaking the rule in :class:`BatchRestartPlanner` therefore cannot produce a
    quietly wrong plan — it produces no plan at all.
    """

    batch_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    evidence: ChildRestartEvidence
    disposition: ChildRestartDisposition
    side_effect: SideEffectKind

    @property
    def resumable(self) -> bool:
        """Return whether this child may be run again."""

        return self.disposition.resumable

    @model_validator(mode="after")
    def _resume_requires_proof_and_safety(self) -> Self:
        if not self.disposition.resumable:
            return self
        if self.evidence is not ChildRestartEvidence.NO_DISPATCH_INTENT:
            raise ValueError(
                "a child may only resume on durable proof that it never started"
            )
        if not _is_safe_read(self.side_effect):
            raise ValueError("a restarted run may only resume declared safe reads")
        return self


class BatchRestartPlan(RuntimeContract):
    """Every planned child of one durably ordered batch, classified."""

    batch_id: str = Field(min_length=1, max_length=255)
    evidence: BatchEvidence
    children: tuple[ChildRestartDecision, ...] = Field(min_length=1, max_length=100)

    @property
    def resumable(self) -> tuple[ChildRestartDecision, ...]:
        """Return only the children a restart may run again."""

        return tuple(child for child in self.children if child.resumable)

    @property
    def indeterminate(self) -> tuple[ChildRestartDecision, ...]:
        """Return every child whose outcome the journal cannot establish."""

        return tuple(
            child
            for child in self.children
            if child.disposition is ChildRestartDisposition.INDETERMINATE
        )


class RunRestartPlan(RuntimeContract):
    """The complete, durable-facts-only recovery decision for one run."""

    run_id: str = Field(min_length=1, max_length=255)
    snapshot_id: str = Field(min_length=1, max_length=255)
    batches: tuple[BatchRestartPlan, ...] = ()

    @property
    def resumable(self) -> tuple[ChildRestartDecision, ...]:
        """Return every child in the run a restart may run again."""

        return tuple(child for batch in self.batches for child in batch.resumable)

    @property
    def indeterminate(self) -> tuple[ChildRestartDecision, ...]:
        """Return every child in the run whose outcome is undetermined."""

        return tuple(child for batch in self.batches for child in batch.indeterminate)

    def batch_for(self, batch_id: str) -> BatchRestartPlan | None:
        """Return one batch's recovery decision, if the run ordered it."""

        return next(
            (batch for batch in self.batches if batch.batch_id == batch_id),
            None,
        )

    def decision_for(self, operation_id: str) -> ChildRestartDecision | None:
        """Return one child's recovery decision, wherever it was planned."""

        return next(
            (
                child
                for batch in self.batches
                for child in batch.children
                if child.operation_id == operation_id
            ),
            None,
        )


def _is_safe_read(side_effect: SideEffectKind) -> bool:
    """Return whether re-running an operation of this class cannot change state.

    ``UNKNOWN`` is not safe.  It is the conservative floor F6.1 assigns to every
    capability that declared nothing, and an operation nobody classified is
    precisely the one whose replay nobody can vouch for.
    """

    return side_effect in (SideEffectKind.READ, SideEffectKind.NONE)


class BatchRestartPlanner:
    """Classify a crashed run's children from its journal, inventing nothing.

    A pure function of the recovery view, with no clock, no store, and no
    fallback path — which is what makes the same journal always produce the same
    plan, on the desktop file store and the in-memory adapter alike.
    """

    def plan(self, view: BatchRecoveryView) -> RunRestartPlan:
        """Return the recovery decision for every durably ordered batch."""

        return RunRestartPlan(
            run_id=view.run_id,
            snapshot_id=view.snapshot_id,
            batches=tuple(
                self.plan_batch(plan, view.transitions_for(plan.batch_id))
                for plan in view.plans
            ),
        )

    def plan_batch(
        self,
        plan: DurableBatchPlan,
        transitions: tuple[DurableChildTransition, ...],
    ) -> BatchRestartPlan:
        """Classify one batch's children against its durable child records."""

        evidence = (
            BatchEvidence.CHILD_RECORDS_PRESENT
            if transitions
            else BatchEvidence.NO_CHILD_RECORDS
        )
        intents = {
            transition.operation_id
            for transition in transitions
            if transition.phase is BatchChildPhase.DISPATCH_INTENT
        }
        settled = {
            transition.operation_id: transition.record.disposition
            for transition in transitions
            if transition.phase is BatchChildPhase.SETTLED
        }
        return BatchRestartPlan(
            batch_id=plan.batch_id,
            evidence=evidence,
            children=tuple(
                self._decide(
                    plan=plan,
                    operation_id=operation_id,
                    evidence=evidence,
                    started=operation_id in intents,
                    disposition=settled.get(operation_id),
                )
                for operation_id in plan.plan.operation_ids
            ),
        )

    def _decide(
        self,
        *,
        plan: DurableBatchPlan,
        operation_id: str,
        evidence: BatchEvidence,
        started: bool,
        disposition: BatchChildDisposition | None,
    ) -> ChildRestartDecision:
        """Classify one child from durable facts and its resolved policy."""

        policy = plan.policy_for(operation_id) or ConcurrencyPolicy()
        child_evidence = self._evidence_for(
            evidence=evidence,
            started=started,
            disposition=disposition,
        )
        return ChildRestartDecision(
            batch_id=plan.batch_id,
            operation_id=operation_id,
            evidence=child_evidence,
            disposition=self._disposition_for(child_evidence, policy.side_effect),
            side_effect=policy.side_effect,
        )

    @staticmethod
    def _evidence_for(
        *,
        evidence: BatchEvidence,
        started: bool,
        disposition: BatchChildDisposition | None,
    ) -> ChildRestartEvidence:
        """Return what the journal states about one child, without judgment."""

        if not evidence.supports_never_started:
            return ChildRestartEvidence.NO_BATCH_EVIDENCE
        if not started:
            return ChildRestartEvidence.NO_DISPATCH_INTENT
        if disposition is BatchChildDisposition.SUCCEEDED:
            return ChildRestartEvidence.SETTLED_SUCCEEDED
        if disposition is BatchChildDisposition.FAILED:
            return ChildRestartEvidence.SETTLED_FAILED
        if disposition is BatchChildDisposition.INDETERMINATE:
            return ChildRestartEvidence.SETTLED_INDETERMINATE
        return ChildRestartEvidence.DISPATCH_INTENT_ONLY

    @staticmethod
    def _disposition_for(
        evidence: ChildRestartEvidence,
        side_effect: SideEffectKind,
    ) -> ChildRestartDisposition:
        """Return the one decision a piece of evidence licenses.

        A settled child is never resumed whatever its outcome, and a started
        child with no outcome is indeterminate whatever it was going to do.  The
        only branch that even consults the effect class is the one that already
        holds proof the child never ran.
        """

        if evidence is ChildRestartEvidence.SETTLED_SUCCEEDED:
            return ChildRestartDisposition.WITHHELD_ALREADY_SUCCEEDED
        if evidence is ChildRestartEvidence.SETTLED_FAILED:
            return ChildRestartDisposition.WITHHELD_ALREADY_FAILED
        if evidence is ChildRestartEvidence.NO_DISPATCH_INTENT:
            return (
                ChildRestartDisposition.RESUME_SAFE_READ
                if _is_safe_read(side_effect)
                else ChildRestartDisposition.WITHHELD_UNSAFE_TO_REPLAY
            )
        return ChildRestartDisposition.INDETERMINATE


__all__ = (
    "BatchEvidence",
    "BatchRestartPlan",
    "BatchRestartPlanner",
    "ChildRestartDecision",
    "ChildRestartDisposition",
    "ChildRestartEvidence",
    "RunRestartPlan",
)
