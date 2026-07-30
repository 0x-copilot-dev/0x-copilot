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

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

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
from agent_runtime.control_plane.model_reliability import (
    ModelReliabilityControl,
    ModelReliabilityReleaseDecision,
    ModelReliabilityReleaseResolver,
)
from agent_runtime.control_plane.parallel_admission import (
    ParallelAdmissionBounds,
    ParallelAdmissionGrant,
    ParallelAdmissionPort,
    ParallelAdmissionResolver,
    ToolAdmissionRequest,
)
from agent_runtime.execution.contracts import RuntimeContract

if TYPE_CHECKING:
    from agent_runtime.observability.context_occupancy_binding import (
        ContextOccupancyRuntimeBinding,
    )
    from agent_runtime.execution.model_invocation.runtime import (
        ModelInvocationRuntimeBinding,
    )
    from agent_runtime.prompts.runtime_binding import PromptRuntimeBinding


class RunControlBinding(RuntimeContract):
    """Immutable snapshot plus its authority-narrowed effective feature modes."""

    snapshot: RunControlSnapshot
    effective_modes: FeatureModeSet
    decisions: tuple[FeatureModeDecision, ...]
    model_reliability: ModelReliabilityReleaseDecision

    @model_validator(mode="before")
    @classmethod
    def _default_model_reliability_off(
        cls,
        value: object,
    ) -> object:
        if not isinstance(value, Mapping) or value.get("model_reliability") is not None:
            return value
        snapshot = RunControlSnapshot.model_validate(value.get("snapshot"))
        effective_modes = FeatureModeSet.model_validate(value.get("effective_modes"))
        normalized = dict(value)
        normalized["model_reliability"] = ModelReliabilityReleaseResolver().resolve(
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
            snapshot_digest=snapshot.snapshot_digest,
            snapshot=snapshot.model_reliability_controls,
            snapshot_f10_mode=snapshot.feature_modes.f10,
            effective_f10_mode=effective_modes.f10,
        )
        return normalized

    @model_validator(mode="after")
    def _model_reliability_matches_snapshot(self) -> "RunControlBinding":
        release = self.model_reliability
        if (
            release.run_id != self.snapshot.run_id
            or release.snapshot_id != self.snapshot.snapshot_id
            or release.snapshot_digest != self.snapshot.snapshot_digest
        ):
            raise ValueError(
                "model reliability decision does not match the run snapshot"
            )
        if release.effective_f10_mode is not self.effective_modes.f10:
            raise ValueError(
                "model reliability decision does not match effective F10 mode"
            )
        for control in ModelReliabilityControl:
            if release.for_control(control).snapshot_mode is not (
                self.snapshot.model_reliability_controls.mode_for(control)
            ):
                raise ValueError(
                    "model reliability decision does not match snapshot subcontrols"
                )
        return self

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


@dataclass(slots=True)
class _ModelInvocationRuntimeSlot:
    """Run-lifetime F10 slot inherited by supervisor and local child tasks."""

    binding: ModelInvocationRuntimeBinding | None = None


@dataclass(slots=True)
class _ContextOccupancyRuntimeSlot:
    """Run-lifetime occupancy slot, deliberately separate from the F10 one.

    Occupancy is measured at the F10 middleware but is not an F10 concern, and
    conflating the two is what made the Context Occupancy Ledger inert on every
    default deployment: the sink lived on the F10 binding, and that binding does
    not exist while ``f10`` is ``OFF`` — its default. A slot of its own is what
    lets measurement be installed by a run whose feature modes are all closed.
    """

    binding: ContextOccupancyRuntimeBinding | None = None


