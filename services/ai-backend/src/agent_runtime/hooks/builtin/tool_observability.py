"""Per-tool wall time and result footprint, observed through the hook seam.

What this answers that nothing else does
========================================

For a finished run: *which tools did it call, how long did each one take, and
how much model-visible text did each one push into the context window* — split
by tool name and execution scope. Today that is answerable nowhere:

* :class:`~agent_runtime.control_plane.context.RuntimeToolLifecycleReducer`
  records one terminal *outcome* per (call, attempt). No timing, no size.
* The worker audit emitter's tool-call-outcome method
  (``runtime_worker/audit.py:197``) takes a duration and has **no production
  caller at all** — only a test reaches it, so that audit action is never
  emitted. It is line 117 of ``tools/dark_wiring_baseline.txt``, and it is
  deliberately not named here in full: that gate's "is this wired?" test is a
  regex over file text, so spelling the symbol out in prose would silence a
  real finding with a comment.
* :mod:`agent_runtime.observability.context_tool_ledger` measures the tool
  *schema* block (what the model is shown before it calls anything), not tool
  *results*.
* The Context Occupancy Ledger measures results as a lifecycle *class*
  (``PER_RESULT``) at the model-call seam. It is a per-class total; it does not
  attribute a byte to the tool that produced it.

Per-tool attribution of result text is the one that pays for itself right now.
``tools/harness-bench/FINDINGS.md`` measured a cold prompt at 22,304 input
tokens, 9,159 of them tool schemas — the schema half already has a ledger.
Result text is the half that grows *during* a run, and "which tool is filling
the window" is not a question this service can currently answer.

Why a hook rather than five lines inside ``wrap_tool_call``
==========================================================

Inline timing would be marginally cheaper, and it would give up three things
this seam provides for free:

* **Isolation.** ``HookDispatch._invoke`` catches ``Exception`` around every
  handler, so a bug in this observer records a ``failed`` row and the run
  continues. Inline code in ``wrap_tool_call`` sits on the dispatch path of
  every tool call in the system with no such guard.
* **Self-metering.** Each invocation's own cost lands on the run's hook ledger,
  so the observability has observability. Inline code is invisible.
* **One shape for the next one.** The seam already carries the veto and rewrite
  affordances; adding a second, differently-shaped observation path beside it
  would be the duplication this repo keeps paying for.

Non-widening, structurally
==========================

Both handlers are registered on *writable* phases, and both decline to use
them: **the only ``return`` statement in either handler is a bare ``return``**.
``HookDispatch._invoke`` treats ``None`` as "declined to act"
(``dispatch.py:259``), so neither handler can veto a call, rewrite arguments,
or rewrite a result — that is a fact about four lines of code, not a promise.
They also never read a payload field back into the call: the ledger is
write-only from the run's point of view until the run handler drains it.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import Lock
import time

from agent_runtime.hooks.contracts import (
    HookPhase,
    ToolExecuteAfterInput,
    ToolExecuteBeforeInput,
)
from agent_runtime.hooks.registry import RuntimeHooks

#: The name both handlers register under. One name across two phases is legal
#: (uniqueness is per ``(phase, name)``) and is what makes the pair readable as
#: one observer in the hook ledger's ``by_phase`` breakdown.
HOOK_NAME = "runtime.tool_observability"

#: Distinct tool names tracked before the tally folds the rest into ``_OTHER``.
#: A run cannot mint unbounded tool names today, but the log line this feeds is
#: an operator surface and low cardinality is not something to leave to luck.
_MAX_TRACKED_TOOLS = 256
#: In-flight calls tracked at once. Beyond it a start stamp is dropped and the
#: call is still counted when it settles, as ``untimed`` rather than missing.
_MAX_PENDING_CALLS = 4_096
_OTHER = "other"


@dataclass(frozen=True, slots=True)
class ToolCallObservation:
    """One tool name's roll-up across a single run."""

    tool_name: str
    calls: int
    failures: int
    total_duration_us: int
    max_duration_us: int
    result_chars: int


