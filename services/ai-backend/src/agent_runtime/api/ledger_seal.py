"""Single authority for a run ledger's causal-prefix seal.

A run's terminal event seals the *causal prefix* ``[1..N]``: every event the run
caused lies inside it, so a consumer that replayed through the terminal event
has provably seen the whole run. The ledger itself stays append-only forever.
Facts that can only be known afterwards — a reconciliation settled long after
the run ended, or a causal event whose inline emission was lost to a crash —
append at ``N+k`` as **amendments**: typed, attributed, and carrying an explicit
pointer to what they amend. An amendment never claims to have happened before
the seal.

This mirrors bitemporal practice (transaction time — here ``sequence_no`` — only
increases, while an amendment's *subject* may lie in the sealed past) and the
accounting rule it descends from: you post an adjusting entry in the current
period referencing the closed one; you never reopen the closed period.

Why this module exists
----------------------
The rule predates it. It was simply never given a home, so it was restated as
prose at four call sites and omitted at a fifth:

* ``runtime_worker/handlers/receipt_hook.py`` — "MUST be appended before the
  terminal lifecycle event"
* ``runtime_worker/handlers/run.py::_emit_receipt_then_terminate`` — the same
  sentence again
* ``runtime_api/sse/adapter.py`` — an unexplained ``return`` on terminal status
* ``chat-surface``'s canvas fold — ``terminal and no subject`` ⇒ "chat only"
* the artifact outbox — **no copy at all**

Copies drift, and the missing copy is the bug: ``publish_artifact`` committed its
outbox rows mid-run, but the rows only became ledger events when a worker next
called ``claim_next``. With an in-process worker (the desktop topology) that is
necessarily *after* the run it was executing had finished, so
``artifact.created`` landed after the terminal event, the SSE stream had already
closed, and no live client ever saw the artifact. The canvas correctly reported
"no artifact was created" because it was told the truth about a false ledger.

Enforcement lives at :class:`~agent_runtime.api.events.RuntimeEventProducer`,
the one funnel every producer already passes through — the same place, and for
the same reason, that ``LifecycleLedger`` is centralised: "individual emission
sites can never forget to keep the ledger consistent".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from runtime_api.schemas.common import RuntimeApiEventType


class LedgerAmendmentReason(StrEnum):
    """Why a fact is landing after the seal instead of inside the prefix.

    Deliberately closed and small. Every member is a case where the fact was
    *unknowable* before the seal — that is the whole test for membership. A
    producer that is merely late is not amending; it is violating, and it
    should fail loudly rather than quietly widen this enum.
    """

    #: An A5 effect claim settled after its run ended. The only member that is
    #: intrinsically post-hoc: an indeterminate write's true disposition cannot
    #: be known while the run is still open.
    RECONCILIATION = "reconciliation"
    #: A causal event whose in-run emission was lost (crash between the durable
    #: commit and the ledger append), recovered afterwards by the outbox bridge.
    #: Recording it as an amendment keeps the prefix's completeness claim honest
    #: — the event is *not* pretending it arrived in time — while still making
    #: the fact visible instead of dropping it.
    LATE_CAUSAL_RECOVERY = "late_causal_recovery"


@dataclass(frozen=True, slots=True)
class LedgerAmendment:
    """A declared intent to append after the seal.

    Callers must construct this explicitly. The producer never infers an
    amendment from timing, event type, or run status: inference is what let the
    artifact events masquerade as causal in the first place. Declaring intent
    puts the decision at the call site, where the author knows whether the fact
    was knowable in time.
    """

    class Keys:
        """Metadata keys an amendment stamps onto the event it annotates."""

        REASON = "ledger_amendment_reason"
        AMENDS = "ledger_amends"

    #: Every key :meth:`as_metadata` can add, named so the event store's
    #: stable-id idempotency check can exclude them. Both describe the *append
    #: attempt*, not the event: ``REASON`` differs between the lane that
    #: publishes a fact causally and the recovery lane that republishes the same
    #: content-addressed event after the seal, and ``AMENDS`` moves with the
    #: run's sequence cursor between an attempt and its own retry. Neither can
    #: be stable by construction, so an equality check that included them would
    #: answer a redelivery of an already-durable event with a conflict.
    METADATA_KEYS: ClassVar[frozenset[str]] = frozenset({Keys.REASON, Keys.AMENDS})

    reason: LedgerAmendmentReason
    #: Ledger id (``<run>·<seq>``) of the sealed fact this amends, when the
    #: amendment refers to a specific event. ``None`` when it amends the run's
    #: outcome as a whole.
    amends: str | None = None

    def as_metadata(self) -> dict[str, str]:
        """Render the amendment as event metadata.

        Stamped onto the envelope so the exception is legible in replay and in
        audit export — a reader can tell a post-seal fact from a causal one
        without reconstructing append timing.
        """

        metadata = {self.Keys.REASON: self.reason.value}
        if self.amends is not None:
            metadata[self.Keys.AMENDS] = self.amends
        return metadata


class LedgerSealViolation(AgentRuntimeError):
    """A causal event was appended to a run whose prefix is already sealed.

    Always a producer bug: either the event should have been emitted before
    termination, or its producer should have declared a
    :class:`LedgerAmendment`. Raised rather than logged so the failure surfaces
    at the producer instead of becoming an event no live client can ever see.
    """

    def __init__(self, *, run_id: str, event_type: str, sealed_by: str) -> None:
        super().__init__(
            RuntimeErrorCode.VALIDATION_ERROR,
            "This run's ledger is sealed and cannot accept further causal events.",
            retryable=False,
        )
        self.run_id = run_id
        self.event_type = event_type
        self.sealed_by = sealed_by


class LedgerSeal:
    """Tracks which runs have had their causal prefix sealed.

    Monotonic: a run seals exactly once and never reopens, so the in-memory
    state cannot go stale in the unsafe direction. Scoped to one
    ``RuntimeEventProducer``, which is the instance that executes a run and
    therefore observes its own terminal append — the hot path (model deltas,
    tool deltas) resolves against a dict lookup and never touches storage.

    Producers that did not execute the run (a worker claiming an outbox command
    for a run some other process ran) will not find it here. They are exactly
    the callers that must declare a :class:`LedgerAmendment`, so the gap is
    closed by the declaration requirement rather than by a durable lookup on
    every append.
    """

    #: Appending one of these seals the prefix. ``RUN_REJECTED`` is included:
    #: a budget-denied run never produces further causal events either.
    SEALING_EVENT_TYPES = frozenset(
        {
            RuntimeApiEventType.RUN_COMPLETED,
            RuntimeApiEventType.RUN_FAILED,
            RuntimeApiEventType.RUN_CANCELLED,
            RuntimeApiEventType.RUN_REJECTED,
        }
    )

    def __init__(self) -> None:
        self._sealed_by: dict[str, str] = {}

    def is_sealed(self, run_id: str) -> bool:
        """Return whether this run's causal prefix is closed."""

        return run_id in self._sealed_by

    def sealed_by(self, run_id: str) -> str | None:
        """Return the event type that sealed the run, if it is sealed."""

        return self._sealed_by.get(run_id)

    def guard(
        self,
        *,
        run_id: str,
        event_type: RuntimeApiEventType,
        amendment: LedgerAmendment | None,
    ) -> None:
        """Admit one append, sealing the run when the event is terminal.

        Order matters: the sealing event is admitted *before* the run is marked,
        so a terminal event is never rejected by the seal it establishes.
        """

        if event_type in self.SEALING_EVENT_TYPES:
            # Re-terminating is idempotent by design: RunTerminationCoordinator
            # documents ``terminate`` as safe to call twice, and cancel/timeout
            # races legitimately reach it from two paths.
            self._sealed_by.setdefault(run_id, event_type.value)
            return
        if amendment is not None:
            return
        sealed_by = self._sealed_by.get(run_id)
        if sealed_by is not None:
            raise LedgerSealViolation(
                run_id=run_id,
                event_type=event_type.value,
                sealed_by=sealed_by,
            )


__all__ = (
    "LedgerAmendment",
    "LedgerAmendmentReason",
    "LedgerSeal",
    "LedgerSealViolation",
)
