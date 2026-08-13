"""Execute a compiled plan, one layer at a time, entirely through the PEP.

The single most important property of this module is what it does **not** hold.
:class:`ToolProgramExecutor` is constructed with a ``PolicyToolInvoker`` and
nothing else that can reach a tool — no tool map, no dispatcher, no connector
client. There is therefore no code path by which a batched step reaches a tool
without passing the same budget/approval/dispatch seam a single interpreter
callback passes, because no such path exists to write. Batching cannot become a
PEP bypass by accident; it would take deleting the constructor to make it one.

Bounds are all enforced here rather than trusted from the plan: step count and
schedulability come from :class:`ToolProgramPlan`, and concurrency, wall clock
and total payload are enforced per layer below.

**Approval-gated steps.** A tool whose own pipeline parks the run for human
approval signals that by raising LangGraph's ``GraphBubbleUp`` from inside
``ainvoke``. A program refuses to be an approval surface: that signal is caught,
the program stops at that step, and the model is told which step needs a direct
call. It is not re-raised, because parking a batch would re-execute every
already-completed step when the graph resumed the tool node — silently doubling
their side effects. Declining is the only disposition that is both honest and
side-effect safe; skipping the step is neither.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping

from langgraph.errors import GraphBubbleUp

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    SnapshotRef,
)
from agent_runtime.capabilities.interpreter.ports import (
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
    PolicyToolInvoker,
)
from agent_runtime.capabilities.tool_program.contracts import (
    RunToolProgramInput,
    StepOutcome,
    StepStatus,
    ToolProgramError,
    ToolProgramErrorCode,
    ToolProgramLimits,
    ToolProgramResult,
)
from agent_runtime.capabilities.tool_program.plan import (
    ReferenceWalker,
    ToolProgramPlan,
)
from agent_runtime.execution.contracts import JsonValue


class _StepAttempt:
    """One step's settled attempt: its outcome, or the control signal it raised."""

    def __init__(
        self,
        *,
        step_id: str,
        outcome: PolicyToolInvocationOutcome | None = None,
        requires_approval: bool = False,
        error: ToolProgramError | None = None,
    ) -> None:
        self.step_id = step_id
        self.outcome = outcome
        self.requires_approval = requires_approval
        self.error = error


class ProgramIdentity:
    """Trusted run identity for one program, resolved from context."""

    def __init__(
        self, *, run_id: str, org_id: str | None = None, user_id: str | None = None
    ) -> None:
        self.run_id = run_id
        self.org_id = org_id
        self.user_id = user_id


