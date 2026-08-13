"""Mid-run steering: a user message delivered into a run already in flight.

The shape is cancellation's, deliberately.  A steer arrives the same way a Stop
does — as a *queued command* claimed out of band, never as an in-process signal
— because the process that must react is executing a graph and cannot be reached
by anything the request path holds.  ``runtime_worker.run_cancellation`` already
solved the hard half of that (the join between a claim and the task executing the
run); this module is its delivery-shaped sibling.

Two properties are the whole design.

*Delivery happens at the model step, never mid-tool.*  A tool call is an
in-flight external effect: interrupting one leaves a write half-applied and a
lifecycle card open, and cancellation already owns the "stop now" semantics.  A
steer is not a stop — it is context the model should read — so it lands where
context is assembled: :class:`RuntimeControlMiddleware`'s ``before_model`` seam,
which runs after the previous tool node has fully settled and before the next
provider dispatch.  The mailbox is what makes the wait possible: a steer that
arrives at 40% of a 30-second tool call simply sits here until the turn ends.

*The mailbox is per run and process-local, and a miss is not an error.*  In a
multi-worker deployment the steer claim may land on a process that is not
executing the run.  ``LiveRunRegistry.steering_for`` — the same registration
cancellation already joins through — answers with a miss, the handler reports
that the message was not delivered, and nothing pretends otherwise, the same
honesty rule ``RunCancellationOutcome`` is built around.  The user's steer is
still a durable transcript fact either way; only its *delivery* is best-effort,
which is exactly why ``SteerRunResponse`` carries no ``delivered`` field.
"""

from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from typing import ClassVar, Final
from uuid import uuid4

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract


class SteeringMessage(RuntimeContract):
    """One user interjection addressed to a run that is already executing.

    Validated here rather than at the edge alone because this object crosses
    three boundaries — HTTP body, durable queue command, and model-visible
    context — and the last of those is the one that must never carry an
    unbounded string into a provider request.
    """

    class Prompt:
        """The exact framing an injected steer wears when the model reads it.

        Delimited and labelled on purpose. The model is mid-task with a
        committed plan, and an unlabelled sentence appended to the transcript
        reads as either a tool result or a system instruction depending on
        position. Naming the author and the timing is what makes it *steering*
        rather than noise.
        """

        OPEN: Final[str] = "<user_steering>"
        CLOSE: Final[str] = "</user_steering>"
        PREAMBLE: Final[str] = (
            "The user sent this while you were still working on the current "
            "request. It is their most recent instruction: treat it as an "
            "amendment to the task, re-plan if it changes your approach, and "
            "acknowledge the change in your next response."
        )

    #: Bounded because this text is appended to a live model request. The cap is
    #: a guard on the injection path, not a product limit on what a user may
    #: type — a longer thought belongs in the next turn, which has a whole
    #: context window for it.
    #:
    #: ``ClassVar`` rather than ``Final``: inside a Pydantic model ``Final`` is
    #: a *field* annotation in V3, which would put a 4000 on the wire and let a
    #: caller re-declare the cap that bounds them.
    MAX_TEXT_LENGTH: ClassVar[int] = 4000

    steer_id: str = Field(
        default_factory=lambda: f"steer_{uuid4().hex}",
        min_length=1,
        max_length=128,
    )
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    requested_by_user_id: str = Field(min_length=1, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def as_model_text(self) -> str:
        """Render the framed block the model reads at the next model step."""

        return "\n".join(
            (
                self.Prompt.OPEN,
                self.Prompt.PREAMBLE,
                "",
                self.text,
                self.Prompt.CLOSE,
            )
        )


class RunSteeringInbox:
    """The per-run mailbox a steer waits in until the next model step.

    Guarded by a ``threading.Lock`` rather than an asyncio primitive: the
    depositing side is the worker's steer claim (asyncio), while the draining
    side is a middleware hook that LangGraph may invoke from its synchronous
    ``ToolNode`` thread pool. One lock covers both, and neither side ever holds
    it across an await.
    """

    #: A backstop, not a product limit. A user hammering the composer during a
    #: long turn must not be able to grow an unbounded list that is then
    #: appended, in full, to a provider request.
    MAX_PENDING: Final[int] = 16

    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: deque[SteeringMessage] = deque()

    @property
    def pending(self) -> int:
        """Return how many steers are waiting for the next model step."""

        with self._lock:
            return len(self._pending)

    def deposit(self, message: SteeringMessage) -> bool:
        """Queue one steer; report whether the mailbox accepted it.

        A rejected deposit is reported rather than raised: the caller is a queue
        handler whose command is already durable, and a full mailbox is a
        throttle, not a failure of the command.
        """

        with self._lock:
            if len(self._pending) >= self.MAX_PENDING:
                return False
            self._pending.append(message)
            return True

    def drain(self) -> tuple[SteeringMessage, ...]:
        """Take every waiting steer, in arrival order, exactly once.

        Consume-once is the point. The drained messages join the conversation
        state the model carries forward, so a mailbox that re-served them would
        re-append the same interjection at every remaining turn of the run.

        No delivered-log is kept here. The durable record of what the user sent
        is the run's own ``run_steered`` ledger event, and a second in-memory
        copy would be an unread list growing for the life of the run.
        """

        with self._lock:
            drained = tuple(self._pending)
            self._pending.clear()
            return drained


class RunSteeringContext:
    """The binding that lets a graph-internal seam find its run's mailbox.

    The run's claim binds the inbox once, at the top of execution, so every
    task LangGraph creates below it inherits the binding. Nothing deeper has to
    thread a run id through the graph, and nothing outside the run can read it.
    """

    _CURRENT: ContextVar["RunSteeringInbox | None"] = ContextVar(
        "run_steering_inbox",
        default=None,
    )

    @classmethod
    def bind_for_run(cls, inbox: "RunSteeringInbox | None") -> object:
        """Set the active mailbox; return the token that restores the previous."""

        return cls._CURRENT.set(inbox)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous mailbox. Safe to call with the bind result."""

        cls._CURRENT.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "RunSteeringInbox | None":
        """Return the mailbox bound to this execution, or ``None``."""

        return cls._CURRENT.get(None)

    @classmethod
    def drain(cls) -> tuple[SteeringMessage, ...]:
        """Take every waiting steer for the active run.

        Total: a graph with no binding (a legacy or test composition) drains
        nothing instead of raising, which keeps this seam invisible to every
        run that is not being steered.
        """

        inbox = cls.active()
        if inbox is None:
            return ()
        return inbox.drain()


__all__ = (
    "RunSteeringContext",
    "RunSteeringInbox",
    "SteeringMessage",
)
