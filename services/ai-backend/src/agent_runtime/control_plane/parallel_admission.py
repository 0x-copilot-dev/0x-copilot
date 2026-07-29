"""The one seam by which F6 may widen the run's serial tool admission.

Step 2 made every graph-visible tool call take one run-scoped serial permit
(:class:`~agent_runtime.control_plane.context.RunSerialAdmission`). That permit
is a safety boundary, not a performance knob: it is what makes the §8 middleware
ordering enforceable across framework-injected and Deep Agents-delegated tools
alike, because *every* such call crosses the same gate.

Its cost is that the effective width of a run is ``min(plan, permits, Step-2)``,
which is ``1`` whatever an F6 batch plan says. This module is how that ``1``
becomes conditional without the gate becoming optional. F6 hands the admission a
**grant**: a positive, call-specific authority to overlap, already narrowed by
everything F6 knows. Absent a grant the admission is exactly as serial as it was.

Three properties are structural rather than conventional:

- **The direction of the dependency.** ``capabilities.concurrency`` already
  imports ``control_plane`` (``batch_journal``, ``contracts``,
  ``batch_journal_store``), so the reverse edge would be a genuine import cycle.
  This module therefore imports nothing from ``agent_runtime`` at all: F6 (or
  root's wiring) implements :class:`ParallelAdmissionPort` on the *capabilities*
  side, which is the same direction ``runtime_tool_control`` already imports in.
  It is the pattern F6.3's ``BatchAllowanceSupplier`` and F6.7's
  ``ConcurrencyKillSwitchSourcePort`` already use.

- **A grant cannot widen anything.** :class:`ParallelAdmissionGrant` refuses to
  construct with a width below one, an unbounded width, or a blank identifier,
  so a widening or anonymous grant is not a value that exists. This mirrors
  ``ConcurrencyKillSwitchDecision``, which makes a widening decision
  unconstructible rather than merely unwritten.

- **Unknown means serial.** :class:`ParallelAdmissionResolver` is the single
  place that turns a port's answer into a decision, and every path that is not
  *positively* a grant naming *this exact call* returns ``None``. No port, a
  raising port, a port returning some other type, a grant for a different call,
  and a grant whose width is one all land on the same serial answer.

The port is deliberately synchronous. It is consulted on the hot path before any
lock is taken, and a synchronous reader cannot await — so it cannot suspend
inside, or deadlock against, the admission it is being consulted for. F6.7's
kill-switch source port is synchronous for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable


class ParallelAdmissionBounds:
    """Hard, content-free bounds on anything a grant may claim.

    ``MAX_PARALLELISM`` matches the largest batch F6 can journal
    (``BatchExecutionReport.outcomes`` is capped at 100), so a grant can never
    authorize more overlap than a durable plan could ever have named.

    ``MAX_TRACKED_COHORTS`` bounds the admission's own cohort table, and it is
    also this gate's run-wide ceiling: concurrent cohorts each carry their own
    width, so the most the gate can ever admit at once is one member per live
    cohort. It matches ``BatchCoordinatorBounds.MAX_TRACKED_BATCHES``, the count
    of batches F6 will track for one run. Both numbers are duplicated rather
    than imported: importing them would be the ``control_plane`` →
    ``capabilities.concurrency`` edge this module exists to avoid.
    """

    MAX_IDENTIFIER_LENGTH: Final[int] = 255
    MAX_PARALLELISM: Final[int] = 100
    MAX_TRACKED_COHORTS: Final[int] = 32


class ParallelAdmissionMessages:
    """Public, content-free text for every grant construction fault."""

    BLANK_TOOL_CALL_ID = "A parallel admission grant must name one tool call."
    BLANK_COHORT_ID = "A parallel admission grant must name its cohort."
    OVERSIZED_IDENTIFIER = "A parallel admission grant identifier is too long."
    WIDTH_BELOW_ONE = "A parallel admission grant cannot narrow below one."
    WIDTH_ABOVE_BOUND = "A parallel admission grant exceeds the hard width bound."


@dataclass(frozen=True, slots=True)
class ToolAdmissionRequest:
    """Everything the graph tool seam knows about one call, before it runs.

    Built from the framework's own tool call without validation on purpose: a
    malformed or unidentifiable call must reach the gate and be admitted
    *serially*, not be refused before it. An empty ``tool_call_id`` is therefore
    a legal request, and it is one no grant can ever authorize.
    """

    tool_call_id: str
    tool_name: str
    execution_scope: str


@dataclass(frozen=True, slots=True)
class ParallelAdmissionGrant:
    """A positive, call-specific authority to overlap, bounded by F6's width.

    ``cohort_id`` names the set of calls this width applies *across* — for F6
    that is one segment of one durable batch plan, the scope whose members the
    coordinator already decided may run together. ``max_parallelism`` is the
    effective allowance F6 computed, which is itself already the minimum of the
    segment allowance, the plan allowance, the live kill-switch allowance, and
    the run permit table.

    The admission never recomputes that width and never widens it. It only
    enforces it across the cohort, and only for calls a grant positively names.
    """

    tool_call_id: str
    cohort_id: str
    max_parallelism: int

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError(ParallelAdmissionMessages.BLANK_TOOL_CALL_ID)
        if not self.cohort_id.strip():
            raise ValueError(ParallelAdmissionMessages.BLANK_COHORT_ID)
        if (
            len(self.tool_call_id) > ParallelAdmissionBounds.MAX_IDENTIFIER_LENGTH
            or len(self.cohort_id) > ParallelAdmissionBounds.MAX_IDENTIFIER_LENGTH
        ):
            raise ValueError(ParallelAdmissionMessages.OVERSIZED_IDENTIFIER)
        if self.max_parallelism < 1:
            raise ValueError(ParallelAdmissionMessages.WIDTH_BELOW_ONE)
        if self.max_parallelism > ParallelAdmissionBounds.MAX_PARALLELISM:
            raise ValueError(ParallelAdmissionMessages.WIDTH_ABOVE_BOUND)

    @property
    def overlaps(self) -> bool:
        """Return whether this grant authorizes any overlap at all.

        A width of one is a grant that says "serial", and it is answered by the
        same exclusive lock an ungranted call takes rather than by a semaphore
        of one. The two are equivalent in width and not equivalent in coverage:
        the exclusive lock is also what a *serial* sibling waits behind.
        """

        return self.max_parallelism > 1

    def authorizes(self, request: ToolAdmissionRequest) -> bool:
        """Return whether this grant was issued for exactly ``request``.

        This is the load-bearing half of "unknown means serial". A grant that
        does not name this precise tool call authorizes nothing, so a stale,
        replayed, or mis-keyed grant can never leak overlap onto a call F6 never
        admitted.
        """

        return bool(self.tool_call_id) and self.tool_call_id == request.tool_call_id


@runtime_checkable
class ParallelAdmissionPort(Protocol):
    """Resolve whether F6 admitted one graph-visible call to run in parallel."""

    def grant_for(
        self,
        request: ToolAdmissionRequest,
    ) -> ParallelAdmissionGrant | None:
        """Return this call's grant, or ``None`` when it has none."""


class ParallelAdmissionResolver:
    """The single fail-closed reading of a port's answer.

    Every rejection lands here rather than being spread across the admission, so
    "anything not positively identified as F6-admitted is serial" is one
    reviewable function instead of a rule each call site is trusted to remember.
    """

    @staticmethod
    def resolve(
        port: ParallelAdmissionPort | None,
        request: ToolAdmissionRequest | None,
    ) -> ParallelAdmissionGrant | None:
        """Return the grant that may widen this call, or ``None`` for serial."""

        if port is None or request is None:
            return None
        try:
            grant = port.grant_for(request)
        except Exception:  # noqa: BLE001 - a failing source is a serial source.
            return None
        if not isinstance(grant, ParallelAdmissionGrant):
            return None
        if not grant.authorizes(request):
            return None
        if not grant.overlaps:
            return None
        return grant


__all__ = (
    "ParallelAdmissionBounds",
    "ParallelAdmissionGrant",
    "ParallelAdmissionMessages",
    "ParallelAdmissionPort",
    "ParallelAdmissionResolver",
    "ToolAdmissionRequest",
)
