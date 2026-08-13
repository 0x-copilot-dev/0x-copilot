"""Execute a compiled plan, one layer at a time, entirely through the PEP.

The single most important property of this module is what it does **not** hold.
:class:`ToolProgramExecutor` is constructed with a
:class:`~agent_runtime.capabilities.tool_program.dispatch.ToolProgramStepDispatcher`
and nothing else that can reach a tool — no tool map, no dispatcher of its own,
no connector client. There is therefore no code path by which a batched step
reaches a tool without passing the graph's own tool seam, because no such path
exists to write. Batching cannot become a PEP bypass by accident; it would take
changing the constructor to make it one.

Bounds are all enforced here rather than trusted from the plan: step count and
schedulability come from :class:`ToolProgramPlan`, and concurrency, wall clock
and total payload are enforced per layer below.

**Approval-gated steps.** A tool whose own pipeline parks the run for human
approval signals that by raising LangGraph's ``GraphBubbleUp`` from inside its
dispatch. A program refuses to be an approval surface: that signal is caught,
the program stops at that step, and the model is told which step needs a direct
call. It is not re-raised, because parking a batch would re-execute every
already-completed step when the graph resumed the tool node — silently doubling
their side effects. Declining is the only disposition that is both honest and
side-effect safe; skipping the step is neither.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping

from langgraph.errors import GraphBubbleUp

from agent_runtime.capabilities.tool_program.contracts import (
    RunToolProgramInput,
    StepOutcome,
    StepStatus,
    ToolProgramError,
    ToolProgramErrorCode,
    ToolProgramLimits,
    ToolProgramResult,
)
from agent_runtime.capabilities.tool_program.dispatch import (
    StepDispatchOutcome,
    StepDispatchStatus,
    ToolProgramStepDispatcher,
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
        outcome: StepDispatchOutcome | None = None,
        requires_approval: bool = False,
        error: ToolProgramError | None = None,
    ) -> None:
        self.step_id = step_id
        self.outcome = outcome
        self.requires_approval = requires_approval
        self.error = error


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


class ToolProgramExecutor:
    """Runs a compiled plan and returns only its projection."""

    def __init__(
        self,
        *,
        dispatcher: ToolProgramStepDispatcher,
        authorized_tool_names: frozenset[str],
        limits: ToolProgramLimits,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dispatcher = dispatcher
        self._authorized_tool_names = authorized_tool_names
        self._limits = limits
        self._clock = clock

    async def run(self, program: RunToolProgramInput) -> ToolProgramResult:
        """Compile, execute, and project ``program`` into one tool result."""

        outcomes: list[StepOutcome] = []
        try:
            plan = ToolProgramPlan.compile(
                program,
                authorized_tool_names=self._authorized_tool_names,
                limits=self._limits,
            )
        except ToolProgramError as error:
            return ToolProgramResult.failure(error=error)
        try:
            outputs = await self._execute(plan, outcomes=outcomes)
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
        outcomes: list[StepOutcome],
    ) -> dict[str, JsonValue]:
        deadline = self._clock() + self._limits.wall_clock_ms / 1000
        semaphore = asyncio.Semaphore(self._limits.max_concurrency)
        outputs: dict[str, JsonValue] = {}
        budget = _PayloadBudget(self._limits.max_total_output_bytes)
        for layer in plan.layers:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise self._expired(layer[0])
            attempts = await self._run_layer(
                layer,
                plan=plan,
                outputs=outputs,
                semaphore=semaphore,
                remaining=remaining,
            )
            for attempt in attempts:
                self._settle(attempt, plan=plan, outcomes=outcomes, outputs=outputs)
            budget.charge(outputs)
        return outputs

    async def _run_layer(
        self,
        layer: tuple[str, ...],
        *,
        plan: ToolProgramPlan,
        outputs: Mapping[str, JsonValue],
        semaphore: asyncio.Semaphore,
        remaining: float,
    ) -> tuple[_StepAttempt, ...]:
        """Dispatch one independent layer under the concurrency + time bounds."""

        tasks = [
            self._attempt(step_id, plan=plan, outputs=outputs, semaphore=semaphore)
            for step_id in layer
        ]
        try:
            settled = await asyncio.wait_for(
                # ``return_exceptions`` is what keeps a fatal error from
                # orphaning this layer's other steps: gather would otherwise
                # propagate the first one and leave its siblings running with
                # nobody awaiting them, mid-side-effect.
                asyncio.gather(*tasks, return_exceptions=True),
                remaining,
            )
        except TimeoutError as expiry:
            raise self._expired(layer[0]) from expiry
        for item in settled:
            if isinstance(item, BaseException):
                raise item
        return tuple(settled)  # type: ignore[arg-type]

    async def _attempt(
        self,
        step_id: str,
        *,
        plan: ToolProgramPlan,
        outputs: Mapping[str, JsonValue],
        semaphore: asyncio.Semaphore,
    ) -> _StepAttempt:
        step = plan.steps[step_id]
        async with semaphore:
            try:
                arguments = ReferenceWalker.resolve(
                    step.arguments, outputs=outputs, owner=step_id
                )
            except ToolProgramError as error:
                return _StepAttempt(step_id=step_id, error=error)
            try:
                outcome = await self._dispatcher.dispatch(
                    step_id=step_id,
                    tool_name=step.tool,
                    arguments=dict(arguments),  # type: ignore[arg-type]
                )
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
            self._record(
                outcomes,
                step_id=step.id,
                tool=step.tool,
                status=StepStatus.FAILED,
                error=attempt.error,
            )
            raise attempt.error
        if attempt.requires_approval:
            error = ToolProgramError(
                ToolProgramErrorCode.STEP_REQUIRES_APPROVAL,
                f"step '{step.id}' needs human approval, which a program cannot "
                f"carry; call '{step.tool}' directly to review it",
                step_id=step.id,
            )
            self._record(
                outcomes,
                step_id=step.id,
                tool=step.tool,
                status=StepStatus.REQUIRES_APPROVAL,
                error=error,
            )
            raise error
        outcome = attempt.outcome
        if outcome is None or outcome.status is not StepDispatchStatus.COMPLETED:
            error = self._blocked(step_id=step.id, outcome=outcome)
            self._record(
                outcomes,
                step_id=step.id,
                tool=step.tool,
                status=(
                    StepStatus.FAILED
                    if outcome is not None
                    and outcome.status is StepDispatchStatus.FAILED
                    else StepStatus.DENIED
                ),
                error=error,
            )
            raise error
        outputs[step.id] = outcome.output
        outcomes.append(
            StepOutcome(step_id=step.id, tool=step.tool, status=StepStatus.COMPLETED)
        )

    # -- projections and bounds -------------------------------------------

    @staticmethod
    def _record(
        outcomes: list[StepOutcome],
        *,
        step_id: str,
        tool: str,
        status: StepStatus,
        error: ToolProgramError,
    ) -> None:
        """Append one non-success outcome that names itself and its reason."""

        outcomes.append(
            StepOutcome(
                step_id=step_id,
                tool=tool,
                status=status,
                error_code=error.code,
                safe_message=error.safe_message,
            )
        )

    @staticmethod
    def _blocked(
        *, step_id: str, outcome: StepDispatchOutcome | None
    ) -> ToolProgramError:
        failed = outcome is not None and outcome.status is StepDispatchStatus.FAILED
        reason = (outcome.safe_message if outcome is not None else None) or "no reason"
        return ToolProgramError(
            (
                ToolProgramErrorCode.STEP_FAILED
                if failed
                else ToolProgramErrorCode.STEP_DENIED
            ),
            f"step '{step_id}' {'failed' if failed else 'was not permitted'}: {reason}",
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


__all__ = ("ToolProgramExecutor",)
