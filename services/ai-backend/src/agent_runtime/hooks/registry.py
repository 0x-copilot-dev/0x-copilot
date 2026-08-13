"""Explicit in-process hook registration, its run binding, and its ledger.

Registration is **explicit and in-process only**. Nothing here loads code from
disk, from a config document, or from a package name: handing arbitrary
third-party code the tool seam is a trust decision that has not been made, and
making it accidentally by shipping a loader would be the worst way to make it.
A handler gets here by a Python call in this process, from code that was
reviewed and deployed with the runtime.

Two objects, deliberately separate:

* :class:`RuntimeHooks` — the process-level registration table. Written at
  startup / import time, read once per run.
* :class:`HookRegistry` — the immutable snapshot a single run executes against.
  A registration that lands mid-run cannot change what that run does, for the
  same reason ``ToolUsePolicySnapshot`` is frozen at run start.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from agent_runtime.hooks.contracts import (
    HookInvocationRecord,
    HookInvocationStatus,
    HookPhase,
)

#: Bound on the per-run invocation ledger. Beyond it records are dropped and
#: counted rather than growing without limit on a long run.
_MAX_LEDGER_RECORDS = 4_096


@dataclass(frozen=True, slots=True)
class RegisteredHook:
    """One handler bound to one phase, with its deterministic position."""

    name: str
    phase: HookPhase
    handler: Callable[[Any], Any]
    order: int


class HookRegistry:
    """Immutable per-run snapshot of the registered handlers."""

    __slots__ = ("_by_phase",)

    def __init__(self, hooks: Sequence[RegisteredHook] = ()) -> None:
        by_phase: dict[HookPhase, list[RegisteredHook]] = {}
        for hook in sorted(hooks, key=lambda item: item.order):
            by_phase.setdefault(hook.phase, []).append(hook)
        self._by_phase: dict[HookPhase, tuple[RegisteredHook, ...]] = {
            phase: tuple(items) for phase, items in by_phase.items()
        }

    def for_phase(self, phase: HookPhase) -> tuple[RegisteredHook, ...]:
        """Return this phase's handlers in registration order, always."""

        return self._by_phase.get(phase, ())


class RuntimeHooks:
    """Process-level explicit registration table."""

    _lock = Lock()
    _hooks: list[RegisteredHook] = []
    _next_order = 0

    @classmethod
    def register(
        cls,
        *,
        phase: HookPhase,
        name: str,
        handler: Callable[[Any], Any],
    ) -> RegisteredHook:
        """Register one handler. ``(phase, name)`` must be unique.

        Handlers are **synchronous**. The tool and model seams both have a sync
        and an async form, and a sync handler is callable from either without a
        nested event loop or a thread-pool decision on the hottest path in the
        system. A handler that returns an awaitable is a contract violation at
        dispatch, never a silently un-awaited coroutine.
        """

        normalized = name.strip()
        if not normalized:
            raise ValueError("hook name must be non-empty")
        if not callable(handler):
            raise TypeError("hook handler must be callable")
        with cls._lock:
            if any(
                hook.phase is phase and hook.name == normalized for hook in cls._hooks
            ):
                raise ValueError(
                    f"hook '{normalized}' is already registered on {phase}"
                )
            registered = RegisteredHook(
                name=normalized,
                phase=phase,
                handler=handler,
                order=cls._next_order,
            )
            cls._next_order += 1
            cls._hooks.append(registered)
            return registered

    @classmethod
    def snapshot(cls) -> HookRegistry:
        """Freeze the current table for one run."""

        with cls._lock:
            return HookRegistry(tuple(cls._hooks))

    @classmethod
    def clear(cls) -> None:
        """Drop every registration. Test affordance, not a runtime operation."""

        with cls._lock:
            cls._hooks = []
            cls._next_order = 0