class ToolProgramExecutor:
    """Runs a compiled plan and returns only its projection."""

    #: Stamped onto the synthetic snapshot ref below so an audit reader can tell
    #: a program call from a Monty callback at a glance.
    ADAPTER = "tool_program"
    ABI_VERSION = "1"

    def __init__(
        self,
        *,
        invoker: PolicyToolInvoker,
        resolver: object,
        limits: ToolProgramLimits,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._invoker = invoker
        self._resolver = resolver
        self._limits = limits
        self._clock = clock

    async def run(
        self, program: RunToolProgramInput, *, identity: ProgramIdentity
    ) -> ToolProgramResult:
        """Compile, execute, and project ``program`` into one tool result."""

        outcomes: list[StepOutcome] = []
        try:
            plan = ToolProgramPlan.compile(
                program, resolver=self._resolver, limits=self._limits
            )
        except ToolProgramError as error:
            return ToolProgramResult.failure(error=error)
        try:
            outputs = await self._execute(plan, identity=identity, outcomes=outcomes)
            projection = ReferenceWalker.resolve(
                plan.projection, outputs=outputs, owner=None
            )
            self._guard_result_size(projection)
        except ToolProgramError as error:
            self._mark_not_run(plan, outcomes=outcomes)
            return ToolProgramResult.failure(error=error, steps=outcomes)
        return ToolProgramResult(
            status="completed", result=projection, steps=tuple(outcomes)
        )

    # -- execution ---------------------------------------------------------

    async def _execute(
        self,
        plan: ToolProgramPlan,
        *,
        identity: ProgramIdentity,
        outcomes: list[StepOutcome],
    ) -> dict[str, JsonValue]:
        deadline = self._clock() + self._limits.wall_clock_ms / 1000
        digest = self._program_digest(plan)
        semaphore = asyncio.Semaphore(self._limits.max_concurrency)
        outputs: dict[str, JsonValue] = {}
        budget = _PayloadBudget(self._limits.max_total_output_bytes)
        index = 0
        for layer in plan.layers:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise self._expired(layer[0])
            attempts = await self._run_layer(
                layer,
                plan=plan,
                identity=identity,
                outputs=outputs,
                semaphore=semaphore,
                digest=digest,
                first_index=index,
                remaining=remaining,
            )
            index += len(layer)
            for attempt in attempts:
                self._settle(attempt, plan=plan, outcomes=outcomes, outputs=outputs)
            budget.charge(outputs)
        return outputs

    async def _run_layer(
        self,
        layer: tuple[str, ...],
        *,
        plan: ToolProgramPlan,
        identity: ProgramIdentity,
        outputs: Mapping[str, JsonValue],
        semaphore: asyncio.Semaphore,
        digest: str,
        first_index: int,
        remaining: float,
    ) -> tuple[_StepAttempt, ...]:
        """Dispatch one independent layer under the concurrency + time bounds."""

        tasks = [
            self._attempt(
                step_id,
                plan=plan,
                identity=identity,
                outputs=outputs,
                semaphore=semaphore,
                digest=digest,
                invocation_index=first_index + offset,
            )
            for offset, step_id in enumerate(layer)
        ]
        try:
            return tuple(await asyncio.wait_for(asyncio.gather(*tasks), remaining))
        except TimeoutError as expiry:
            raise self._expired(layer[0]) from expiry

    async def _attempt(
        self,
        step_id: str,
        *,
        plan: ToolProgramPlan,
        identity: ProgramIdentity,
        outputs: Mapping[str, JsonValue],
        semaphore: asyncio.Semaphore,
        digest: str,
        invocation_index: int,
    ) -> _StepAttempt:
        step = plan.steps[step_id]
        async with semaphore:
            try:
                arguments = ReferenceWalker.resolve(
                    step.arguments, outputs=outputs, owner=step_id
                )
            except ToolProgramError as error:
                return _StepAttempt(step_id=step_id, error=error)
            call = ExternalFunctionCall(
                interpreter_session_id=digest,
                invocation_index=invocation_index,
                alias=step.tool,
                arguments=dict(arguments),  # type: ignore[arg-type]
                snapshot=SnapshotRef(
                    sha256=digest,
                    # A program has no resumable interpreter state, so there are
                    # no snapshot bytes to address. Zero says that plainly rather
                    # than implying a stored blob exists.
                    size=0,
                    adapter=self.ADAPTER,
                    abi_version=self.ABI_VERSION,
                    source_sha256=digest,
                    limit_profile_hash=digest,
                    invocation_index=invocation_index,
                ),
                source_sha256=digest,
            )
            context = PolicyInvocationContext(
                run_id=identity.run_id,
                # The program id plays the session role: it is what binds every
                # step of one program together for budget and audit.
                interpreter_session_id=digest,
                org_id=identity.org_id,
                user_id=identity.user_id,
                spec=plan.specs[step_id],
            )
            try:
                outcome = await self._invoker.invoke(call=call, context=context)
            except GraphBubbleUp:
                return _StepAttempt(step_id=step_id, requires_approval=True)
            return _StepAttempt(step_id=step_id, outcome=outcome)

    def _settle(
        self,
        attempt: _StepAttempt,
        *,
        plan: ToolProgramPlan,
        outcomes: list[StepOutcome],
        outputs: dict[str, JsonValue],
    ) -> None:
        """Record one settled attempt, raising on the first non-success."""

        step = plan.steps[attempt.step_id]
        if attempt.error is not None:
            outcomes.append(
                StepOutcome(
                    step_id=step.id,
                    tool=step.tool,
                    status=StepStatus.FAILED,
                    error_code=attempt.error.code,
                    safe_message=attempt.error.safe_message,
                )
            )
            raise attempt.error
        if attempt.requires_approval:
            error = ToolProgramError(
                ToolProgramErrorCode.STEP_REQUIRES_APPROVAL,
                f"step '{step.id}' needs human approval, which a program cannot "
                f"carry; call '{step.tool}' directly to review it",
                step_id=step.id,
            )
            outcomes.append(
                StepOutcome(
                    step_id=step.id,
                    tool=step.tool,
                    status=StepStatus.REQUIRES_APPROVAL,
                    error_code=error.code,
                    safe_message=error.safe_message,
                )
            )
            raise error
        outcome = attempt.outcome
        if outcome is None or outcome.status != PolicyToolInvocationOutcome.ALLOWED:
            error = self._blocked(step_id=step.id, outcome=outcome)
            outcomes.append(
                StepOutcome(
                    step_id=step.id,
                    tool=step.tool,
                    status=(
                        StepStatus.DENIED
                        if outcome.status != PolicyToolInvocationOutcome.ERROR
                        else StepStatus.FAILED
                    ),
                    error_code=error.code,
                    safe_message=error.safe_message,
                )
            )
            raise error
        outputs[step.id] = self._record(outcome.return_value)
        outcomes.append(
            StepOutcome(step_id=step.id, tool=step.tool, status=StepStatus.COMPLETED)
        )

    # -- projections and bounds -------------------------------------------

    @staticmethod
    def _record(value: JsonValue) -> JsonValue:
        """Record a step's output in the shape later steps can address.

        Tools in this runtime overwhelmingly return a serialized JSON document
        as a string. Recording that string verbatim would make every structural
        reference into it unresolvable, so a string that parses as a JSON object
        or array is recorded parsed. Anything else is recorded exactly as it
        came back — no coercion, no reshaping.
        """

        if not isinstance(value, str):
            return value
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return value
        return decoded if isinstance(decoded, (dict, list)) else value

    @staticmethod
    def _blocked(
        *, step_id: str, outcome: PolicyToolInvocationOutcome
    ) -> ToolProgramError:
        denied = outcome.status != PolicyToolInvocationOutcome.ERROR
        return ToolProgramError(
            (
                ToolProgramErrorCode.STEP_DENIED
                if denied
                else ToolProgramErrorCode.STEP_FAILED
            ),
            f"step '{step_id}' {'was not permitted' if denied else 'failed'}: "
            f"{outcome.safe_message or outcome.status}",
            step_id=step_id,
        )

    @staticmethod
    def _expired(step_id: str) -> ToolProgramError:
        return ToolProgramError(
            ToolProgramErrorCode.WALL_CLOCK_EXCEEDED,
            f"the program ran out of time before step '{step_id}' settled",
            step_id=step_id,
        )

    def _guard_result_size(self, projection: JsonValue) -> None:
        if _PayloadBudget.size(projection) > self._limits.max_result_bytes:
            raise ToolProgramError(
                ToolProgramErrorCode.PAYLOAD_LIMIT_EXCEEDED,
                "the result projection is larger than "
                f"{self._limits.max_result_bytes} bytes; narrow it with a "
                "reference path",
            )

    @staticmethod
    def _mark_not_run(plan: ToolProgramPlan, *, outcomes: list[StepOutcome]) -> None:
        """Name the steps that never ran, so the model can retry only those."""

        reported = {outcome.step_id for outcome in outcomes}
        for step_id, step in plan.steps.items():
            if step_id not in reported:
                outcomes.append(
                    StepOutcome(
                        step_id=step_id, tool=step.tool, status=StepStatus.NOT_RUN
                    )
                )

    @classmethod
    def _program_digest(cls, plan: ToolProgramPlan) -> str:
        """Stable 64-hex identity for one program, derived from its own plan."""

        canonical = json.dumps(
            [
                [
                    step_id,
                    plan.steps[step_id].tool,
                    sorted(plan.steps[step_id].arguments),
                ]
                for layer in plan.layers
                for step_id in layer
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _PayloadBudget:
    """Accumulates the serialized size of everything the program has pulled in."""

    def __init__(self, ceiling: int) -> None:
        self._ceiling = ceiling

    @staticmethod
    def size(value: JsonValue) -> int:
        try:
            return len(json.dumps(value, default=str).encode("utf-8"))
        except (TypeError, ValueError):  # pragma: no cover - default=str covers it
            return len(repr(value).encode("utf-8"))

    def charge(self, outputs: Mapping[str, JsonValue]) -> None:
        total = sum(self.size(value) for value in outputs.values())
        if total > self._ceiling:
            raise ToolProgramError(
                ToolProgramErrorCode.PAYLOAD_LIMIT_EXCEEDED,
                f"the program's step outputs exceed {self._ceiling} bytes in "
                "total; split it into smaller programs",
            )


__all__ = ("ProgramIdentity", "ToolProgramExecutor")