@dataclass(frozen=True, slots=True)
class ToolCallObservationSummary:
    """One run's tool-path activity, shaped for a structured log line.

    Counts, durations and tool names only. No arguments, no result text, no
    paths, no identifiers — the same discipline
    :class:`~agent_runtime.hooks.registry.HookLedgerSummary` keeps, for the same
    reason: this line is written on every run that calls a tool.
    """

    calls: int
    failures: int
    unsettled: int
    untimed: int
    total_duration_us: int
    result_chars: int
    by_tool: tuple[ToolCallObservation, ...]

    def as_log_fields(self) -> dict[str, object]:
        """Flat, low-cardinality fields for the run handler's summary line."""

        return {
            "tool_calls": self.calls,
            "tool_failures": self.failures,
            "tool_unsettled": self.unsettled,
            "tool_untimed": self.untimed,
            "tool_duration_us": self.total_duration_us,
            "tool_result_chars": self.result_chars,
            "tool_by_name": {
                observation.tool_name: {
                    "calls": observation.calls,
                    "failures": observation.failures,
                    "duration_us": observation.total_duration_us,
                    "max_duration_us": observation.max_duration_us,
                    "result_chars": observation.result_chars,
                }
                for observation in self.by_tool
            },
        }


class ToolCallObservationLedger:
    """Bounded, thread-safe tally of one run's tool calls.

    Thread-safe because it has to be: LangGraph's synchronous ``ToolNode`` fans
    a turn's calls across a thread pool and its async form gathers them as
    concurrent tasks, so ``start`` and ``settle`` genuinely race.
    """

    __slots__ = ("_lock", "_pending", "_tools", "_untimed")

    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: dict[tuple[str, str], int] = {}
        self._tools: dict[str, list[int]] = {}
        self._untimed = 0

    def start(self, *, execution_scope: str, tool_call_id: str) -> None:
        """Stamp a call's start. Bounded; never raises.

        A start dropped at the bound is not counted here. It surfaces when the
        call settles — as ``untimed``, the same row a call that never reached
        ``tool.execute.before`` produces — rather than as a second counter that
        would double-report the same call.
        """

        key = (execution_scope, tool_call_id)
        started = time.perf_counter_ns()
        with self._lock:
            if key not in self._pending and len(self._pending) >= _MAX_PENDING_CALLS:
                return
            self._pending[key] = started

    def settle(
        self,
        *,
        execution_scope: str,
        tool_call_id: str,
        tool_name: str,
        succeeded: bool,
        result_chars: int,
    ) -> None:
        """Fold one completed call into the tally. Never raises."""

        ended = time.perf_counter_ns()
        with self._lock:
            started = self._pending.pop((execution_scope, tool_call_id), None)
            if started is None:
                self._untimed += 1
                duration_us = 0
            else:
                duration_us = max(0, (ended - started) // 1_000)
            name = tool_name.strip() or _OTHER
            slot = self._tools.get(name)
            if slot is None:
                if len(self._tools) >= _MAX_TRACKED_TOOLS:
                    name = _OTHER
                    slot = self._tools.get(_OTHER)
                if slot is None:
                    slot = [0, 0, 0, 0, 0]
                    self._tools[name] = slot
            slot[0] += 1
            slot[1] += 0 if succeeded else 1
            slot[2] += duration_us
            slot[3] = max(slot[3], duration_us)
            slot[4] += max(0, result_chars)

    def summary(self) -> ToolCallObservationSummary | None:
        """Roll the tally up for emission, or ``None`` when no tool ran.

        ``None`` is the common case for a chat turn that answers directly, and
        it is what keeps the run handler from logging an empty line per run.
        A call that started and never settled still counts: it is reported as
        ``unsettled`` rather than dropped, because a tool that raised through
        the seam is exactly the thing an operator wants to see.
        """

        with self._lock:
            if not self._tools and not self._pending:
                return None
            by_tool = tuple(
                ToolCallObservation(
                    tool_name=name,
                    calls=slot[0],
                    failures=slot[1],
                    total_duration_us=slot[2],
                    max_duration_us=slot[3],
                    result_chars=slot[4],
                )
                for name, slot in sorted(self._tools.items())
            )
            return ToolCallObservationSummary(
                calls=sum(observation.calls for observation in by_tool),
                failures=sum(observation.failures for observation in by_tool),
                unsettled=len(self._pending),
                untimed=self._untimed,
                total_duration_us=sum(
                    observation.total_duration_us for observation in by_tool
                ),
                result_chars=sum(observation.result_chars for observation in by_tool),
                by_tool=by_tool,
            )


_CURRENT_LEDGER: ContextVar[ToolCallObservationLedger | None] = ContextVar(
    "agent_runtime_tool_observation_ledger",
    default=None,
)


class ToolCallObservationContext:
    """Run-local access to the tally the handlers write into.

    ``None`` is a normal answer, and it is the reason this builtin cannot be
    installed by accident into a path that does not want it: a caller that
    never binds a ledger runs both handlers to completion and they record
    nothing. Registration alone changes no behaviour; binding does.
    """

    @staticmethod
    def bind_for_run(
        ledger: ToolCallObservationLedger | None = None,
    ) -> Token[ToolCallObservationLedger | None]:
        """Bind a fresh (or supplied) ledger for the duration of one run."""

        return _CURRENT_LEDGER.set(
            ledger if ledger is not None else ToolCallObservationLedger()
        )

    @staticmethod
    def current() -> ToolCallObservationLedger | None:
        """Return the active ledger, or ``None`` when nothing is bound."""

        return _CURRENT_LEDGER.get()

    @staticmethod
    def unbind(token: Token[ToolCallObservationLedger | None]) -> None:
        """Restore the ledger that preceded ``token``."""

        _CURRENT_LEDGER.reset(token)


def _observe_tool_start(payload: ToolExecuteBeforeInput) -> None:
    """``tool.execute.before``: stamp the start time, decide nothing."""

    ledger = ToolCallObservationContext.current()
    if ledger is not None:
        ledger.start(
            execution_scope=payload.execution_scope,
            tool_call_id=payload.tool_call_id,
        )
    return


def _observe_tool_end(payload: ToolExecuteAfterInput) -> None:
    """``tool.execute.after``: fold the completed call in, rewrite nothing."""

    ledger = ToolCallObservationContext.current()
    if ledger is not None:
        ledger.settle(
            execution_scope=payload.execution_scope,
            tool_call_id=payload.tool_call_id,
            tool_name=payload.tool_name,
            succeeded=payload.succeeded,
            result_chars=len(payload.result_text or ""),
        )
    return


_INSTALL_LOCK = Lock()


def install_builtin_hooks() -> bool:
    """Register the runtime's own hooks once per process; return whether it did.

    Idempotent, because the single chokepoint that calls it
    (``RuntimeRunHandler.__init__``) legitimately runs more than once in a
    process — the worker and the API's in-process worker both build a handler,
    and a test suite builds dozens. ``RuntimeHooks.register`` raises on a
    duplicate ``(phase, name)`` by design, so the check belongs here rather
    than a swallowed exception there.
    """

    with _INSTALL_LOCK:
        registered = RuntimeHooks.snapshot().for_phase(HookPhase.TOOL_EXECUTE_BEFORE)
        if any(hook.name == HOOK_NAME for hook in registered):
            return False
        RuntimeHooks.register(
            phase=HookPhase.TOOL_EXECUTE_BEFORE,
            name=HOOK_NAME,
            handler=_observe_tool_start,
        )
        RuntimeHooks.register(
            phase=HookPhase.TOOL_EXECUTE_AFTER,
            name=HOOK_NAME,
            handler=_observe_tool_end,
        )
        return True


__all__ = [
    "HOOK_NAME",
    "ToolCallObservation",
    "ToolCallObservationContext",
    "ToolCallObservationLedger",
    "ToolCallObservationSummary",
    "install_builtin_hooks",
]