class HookLedger:
    """Bounded, thread-safe record of one run's hook invocations."""

    __slots__ = ("_lock", "_records", "_dropped", "_max_records")

    def __init__(self, *, max_records: int = _MAX_LEDGER_RECORDS) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._lock = Lock()
        self._records: list[HookInvocationRecord] = []
        self._dropped = 0
        self._max_records = max_records

    def record(self, record: HookInvocationRecord) -> None:
        """Append one invocation record, or count it as dropped when full."""

        with self._lock:
            if len(self._records) >= self._max_records:
                self._dropped += 1
                return
            self._records.append(record)

    def records(self) -> tuple[HookInvocationRecord, ...]:
        """Return the invocation records in the order they were observed."""

        with self._lock:
            return tuple(self._records)

    @property
    def dropped(self) -> int:
        """Invocations observed after the ledger bound was reached."""

        with self._lock:
            return self._dropped

    def summary(self) -> HookLedgerSummary | None:
        """Roll the ledger up for emission, or ``None`` when nothing ran.

        ``None`` is the common case — no hooks are registered by default — and
        it is what keeps the run handler from logging a line per run about a
        seam nobody is using.
        """

        records = self.records()
        if not records:
            return None
        by_status: dict[str, int] = {}
        by_phase: dict[str, int] = {}
        for record in records:
            by_status[record.status.value] = by_status.get(record.status.value, 0) + 1
            by_phase[record.phase.value] = by_phase.get(record.phase.value, 0) + 1
        return HookLedgerSummary(
            invocations=len(records),
            modified=sum(1 for record in records if record.modified),
            failed=sum(
                1 for record in records if record.status is not HookInvocationStatus.OK
            ),
            total_duration_us=sum(record.duration_us for record in records),
            dropped=self.dropped,
            by_status=by_status,
            by_phase=by_phase,
        )


@dataclass(frozen=True, slots=True)
class HookLedgerSummary:
    """One run's hook activity, shaped for a structured log line.

    Deliberately free of hook-authored strings: only counts, phase names and
    status names from the closed enums travel here, so a handler cannot write
    into the operator's logs.
    """

    invocations: int
    modified: int
    failed: int
    total_duration_us: int
    dropped: int
    by_status: dict[str, int]
    by_phase: dict[str, int]

    def as_log_fields(self) -> dict[str, object]:
        """Flat, low-cardinality fields for the run handler's summary line."""

        return {
            "hook_invocations": self.invocations,
            "hook_modified": self.modified,
            "hook_failed": self.failed,
            "hook_duration_us": self.total_duration_us,
            "hook_dropped": self.dropped,
            "hook_by_status": dict(sorted(self.by_status.items())),
            "hook_by_phase": dict(sorted(self.by_phase.items())),
        }


@dataclass(frozen=True, slots=True)
class HookSession:
    """The registry + ledger pair one run executes against."""

    registry: HookRegistry
    ledger: HookLedger = field(default_factory=HookLedger)


_CURRENT_HOOK_SESSION: ContextVar[HookSession | None] = ContextVar(
    "agent_runtime_hook_session",
    default=None,
)


class RuntimeHookContext:
    """Run-local access to the frozen hook session.

    ``None`` is a normal answer: a legacy or direct-factory path that never
    bound a session simply has no hooks, and every dispatch degrades to the
    unhooked behaviour rather than raising.
    """

    @staticmethod
    def bind_for_run(session: HookSession | None = None) -> Token[HookSession | None]:
        """Bind a session (defaulting to a snapshot of the process table)."""

        return _CURRENT_HOOK_SESSION.set(
            session if session is not None else HookSession(RuntimeHooks.snapshot())
        )

    @staticmethod
    def current() -> HookSession | None:
        """Return the active session, or ``None`` when nothing is bound."""

        return _CURRENT_HOOK_SESSION.get()

    @staticmethod
    def unbind(token: Token[HookSession | None]) -> None:
        """Restore the session that preceded ``token``."""

        _CURRENT_HOOK_SESSION.reset(token)


__all__ = [
    "HookLedger",
    "HookLedgerSummary",
    "HookRegistry",
    "HookSession",
    "RegisteredHook",
    "RuntimeHookContext",
    "RuntimeHooks",
]
