"""What cancelling a run may honestly claim about work already in flight.

Cancellation is where a system is most tempted to lie.  The two comfortable
answers — "we stopped it" and "we rolled it back" — are usually both false, and
the true answer is often that a request left the process and nobody knows what
the other side did with it.  This module gives that answer a name, a closed
vocabulary, and a place in a returned value, so a caller has to *choose* to
misreport it rather than fall into misreporting it because the type system only
offered success and failure.

Three distinctions carry all the weight:

- **Started or not.**  A child refused before its body was awaited had no
  effect, full stop.  That is not an optimistic reading, it is a structural one:
  the coordinator never awaited the runner.
- **Effect certain or not.**  A cancelled *read* changed nothing — its result is
  missing, but the world is untouched.  A cancelled write may have completed
  remotely.  Collapsing these into one "cancelled" would either slander every
  read or excuse every write.
- **Drained or bounded out.**  A drain that finished observed every child
  settle.  A drain that hit its bound stopped *watching*; it did not stop the
  work.  :attr:`BatchCancellationReport.drained` is that difference, and it is
  never inferred from the absence of running children.

Nothing here cancels, rolls back, compensates, or retries.  It only describes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agent_runtime.capabilities.concurrency.contracts import SideEffectKind
from agent_runtime.execution.contracts import RuntimeContract


class BatchCancellationReason(StrEnum):
    """Why admission stopped.

    Both members stop new admission identically.  They are distinguished
    because "the user cancelled" and "the process is going away" produce the
    same in-flight ambiguity but call for different messages, and deriving one
    from the other after the fact is guesswork.
    """

    RUN_CANCELLED = "run_cancelled"
    RUNTIME_SHUTDOWN = "runtime_shutdown"


class ChildEffectCertainty(StrEnum):
    """What is known about a child's effect on the world outside this process.

    ``NO_EXTERNAL_EFFECT`` is a claim about the *capability*, not about timing:
    it is asserted only for a resolved policy that declares the operation a read
    or effect-free, and those declarations are already narrowed to the
    conservative floor by F6.1.  Every other case — including an undeclared
    effect class — is ``UNKNOWN``, because an operation nobody classified is
    exactly the operation nobody can vouch for.
    """

    NO_EXTERNAL_EFFECT = "no_external_effect"
    UNKNOWN = "unknown"

    @classmethod
    def of(cls, side_effect: SideEffectKind) -> Self:
        """Return what a resolved policy's effect class permits us to claim."""

        return (
            cls.NO_EXTERNAL_EFFECT
            if side_effect in (SideEffectKind.READ, SideEffectKind.NONE)
            else cls.UNKNOWN
        )


class ChildCancellationState(StrEnum):
    """Closed description of one child at the moment cancellation finished.

    ``IN_FLIGHT_INDETERMINATE`` is the PRD's ``in_flight``/``indeterminate``
    pair rendered as one member on purpose: after a bounded drain those are the
    same child seen from two angles — still running as far as we know, and
    therefore of unknown outcome — and offering both would invite a caller to
    treat one of them as milder than it is.

    Three of the five members mean "no result", and they are kept apart because
    the *reason* differs and callers act on it differently: one was interrupted
    on purpose, one outlived the drain, and one was already unknowable before
    cancellation was ever requested. Only the middle one says anything about
    whether the drain did its job.
    """

    NOT_STARTED = "not_started"
    SETTLED_BEFORE_CANCEL = "settled_before_cancel"
    SETTLED_DURING_DRAIN = "settled_during_drain"
    CANCELLED_IN_PLACE = "cancelled_in_place"
    IN_FLIGHT_INDETERMINATE = "in_flight_indeterminate"
    INDETERMINATE_BEFORE_CANCEL = "indeterminate_before_cancel"

    @property
    def outcome_known(self) -> bool:
        """Return whether this state pins the child's outcome to a fact."""

        return self in (
            ChildCancellationState.NOT_STARTED,
            ChildCancellationState.SETTLED_BEFORE_CANCEL,
            ChildCancellationState.SETTLED_DURING_DRAIN,
        )


class CancelledChild(RuntimeContract):
    """Body-free cancellation record for one planned child.

    ``effect_certainty`` is carried even for children that never started,
    because a caller filtering for "what might have changed" should be able to
    ask one question of every row rather than remembering which states make the
    field meaningful.
    """

    batch_id: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(min_length=1, max_length=255)
    state: ChildCancellationState
    effect_certainty: ChildEffectCertainty
    cancel_requested: bool = False

    @property
    def indeterminate(self) -> bool:
        """Return whether this child's outcome is genuinely unknown.

        Derived from :attr:`ChildCancellationState.outcome_known` rather than
        listed, so a state added later is unknown-by-default. A member that
        should count as a known outcome has to say so explicitly, which is the
        direction that fails safe.
        """

        return not self.state.outcome_known

    @model_validator(mode="after")
    def _only_started_work_can_be_cancelled(self) -> Self:
        if self.cancel_requested and self.state is ChildCancellationState.NOT_STARTED:
            raise ValueError("a child that never started cannot have been cancelled")
        return self


class BatchCancellationReport(RuntimeContract):
    """Everything one cancellation may honestly claim, per child and in total.

    There is no ``rolled_back`` count and no ``succeeded`` count, and that is
    the point of the type: a caller cannot report an outcome this lane never
    established, because there is no field to read it from.
    """

    reason: BatchCancellationReason
    requested_at: datetime
    completed_at: datetime
    drained: bool
    children: tuple[CancelledChild, ...] = ()

    @property
    def indeterminate(self) -> tuple[CancelledChild, ...]:
        """Return every child whose outcome nobody can determine."""

        return tuple(child for child in self.children if child.indeterminate)

    @property
    def unknown_effects(self) -> tuple[CancelledChild, ...]:
        """Return every indeterminate child that may have changed something."""

        return tuple(
            child
            for child in self.indeterminate
            if child.effect_certainty is ChildEffectCertainty.UNKNOWN
        )

    @property
    def cancelled_in_place(self) -> tuple[CancelledChild, ...]:
        """Return every child this cancellation actively interrupted."""

        return tuple(
            child
            for child in self.children
            if child.state is ChildCancellationState.CANCELLED_IN_PLACE
        )

    @property
    def in_flight(self) -> tuple[CancelledChild, ...]:
        """Return every child that outlived the drain and may still be running."""

        return tuple(
            child
            for child in self.children
            if child.state is ChildCancellationState.IN_FLIGHT_INDETERMINATE
        )

    @model_validator(mode="after")
    def _report_is_self_consistent(self) -> Self:
        """Hold the one invariant a completed drain actually establishes.

        It is deliberately narrower than "nothing is indeterminate". A drain
        that observed every child settle proves nothing was left *running* — it
        does not, and cannot, turn a cancelled read or a child that was already
        unknowable into a known outcome. Stating the broader rule would make
        this contract refuse to describe a run it describes perfectly well.
        """

        for moment in (self.requested_at, self.completed_at):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("cancellation timestamps must be timezone-aware")
        if self.drained and self.in_flight:
            raise ValueError("a completed drain cannot leave a child still running")
        return self


__all__ = (
    "BatchCancellationReason",
    "BatchCancellationReport",
    "CancelledChild",
    "ChildCancellationState",
    "ChildEffectCertainty",
)