_CURRENT_PROMPT_RUNTIME: ContextVar[_PromptRuntimeSlot | None] = ContextVar(
    "agent_runtime_prompt_runtime_slot",
    default=None,
)
_CURRENT_MODEL_INVOCATION_RUNTIME: ContextVar[_ModelInvocationRuntimeSlot | None] = (
    ContextVar("agent_runtime_model_invocation_runtime_slot", default=None)
)
_CURRENT_CONTEXT_OCCUPANCY: ContextVar[_ContextOccupancyRuntimeSlot | None] = (
    ContextVar(
        "agent_runtime_context_occupancy_slot",
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


@dataclass(slots=True)
class _CohortGate:
    """The bounded slot table one grant cohort's calls share while it is live.

    ``references`` counts waiters *and* holders, so a cohort is only evicted
    once nobody is queued behind it. ``max_parallelism`` is fixed for the life
    of one generation: a semaphore cannot be safely narrowed after coroutines
    are already queued on it, so a grant that disagrees with a live cohort's
    width is answered serially rather than by reshaping the gate underneath it.
    """

    semaphore: asyncio.Semaphore
    max_parallelism: int
    references: int = 0


class RunSerialAdmission:
    """The run's one tool-call gate: serial by default, F6-widenable by grant.

    Step 2 installed this as an unconditional exclusive lock around every
    graph-visible tool call, which is what makes the §8 middleware ordering
    enforceable for framework-injected and delegated tools alike. It also made
    the effective width of every run ``1``, so no F6 batch plan could ever be
    observed to overlap.

    This class keeps the gate and makes the *width* conditional. Two lanes cross
    it, and neither one is a way around it:

    - **Exclusive** — the Step-2 behaviour, byte-for-byte. Taken by every call
      without a grant, which is every call while F6 is dark.
    - **Shared** — taken only by a call an installed
      :class:`~agent_runtime.control_plane.parallel_admission.ParallelAdmissionPort`
      positively named. Bounded twice: by the grant's own cohort semaphore, and
      by the shared lane itself. A grant of width *one* is a shared-lane call
      too, and a narrow one: it waits behind its own cohort instead of behind
      every other call in the run, which is what lets a grantor with its own
      ordering barrier make progress at all. When the cohort table cannot hold a
      cohort, :meth:`_unrepresentable_cohort_lane` says which of the two lanes
      answers.

    The two lanes are mutually exclusive, which is the point. The shared lane is
    a *lightswitch*: the first entrant takes the same exclusive lock a serial
    call would, and the last one out releases it. So a serial call still cannot
    overlap anything — it waits behind the whole parallel group — and a parallel
    group still cannot start while a serial call is running. Overlap is
    something this gate *grants*; it is never a path that skips it.

    Ordering is cohort-then-shared for every shared caller, and exclusive
    callers take only the exclusive lock, so the two lanes cannot form a cycle.
    The leave path never awaits, so a cancellation between the last release and
    the counter update cannot strand the run's lock.
    """

    def __init__(
        self,
        *,
        parallel_admission: ParallelAdmissionPort | None = None,
    ) -> None:
        self._async_lock = asyncio.Lock()
        self._sync_lock = Lock()
        self._parallel_admission = parallel_admission
        self._shared_gate = asyncio.Lock()
        self._shared_holders = 0
        self._cohorts: dict[str, _CohortGate] = {}

    def install_parallel_admission(self, port: ParallelAdmissionPort) -> None:
        """Install the one F6 source that may widen this run's admission.

        Install-once, matching the F2/F10 runtime slots: a second, different
        source would mean two authorities disagreeing about which calls may
        overlap, and the gate would have no principled way to choose.
        """

        if (
            self._parallel_admission is not None
            and self._parallel_admission is not port
        ):
            raise RuntimeError("parallel admission is already installed")
        self._parallel_admission = port

    @asynccontextmanager
    async def async_permit(
        self,
        request: ToolAdmissionRequest | None = None,
    ) -> AsyncIterator[None]:
        """Admit one async tool call, serially unless F6 granted otherwise.

        ``request`` is omitted by callers that have nothing to identify, and an
        omitted request can never match a grant — so the no-argument call is the
        pre-F6 exclusive permit exactly.
        """

        grant = ParallelAdmissionResolver.resolve(self._parallel_admission, request)
        if grant is None:
            async with self._async_lock:
                yield
            return
        cohort = self._bind_cohort(grant)
        if cohort is None:
            async with self._unrepresentable_cohort_lane(grant):
                yield
            return
        try:
            async with cohort.semaphore:
                await self._join_shared()
                try:
                    yield
                finally:
                    self._leave_shared()
        finally:
            self._release_cohort(grant, cohort)

    @asynccontextmanager
    async def _unrepresentable_cohort_lane(
        self,
        grant: ParallelAdmissionGrant,
    ) -> AsyncIterator[None]:
        """Admit a granted call whose cohort this gate cannot currently hold.

        Two things can make a cohort unrepresentable: the bounded cohort table is
        full, or a live cohort already fixed a different width. Either way this
        gate has no slot through which to apply the grant's width, and it has
        exactly two ways to answer.

        The exclusive lock is the fail-closed answer and stays the default,
        because a grantor that does not enforce its own width has no other
        enforcer and admitting it unbounded would widen the run.

        It is also, on its own, a way to *stall* a grantor that orders its calls
        behind a barrier: a member waiting for an earlier sibling would hold this
        lock while that sibling waits for it. So a grantor that positively states
        it enforces the width itself is admitted into the shared lane without a
        slot. Nothing is unbounded there — the shared lane still takes the run's
        exclusive lock on the group's behalf, so granted work still cannot
        overlap ungranted work, and the grantor's own gate still decides how many
        of its members run at once.
        """

        if not grant.width_enforced_by_grantor:
            async with self._async_lock:
                yield
            return
        await self._join_shared()
        try:
            yield
        finally:
            self._leave_shared()

    @contextmanager
    def sync_permit(self) -> Iterator[None]:
        """Admit one synchronous tool call for this run, always exclusively.

        The sync path is never widened. F6 drives children through an awaitable
        ``BatchChildRunner`` and gates them on ``asyncio`` futures, so a
        synchronous graph tool call is never a batch child and a grant for one
        could not be honestly produced. Keeping this lane exclusive means the
        widening story needs no threading lightswitch for work that cannot
        arrive on it.
        """

        with self._sync_lock:
            yield

    def _bind_cohort(self, grant: ParallelAdmissionGrant) -> _CohortGate | None:
        """Reserve a reference on this grant's cohort, or refuse to widen.

        Synchronous and await-free, so the check and the reservation are atomic
        against every other coroutine on the loop. ``None`` means this gate holds
        no slot for the grant — the cohort table is full, or a live cohort
        already fixed a different width — and
        :meth:`_unrepresentable_cohort_lane` decides what a call with no slot is
        answered by.
        """

        cohort = self._cohorts.get(grant.cohort_id)
        if cohort is None:
            if len(self._cohorts) >= ParallelAdmissionBounds.MAX_TRACKED_COHORTS:
                return None
            cohort = _CohortGate(
                semaphore=asyncio.Semaphore(grant.max_parallelism),
                max_parallelism=grant.max_parallelism,
            )
            self._cohorts[grant.cohort_id] = cohort
        elif cohort.max_parallelism != grant.max_parallelism:
            return None
        cohort.references += 1
        return cohort

    def _release_cohort(
        self,
        grant: ParallelAdmissionGrant,
        cohort: _CohortGate,
    ) -> None:
        """Drop one reference and evict the cohort once nobody is queued."""

        cohort.references -= 1
        if cohort.references <= 0 and self._cohorts.get(grant.cohort_id) is cohort:
            del self._cohorts[grant.cohort_id]

    async def _join_shared(self) -> None:
        """Take the run's exclusive lock on behalf of the whole shared group.

        The first member to arrive takes exactly the lock a serial call takes,
        and holds it until the last member leaves. That is what keeps a serial
        call from overlapping a parallel group in either direction, and it is
        why "may overlap" never means "skipped the gate".

        The turnstile serializes the count-zero acquisition so two first-movers
        cannot both block on the exclusive lock and deadlock each other behind
        it. Returning normally is the caller's signal — and the only signal —
        that :meth:`_leave_shared` is owed.
        """

        async with self._shared_gate:
            if self._shared_holders == 0:
                await self._async_lock.acquire()
            self._shared_holders += 1

    def _leave_shared(self) -> None:
        """Release the group's hold once its last member is done.

        Deliberately await-free. A leave that could suspend could also be
        cancelled between the decrement and the release, stranding the run's
        exclusive lock for the life of the run; single-threaded loop semantics
        make an await-free body atomic against every other coroutine.
        """

        self._shared_holders -= 1
        if self._shared_holders == 0:
            self._async_lock.release()


@dataclass(frozen=True)
class _RunControlContextToken:
    binding: Token[RunControlBinding | None]
    serial_admission: Token[RunSerialAdmission | None]
    lifecycle_reducer: Token[RuntimeToolLifecycleReducer | None]
    task_policy: Token[TaskPolicyRuntimeBinding | None]
    prompt_runtime: Token[_PromptRuntimeSlot | None]
    model_invocation_runtime: Token[_ModelInvocationRuntimeSlot | None]
    context_occupancy: Token[_ContextOccupancyRuntimeSlot | None]


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
            model_invocation_runtime=_CURRENT_MODEL_INVOCATION_RUNTIME.set(
                _ModelInvocationRuntimeSlot()
            ),
            context_occupancy=_CURRENT_CONTEXT_OCCUPANCY.set(
                _ContextOccupancyRuntimeSlot()
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
    def install_parallel_admission(port: ParallelAdmissionPort) -> None:
        """Install the one F6 source that may widen this run's tool admission.

        Root wires this after ``bind_for_run`` because F6's coordinator is built
        per run, the same way the F2 prompt runtime and the F10 invocation
        runtime are installed into their slots. Until it is called — which is
        every run while F6 is dark — the admission is exactly the Step-2 serial
        permit.
        """

        admission = _CURRENT_SERIAL_ADMISSION.get()
        if admission is None:
            raise RuntimeError("run control is not bound")
        admission.install_parallel_admission(port)

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
    def install_model_invocation_runtime(
        binding: ModelInvocationRuntimeBinding,
    ) -> None:
        """Install one run-scoped F10 authority/journal binding."""

        slot = _CURRENT_MODEL_INVOCATION_RUNTIME.get()
        if slot is None:
            raise RuntimeError("run control is not bound")
        if slot.binding is not None and slot.binding is not binding:
            raise RuntimeError("model invocation runtime binding is already installed")
        slot.binding = binding

    @staticmethod
    def model_invocation_runtime() -> ModelInvocationRuntimeBinding | None:
        """Return the F10 binding inherited by supervisor and local children."""

        slot = _CURRENT_MODEL_INVOCATION_RUNTIME.get()
        return None if slot is None else slot.binding

    @staticmethod
    def install_context_occupancy_runtime(
        binding: ContextOccupancyRuntimeBinding,
    ) -> None:
        """Install one run-scoped occupancy sink, whatever the feature modes say.

        Deliberately takes no feature mode and consults none: the whole defect
        this repairs was measurement reachable only through a binding that a
        closed feature mode suppresses. The only precondition is a bound run,
        because a snapshot has to name the run and conversation it describes.
        """

        slot = _CURRENT_CONTEXT_OCCUPANCY.get()
        if slot is None:
            raise RuntimeError("run control is not bound")
        if slot.binding is not None and slot.binding is not binding:
            raise RuntimeError("context occupancy binding is already installed")
        slot.binding = binding

    @staticmethod
    def context_occupancy_runtime() -> ContextOccupancyRuntimeBinding | None:
        """Return the occupancy sink inherited by supervisor and local children."""

        slot = _CURRENT_CONTEXT_OCCUPANCY.get()
        return None if slot is None else slot.binding

    @staticmethod
    def unbind(token: _RunControlContextToken) -> None:
        """Restore the binding that preceded ``token``."""

        _CURRENT_CONTEXT_OCCUPANCY.reset(token.context_occupancy)
        _CURRENT_MODEL_INVOCATION_RUNTIME.reset(token.model_invocation_runtime)
        _CURRENT_PROMPT_RUNTIME.reset(token.prompt_runtime)
        _CURRENT_TASK_POLICY.reset(token.task_policy)
        _CURRENT_LIFECYCLE_REDUCER.reset(token.lifecycle_reducer)
        _CURRENT_SERIAL_ADMISSION.reset(token.serial_admission)
        _CURRENT_BINDING.reset(token.binding)


__all__ = [
    "ParallelAdmissionBounds",
    "ParallelAdmissionGrant",
    "ParallelAdmissionPort",
    "ParallelAdmissionResolver",
    "RunControlBinding",
    "RunControlContext",
    "RunSerialAdmission",
    "ToolAdmissionRequest",
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
