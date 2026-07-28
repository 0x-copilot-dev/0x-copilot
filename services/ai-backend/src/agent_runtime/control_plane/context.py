"""Read-only execution binding for one verified run-control snapshot."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import (
    TYPE_CHECKING,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Protocol,
)

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_runtime.capabilities.task_policy import (
    TaskPolicyProfile,
    TaskPolicySelection,
    ToolOperationOutcome,
    ToolUseFeedback,
    ToolUseIntent,
)
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeDecision,
    FeatureModeSet,
)
from agent_runtime.execution.contracts import RuntimeContract

if TYPE_CHECKING:
    from agent_runtime.prompts.runtime_binding import PromptRuntimeBinding


class RunControlBinding(RuntimeContract):
    """Immutable snapshot plus its authority-narrowed effective feature modes."""

    snapshot: RunControlSnapshot
    effective_modes: FeatureModeSet
    decisions: tuple[FeatureModeDecision, ...]

    def mode_for(self, feature: AgentQualityFeature) -> FeatureMode:
        """Return the effective, never-broadened mode for ``feature``."""

        return self.effective_modes.mode_for(feature)


class TaskPolicyControllerPort(Protocol):
    """Narrow domain-controller seam shared by supervisor and local children."""

    def before_operation(
        self,
        intent: ToolUseIntent,
    ) -> ToolUseFeedback | Awaitable[ToolUseFeedback]: ...

    def after_operation(
        self,
        outcome: ToolOperationOutcome,
    ) -> ToolUseFeedback | Awaitable[ToolUseFeedback]: ...


class TaskPolicyRuntimeControllerPort(TaskPolicyControllerPort, Protocol):
    """Full graph-seam contract supplied by the durable F4 domain lane."""

    def before_model_turn(
        self,
        *,
        model_turn: int,
        execution_scope: str,
    ) -> ToolUseFeedback | Awaitable[ToolUseFeedback]: ...

    def observe_upstream_policy_block(
        self,
        intent: ToolUseIntent,
    ) -> ToolUseFeedback | Awaitable[ToolUseFeedback]: ...


class TaskPolicyFingerprintPort(Protocol):
    """Keyed canonical fingerprints; protected bodies never leave the process."""

    def for_request(
        self,
        *,
        capability_id: str,
        arguments: Mapping[str, object],
    ) -> str: ...

    def for_result(
        self,
        *,
        capability_id: str,
        result_metadata: Mapping[str, object],
    ) -> str: ...

    def for_error(
        self,
        *,
        capability_id: str,
        request_fingerprint: str,
        error_class: str,
        retryable: bool,
        retry_hint: str | None = None,
    ) -> str: ...


class TaskPolicyCapabilityProgress(RuntimeContract):
    """Content-free durable usage for one registered capability."""

    capability_id: str = Field(min_length=1, max_length=240)
    tool_calls_used: NonNegativeInt = 0
    input_tokens_used: NonNegativeInt = 0
    tool_call_limit: PositiveInt | None = None


class TaskPolicyProgressProjection(RuntimeContract):
    """Bounded F4 handoff consumed by later prompt assembly, never prompt text."""

    profile_id: str = Field(min_length=1, max_length=160)
    profile_revision: str = Field(min_length=1, max_length=160)
    task_family: str = Field(min_length=1, max_length=80)
    model_turns_used: NonNegativeInt = 0
    model_turn_limit: PositiveInt | None = None
    tool_calls_used: NonNegativeInt = 0
    tool_call_limit: PositiveInt | None = None
    cost_microusd_used: NonNegativeInt = 0
    cost_microusd_limit: NonNegativeInt | None = None
    deadline_epoch_ms: NonNegativeInt | None = None
    completed_steps: NonNegativeInt = 0
    total_steps: NonNegativeInt = 0
    capabilities: tuple[TaskPolicyCapabilityProgress, ...] = Field(
        default=(),
        max_length=256,
    )


TaskPolicyProgressProjector = Callable[[], TaskPolicyProgressProjection]


@dataclass(frozen=True, slots=True)
class TaskPolicyRuntimeBinding:
    """One replayed F4 selection/controller shared by the complete run graph."""

    selection: TaskPolicySelection
    profile: TaskPolicyProfile
    controller: TaskPolicyRuntimeControllerPort
    fingerprinter: TaskPolicyFingerprintPort
    mode: FeatureMode
    progress_projector: TaskPolicyProgressProjector

    def progress(self) -> TaskPolicyProgressProjection:
        """Return the latest typed, bounded prompt/progress projection."""

        return TaskPolicyProgressProjection.model_validate(self.progress_projector())


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
_CURRENT_TASK_POLICY: ContextVar[TaskPolicyRuntimeBinding | None] = ContextVar(
    "agent_runtime_task_policy_binding",
    default=None,
)


@dataclass(slots=True)
class _PromptRuntimeSlot:
    """Run-lifetime slot shared by graph tasks and local child graphs."""

    binding: PromptRuntimeBinding | None = None


_CURRENT_PROMPT_RUNTIME: ContextVar[_PromptRuntimeSlot | None] = ContextVar(
    "agent_runtime_prompt_runtime_slot",
    default=None,
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
    task_policy: Token[TaskPolicyRuntimeBinding | None]
    prompt_runtime: Token[_PromptRuntimeSlot | None]


class RunControlContext:
    """Read-only run-local access to the verified immutable binding."""

    @staticmethod
    def bind_for_run(
        binding: RunControlBinding,
        *,
        task_policy: TaskPolicyRuntimeBinding | None = None,
    ) -> _RunControlContextToken:
        """Bind ``binding`` for one worker execution or approval continuation."""

        return _RunControlContextToken(
            binding=_CURRENT_BINDING.set(binding),
            serial_admission=_CURRENT_SERIAL_ADMISSION.set(RunSerialAdmission()),
            lifecycle_reducer=_CURRENT_LIFECYCLE_REDUCER.set(
                RuntimeToolLifecycleReducer()
            ),
            task_policy=_CURRENT_TASK_POLICY.set(task_policy),
            prompt_runtime=_CURRENT_PROMPT_RUNTIME.set(_PromptRuntimeSlot()),
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
    def task_policy() -> TaskPolicyRuntimeBinding | None:
        """Return the replayed F4 binding inherited by all local subagents."""

        return _CURRENT_TASK_POLICY.get()

    @staticmethod
    def task_policy_progress() -> TaskPolicyProgressProjection | None:
        """Return the sole typed Step 5 progress/budget handoff."""

        binding = _CURRENT_TASK_POLICY.get()
        return None if binding is None else binding.progress()

    @staticmethod
    def install_prompt_runtime(binding: PromptRuntimeBinding) -> None:
        """Install the run-scoped F2 provider once during harness construction."""

        slot = _CURRENT_PROMPT_RUNTIME.get()
        if slot is None:
            raise RuntimeError("run control is not bound")
        if slot.binding is not None and slot.binding is not binding:
            raise RuntimeError("prompt runtime binding is already installed")
        slot.binding = binding

    @staticmethod
    def prompt_runtime() -> PromptRuntimeBinding | None:
        """Return the per-call F2 binding inherited by every local child."""

        slot = _CURRENT_PROMPT_RUNTIME.get()
        return None if slot is None else slot.binding

    @staticmethod
    def unbind(token: _RunControlContextToken) -> None:
        """Restore the binding that preceded ``token``."""

        _CURRENT_PROMPT_RUNTIME.reset(token.prompt_runtime)
        _CURRENT_TASK_POLICY.reset(token.task_policy)
        _CURRENT_LIFECYCLE_REDUCER.reset(token.lifecycle_reducer)
        _CURRENT_SERIAL_ADMISSION.reset(token.serial_admission)
        _CURRENT_BINDING.reset(token.binding)


__all__ = [
    "RunControlBinding",
    "RunControlContext",
    "RunSerialAdmission",
    "TaskPolicyCapabilityProgress",
    "TaskPolicyControllerPort",
    "TaskPolicyFingerprintPort",
    "TaskPolicyProgressProjection",
    "TaskPolicyRuntimeControllerPort",
    "TaskPolicyRuntimeBinding",
    "RuntimeToolControlOutcome",
    "RuntimeToolControlTerminalRecord",
    "RuntimeToolLifecycleReducer",
]
