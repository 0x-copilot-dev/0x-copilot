"""The in-process join between a run's executing task and commands aimed at it.

Control here is out of band: the API enqueues a command and a worker claims
it as a claim of its own, so the coroutine that *learns* a run was cancelled is
never the coroutine *executing* it.  Something has to join those two facts.
:class:`LiveBatchAdmissionRegistry` already does it for F6 batch work; this does
it for the run itself, and it is the only thing that can stop a subagent.

Cancel was the first such command and named the module; **steering is the
second**, and it reuses this registry rather than standing up a sibling.  The
two need the *same* lifetime — published for exactly as long as this process is
inside the run's claim — and two registries that must agree on a lifetime is a
correctness hazard, not merely duplication.  So a registration carries both the
task a cancel stops and the mailbox a steer waits in
(:class:`~agent_runtime.execution.run_steering.RunSteeringInbox`); the file name
is now narrower than its contents, which is the smaller of the two costs.

Subagents are in-process.  The ``task`` tool awaits the child graph inside the
supervisor's own tool call (``delegation/subagents/atlas_task_tool.py``), so
there is no child process to signal and no child run to cancel through the API.
The child stops when — and only when — the task executing the parent stops.
That makes task cancellation not one option among several but the mechanism.

Three properties are deliberate.

*A miss is not an error.*  In a multi-worker deployment the cancel claim may
land on a process that is not executing the run.  The lookup answers ``None``
and the cancel handler does exactly what it did before this module existed.
Reaching a task in another process is a real distributed problem; pretending to
have solved it here would be worse than not solving it.

*The drain is bounded, and its bound is reported.*  Cancelling a task requests
unwinding; it does not guarantee it.  A run wedged in an uninterruptible section
must not be able to hold the user's Stop button open forever, so the wait has a
ceiling and :attr:`RunCancellationOutcome.drained` says whether the ceiling was
reached — reported, never inferred.

*Why the drain exists at all.*  The run's own cancellation path emits the facts
that close the user's screen: in-flight tool calls settle, and every subagent
still showing "live" gets its terminal frame.  Those are causal events, so they
belong inside the run's sealed prefix — a client's stream closes on the terminal
event and never delivers anything after it (see
:mod:`agent_runtime.api.ledger_seal`).  Terminating first and unwinding second
would therefore produce a durable ledger no live client ever sees, which is the
exact failure mode the seal module was written about.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Final

from agent_runtime.execution.run_steering import RunSteeringInbox


_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LiveRunHandle:
    """One registration: the run, the task executing it, and why it stopped.

    ``cancel_requested`` is carried on the handle rather than in a registry-wide
    set because its reader is the *registration site*, which learns the answer
    only after the registry has already released the entry.  A shared marker
    would have to outlive the registration to be readable, and anything that
    outlives a registration can be read for the wrong run.

    ``steering`` is on the handle for the mirror-image reason: a mailbox that
    outlived the registration would accept a steer for a run this process is no
    longer executing, and that steer would then be delivered to nobody while
    looking delivered.  Bound to the registration, an unreachable run is a
    *miss* — which the steer handler already knows how to report honestly.
    """

    run_id: str
    task: asyncio.Task[object]
    steering: RunSteeringInbox
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class RunCancellationOutcome:
    """What one cancellation request may honestly claim about the run's task."""

    #: A live task for this run existed in this process and was cancelled.
    requested: bool
    #: The task finished unwinding within the drain bound.  ``False`` when the
    #: bound was reached — the work was not observed to stop, only asked to.
    drained: bool

    @classmethod
    def not_found(cls) -> "RunCancellationOutcome":
        """Return the outcome for a run this process is not executing."""

        return cls(requested=False, drained=True)


@dataclass
class LiveRunRegistry:
    """Which runs are executing *in this process*, right now.

    A lookup, not a second cancellation mechanism.  Registration is scoped to
    the claim's own try/finally, so a process that has stopped executing a run
    cannot later be asked to cancel it and answer from a stale task.
    """

    #: A ceiling, not a target: one entry per run this process executes, and the
    #: worker's own claim bound already holds that far below this.
    MAX_TRACKED_RUNS: Final[int] = 1024

    #: How long a cancellation waits for the run to unwind before terminating
    #: anyway.  Generous enough for a run to settle its tool calls and close its
    #: subagent cards; short enough that a wedged run cannot hold Stop open.
    DEFAULT_DRAIN_SECONDS: Final[float] = 10.0

    _handles: dict[str, LiveRunHandle] = field(default_factory=dict)

    @property
    def tracked_runs(self) -> int:
        """Return how many runs this process currently has in flight."""

        return len(self._handles)

    def register(
        self, *, run_id: str | None, task: asyncio.Task[object] | None
    ) -> LiveRunHandle | None:
        """Publish one run's executing task, returning the release handle.

        Returning a handle rather than having the caller re-derive the key keeps
        the removal paired with this exact registration, so a run that was never
        registered releases nothing instead of evicting a namesake.
        """

        key = (run_id or "").strip()
        if not key or task is None or len(self._handles) >= self.MAX_TRACKED_RUNS:
            return None
        handle = LiveRunHandle(run_id=key, task=task, steering=RunSteeringInbox())
        self._handles[key] = handle
        return handle

    def steering_for(self, run_id: str) -> RunSteeringInbox | None:
        """Return the mailbox for a run this process is executing, if it is.

        The steer half of the same join ``cancel`` makes below, and it answers
        with the same honesty: ``None`` when the steer claim landed on a process
        that is not executing this run, which is the ordinary multi-worker case
        rather than a fault.
        """

        handle = self._handles.get((run_id or "").strip())
        return None if handle is None else handle.steering

    def release(self, handle: LiveRunHandle | None) -> None:
        """Withdraw one registration.  Idempotent, and safe on ``None``."""

        if handle is None:
            return
        if self._handles.get(handle.run_id) is handle:
            del self._handles[handle.run_id]

    async def cancel(
        self,
        run_id: str,
        *,
        drain_seconds: float | None = None,
    ) -> RunCancellationOutcome:
        """Stop this run's execution in this process and wait for it to unwind.

        Total by construction: no exception raised by a run that is already over
        can stop the caller from making the run terminal.
        """

        handle = self._handles.get((run_id or "").strip())
        if handle is None:
            return RunCancellationOutcome.not_found()
        if handle.task is asyncio.current_task():
            # The cancel command would be cancelling itself. Nothing in this
            # runtime dispatches both from one task, but a caller that did would
            # otherwise deadlock on its own drain.
            return RunCancellationOutcome.not_found()
        handle.cancel_requested = True
        handle.task.cancel()
        bound = (
            self.DEFAULT_DRAIN_SECONDS
            if drain_seconds is None
            else max(0.0, drain_seconds)
        )
        # ``wait`` rather than ``wait_for``: it observes the task settling
        # without consuming its outcome. Whether the run unwound cleanly or
        # raised on its way out is the run's own terminal path to classify —
        # this outcome reports only that it stopped.
        done, _pending = await asyncio.wait({handle.task}, timeout=bound)
        if not done:
            _LOGGER.warning(
                "run cancellation drain reached its bound run_id=%s seconds=%s",
                handle.run_id,
                bound,
            )
        return RunCancellationOutcome(requested=True, drained=bool(done))


__all__ = (
    "LiveRunHandle",
    "LiveRunRegistry",
    "RunCancellationOutcome",
)
