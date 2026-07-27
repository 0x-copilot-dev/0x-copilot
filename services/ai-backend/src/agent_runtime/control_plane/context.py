"""Read-only execution binding for one verified run-control snapshot."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import AsyncIterator, Iterator

from pydantic import Field

from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeDecision,
    FeatureModeSet,
)
from agent_runtime.execution.contracts import RuntimeContract


class RunControlBinding(RuntimeContract):
    """Immutable snapshot plus its authority-narrowed effective feature modes."""

    snapshot: RunControlSnapshot
    effective_modes: FeatureModeSet
    decisions: tuple[FeatureModeDecision, ...]

    def mode_for(self, feature: AgentQualityFeature) -> FeatureMode:
        """Return the effective, never-broadened mode for ``feature``."""

        return self.effective_modes.mode_for(feature)


_CURRENT_BINDING: ContextVar[RunControlBinding | None] = ContextVar(
    "agent_runtime_run_control_binding",
    default=None,
)
_CURRENT_SERIAL_ADMISSION: ContextVar["RunSerialAdmission | None"] = ContextVar(
    "agent_runtime_run_serial_admission",
    default=None,
)
_CURRENT_LIFECYCLE_REDUCER: ContextVar["RuntimeToolLifecycleReducer | None"] = (
    ContextVar(
        "agent_runtime_tool_lifecycle_reducer",
        default=None,
    )
)


class RuntimeToolControlOutcome(StrEnum):
    """Content-free terminal classifications at the graph tool seam."""

    SUCCESS = "success"
    ERROR = "error"
    INTERRUPT = "interrupt"
    COMMAND = "command"
    CANCELLED = "cancelled"


class RuntimeToolControlTerminalRecord(RuntimeContract):
    """Exactly one terminal outcome for one stable framework execution attempt."""

    control_call_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    operation_id: str = Field(min_length=1, max_length=255)
    execution_scope: str = Field(min_length=1, max_length=320)
    outcome: RuntimeToolControlOutcome


class RuntimeToolLifecycleReducer:
    """Bounded idempotent run ledger keyed by call and execution attempt."""

    def __init__(
        self,
        *,
        max_records: int = 4096,
        initial_records: tuple[RuntimeToolControlTerminalRecord, ...] = (),
    ) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        if len(initial_records) > max_records:
            raise ValueError("initial lifecycle records exceed the configured bound")
        self._max_records = max_records
        self._lock = Lock()
        self._open: set[tuple[str, str]] = set()
        self._terminal: dict[
            tuple[str, str],
            RuntimeToolControlTerminalRecord,
        ] = {}
        for record in initial_records:
            key = (record.control_call_id, record.attempt_id)
            existing = self._terminal.get(key)
            if existing is not None and existing != record:
                raise ValueError("conflicting initial lifecycle terminal records")
            self._terminal[key] = record

    def observe_open(self, *, control_call_id: str, attempt_id: str) -> None:
        """Observe an execution start once; terminal replays stay closed."""

        key = self._key(control_call_id=control_call_id, attempt_id=attempt_id)
        with self._lock:
            if key in self._terminal:
                return
            if key not in self._open and (
                len(self._open) + len(self._terminal) >= self._max_records
            ):
                raise RuntimeError("runtime tool lifecycle record bound exhausted")
            self._open.add(key)

    def observe_terminal(
        self,
        *,
        control_call_id: str,
        attempt_id: str,
        operation_id: str,
        execution_scope: str,
        outcome: RuntimeToolControlOutcome,
    ) -> RuntimeToolControlTerminalRecord:
        """Fold one terminal outcome idempotently for this exact attempt."""

        key = self._key(control_call_id=control_call_id, attempt_id=attempt_id)
        candidate = RuntimeToolControlTerminalRecord(
            control_call_id=key[0],
            attempt_id=key[1],
            operation_id=operation_id,
            execution_scope=execution_scope,
            outcome=outcome,
        )
        with self._lock:
            existing = self._terminal.get(key)
            if existing is not None:
                if existing != candidate:
                    raise RuntimeError(
                        "conflicting terminal outcome for runtime tool attempt"
                    )
                return existing
            if key not in self._open and (
                len(self._open) + len(self._terminal) >= self._max_records
            ):
                raise RuntimeError("runtime tool lifecycle record bound exhausted")
            self._open.discard(key)
            self._terminal[key] = candidate
            return candidate

    def records(self) -> tuple[RuntimeToolControlTerminalRecord, ...]:
        """Return deterministic content-free terminal records."""

        with self._lock:
            return tuple(self._terminal[key] for key in sorted(self._terminal))

    @staticmethod
    def _key(*, control_call_id: str, attempt_id: str) -> tuple[str, str]:
        normalized_call = control_call_id.strip()
        normalized_attempt = attempt_id.strip()
        if not normalized_call or not normalized_attempt:
            raise ValueError("runtime tool lifecycle ids must be non-empty")
        return (normalized_call, normalized_attempt)


class RunSerialAdmission:
    """One conservative serial permit shared by a run and all local children."""

    def __init__(self) -> None:
        self._async_lock = asyncio.Lock()
        self._sync_lock = Lock()

    @asynccontextmanager
    async def async_permit(self) -> AsyncIterator[None]:
        """Admit one async tool call for this run."""

        async with self._async_lock:
            yield

    @contextmanager
    def sync_permit(self) -> Iterator[None]:
        """Admit one synchronous tool call for this run."""

        with self._sync_lock:
            yield


@dataclass(frozen=True)
class _RunControlContextToken:
    binding: Token[RunControlBinding | None]
    serial_admission: Token[RunSerialAdmission | None]
    lifecycle_reducer: Token[RuntimeToolLifecycleReducer | None]


class RunControlContext:
    """Read-only run-local access to the verified immutable binding."""

    @staticmethod
    def bind_for_run(binding: RunControlBinding) -> _RunControlContextToken:
        """Bind ``binding`` for one worker execution or approval continuation."""

        return _RunControlContextToken(
            binding=_CURRENT_BINDING.set(binding),
            serial_admission=_CURRENT_SERIAL_ADMISSION.set(RunSerialAdmission()),
            lifecycle_reducer=_CURRENT_LIFECYCLE_REDUCER.set(
                RuntimeToolLifecycleReducer()
            ),
        )

    @staticmethod
    def current() -> RunControlBinding | None:
        """Return the active verified binding, if this is a legacy/test path."""

        return _CURRENT_BINDING.get()

    @staticmethod
    def require_current() -> RunControlBinding:
        """Return the active binding or fail closed at an authoritative seam."""

        binding = _CURRENT_BINDING.get()
        if binding is None:
            raise RuntimeError("run control is not bound")
        return binding

    @staticmethod
    def serial_admission() -> RunSerialAdmission | None:
        """Return the run-shared serial permit, including in local subagents."""

        return _CURRENT_SERIAL_ADMISSION.get()

    @staticmethod
    def lifecycle_reducer() -> RuntimeToolLifecycleReducer | None:
        """Return the run-shared content-free tool lifecycle reducer."""

        return _CURRENT_LIFECYCLE_REDUCER.get()

    @staticmethod
    def unbind(token: _RunControlContextToken) -> None:
        """Restore the binding that preceded ``token``."""

        _CURRENT_LIFECYCLE_REDUCER.reset(token.lifecycle_reducer)
        _CURRENT_SERIAL_ADMISSION.reset(token.serial_admission)
        _CURRENT_BINDING.reset(token.binding)


__all__ = [
    "RunControlBinding",
    "RunControlContext",
    "RunSerialAdmission",
    "RuntimeToolControlOutcome",
    "RuntimeToolControlTerminalRecord",
    "RuntimeToolLifecycleReducer",
]
