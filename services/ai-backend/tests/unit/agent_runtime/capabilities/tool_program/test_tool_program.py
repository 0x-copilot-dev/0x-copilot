"""Sequencing, structural references, bounds, and what comes back.

The enforcement claim lives in ``test_step_policy_enforcement.py``. This file
covers the other half: that the plan is scheduled correctly, that a data
reference is a *structure* and never an interpolation, that every bound is
actually enforced rather than declared, and that a step which cannot proceed
names itself instead of collapsing into "the batch failed".

The dispatcher here is a recording double, because these properties are about
the executor's own decisions rather than about the seam it dispatches through.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphInterrupt

from agent_runtime.capabilities.tool_program import (
    RunToolProgramInput,
    StepDispatchOutcome,
    StepDispatchStatus,
    StepRef,
    StepStatus,
    ToolProgramError,
    ToolProgramErrorCode,
    ToolProgramExecutor,
    ToolProgramLimits,
    ToolProgramToolFactory,
)
from agent_runtime.capabilities.tool_program.plan import ToolProgramPlan


class RecordingDispatcher:
    """Records every step it is asked for and answers from a script.

    ``responses`` maps a tool name to what it returns; ``interrupts`` names
    tools that raise the runtime's approval signal; ``delays`` makes a tool take
    long enough for concurrency and wall-clock claims to be observable.
    """

    def __init__(
        self,
        *,
        responses: dict[str, object] | None = None,
        interrupts: frozenset[str] = frozenset(),
        refusals: dict[str, str] | None = None,
        delays: dict[str, float] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.interrupts = interrupts
        self.refusals = refusals or {}
        self.delays = delays or {}
        self.calls: list[tuple[str, str, dict]] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def dispatch(
        self, *, step_id: str, tool_name: str, arguments
    ) -> StepDispatchOutcome:
        self.calls.append((step_id, tool_name, dict(arguments)))
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if tool_name in self.interrupts:
                raise GraphInterrupt(())
            delay = self.delays.get(tool_name)
            if delay:
                await asyncio.sleep(delay)
            else:
                # Yield unconditionally so independent steps really interleave;
                # a concurrency claim proved by a coroutine that never awaits
                # would be proving nothing.
                await asyncio.sleep(0)
            if tool_name in self.refusals:
                return StepDispatchOutcome(
                    status=StepDispatchStatus.REFUSED,
                    safe_message=self.refusals[tool_name],
                )
            return StepDispatchOutcome(
                status=StepDispatchStatus.COMPLETED,
                output=self.responses.get(tool_name, {"tool": tool_name}),
            )
        finally:
            self.in_flight -= 1

    @property
    def step_ids(self) -> list[str]:
        return [step_id for step_id, _, _ in self.calls]


class ProgramMixin:
    """Executor construction plus the plan shapes the tests reuse."""

    def limits(self, **overrides) -> ToolProgramLimits:
        values: dict[str, int] = {
            "max_steps": 8,
            "max_concurrency": 4,
            "wall_clock_ms": 5_000,
            "max_total_output_bytes": 1_000_000,
            "max_result_bytes": 500_000,
        }
        values.update(overrides)
        return ToolProgramLimits(**values)

    def executor(
        self,
        dispatcher: RecordingDispatcher,
        *,
        tools: set[str],
        clock=None,
        **overrides,
    ) -> ToolProgramExecutor:
        arguments = {
            "dispatcher": dispatcher,
            "authorized_tool_names": frozenset(tools),
            "limits": self.limits(**overrides),
        }
        if clock is not None:
            arguments["clock"] = clock
        return ToolProgramExecutor(**arguments)  # type: ignore[arg-type]

    @staticmethod
    def program(**payload) -> RunToolProgramInput:
        return RunToolProgramInput.model_validate(payload)


class TestSequencingAndDependencies(ProgramMixin):
    """A reference is an edge; ``depends_on`` is an edge with no data."""

    async def test_a_referenced_step_runs_before_the_step_that_reads_it(self) -> None:
        dispatcher = RecordingDispatcher(
            responses={"list": {"items": [{"id": "i-1"}, {"id": "i-2"}]}}
        )

        result = await self.executor(dispatcher, tools={"list", "fetch"}).run(
            self.program(
                steps=[
                    {
                        "id": "detail",
                        "tool": "fetch",
                        "arguments": {
                            "id": {"$from": "issues", "path": ["items", 1, "id"]}
                        },
                    },
                    {"id": "issues", "tool": "list", "arguments": {}},
                ],
                result={"$from": "detail"},
            )
        )

        assert result.status == "completed"
        # Declaration order is irrelevant; the reference decides the schedule.
        assert dispatcher.step_ids == ["issues", "detail"]
        assert dispatcher.calls[1][2] == {"id": "i-2"}

    async def test_depends_on_orders_two_steps_that_share_no_data(self) -> None:
        dispatcher = RecordingDispatcher()

        result = await self.executor(dispatcher, tools={"a", "b"}).run(
            self.program(
                steps=[
                    {"id": "second", "tool": "b", "depends_on": ["first"]},
                    {"id": "first", "tool": "a"},
                ],
                result={"$from": "second"},
            )
        )

        assert result.status == "completed"
        assert dispatcher.step_ids == ["first", "second"]

    async def test_a_reference_is_substituted_structurally_not_interpolated(
        self,
    ) -> None:
        """The whole referenced value arrives intact, at its own position."""

        payload = {"nested": {"rows": [1, 2, 3]}, "flag": True}
        dispatcher = RecordingDispatcher(responses={"source": payload})

        await self.executor(dispatcher, tools={"source", "sink"}).run(
            self.program(
                steps=[
                    {"id": "src", "tool": "source"},
                    {
                        "id": "dst",
                        "tool": "sink",
                        "arguments": {
                            "whole": {"$from": "src"},
                            "deep": {"$from": "src", "path": ["nested", "rows", -1]},
                            "wrapped": [{"$from": "src", "path": ["flag"]}],
                            "literal": "{$from: src}",
                        },
                        "depends_on": ["src"],
                    },
                ],
                result=None,
            )
        )

        assert dispatcher.calls[1][2] == {
            "whole": payload,
            "deep": 3,
            "wrapped": [True],
            # A string that merely looks like a marker is a string. There is no
            # template engine here to mistake it for one.
            "literal": "{$from: src}",
        }

    def test_a_malformed_reference_marker_is_rejected_not_passed_through(self) -> None:
        """A mapping carrying ``$from`` is always a reference, never a payload."""

        with pytest.raises(ToolProgramError) as raised:
            StepRef.parse({"$from": "a", "path": "items"})

        assert raised.value.code is ToolProgramErrorCode.INVALID_PLAN
        assert "path" in raised.value.safe_message


class TestBoundedConcurrency(ProgramMixin):
    """Independent steps overlap; the overlap is capped."""

    async def test_independent_steps_run_concurrently(self) -> None:
        dispatcher = RecordingDispatcher(delays={"slow": 0.02})

        result = await self.executor(dispatcher, tools={"slow"}, max_concurrency=4).run(
            self.program(
                steps=[{"id": f"s{index}", "tool": "slow"} for index in range(4)],
                result={"$from": "s0"},
            )
        )

        assert result.status == "completed"
        assert dispatcher.peak_in_flight == 4

    async def test_concurrency_never_exceeds_the_configured_ceiling(self) -> None:
        dispatcher = RecordingDispatcher(delays={"slow": 0.01})

        result = await self.executor(dispatcher, tools={"slow"}, max_concurrency=2).run(
            self.program(
                steps=[{"id": f"s{index}", "tool": "slow"} for index in range(6)],
                result={"$from": "s0"},
            )
        )

        assert result.status == "completed"
        assert len(dispatcher.calls) == 6
        assert dispatcher.peak_in_flight == 2, (
            f"{dispatcher.peak_in_flight} steps overlapped against a ceiling of 2"
        )

    async def test_a_later_layer_never_starts_before_the_earlier_one_settles(
        self,
    ) -> None:
        dispatcher = RecordingDispatcher(delays={"slow": 0.01})

        await self.executor(dispatcher, tools={"slow"}, max_concurrency=4).run(
            self.program(
                steps=[
                    {"id": "a", "tool": "slow"},
                    {"id": "b", "tool": "slow"},
                    {"id": "c", "tool": "slow", "depends_on": ["a", "b"]},
                ],
                result={"$from": "c"},
            )
        )

        assert dispatcher.step_ids[-1] == "c"


class TestBoundsAreEnforcedNotDeclared(ProgramMixin):
    """Every ceiling in the hyperparameter section refuses something."""

    async def test_a_plan_over_the_step_ceiling_is_refused_before_any_step(
        self,
    ) -> None:
        dispatcher = RecordingDispatcher()

        result = await self.executor(dispatcher, tools={"a"}, max_steps=2).run(
            self.program(
                steps=[{"id": f"s{index}", "tool": "a"} for index in range(3)],
                result=None,
            )
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.STEP_LIMIT_EXCEEDED
        assert dispatcher.calls == []

    async def test_the_wall_clock_stops_the_program_and_names_the_step(self) -> None:
        dispatcher = RecordingDispatcher(delays={"slow": 0.05})

        result = await self.executor(
            dispatcher, tools={"slow"}, wall_clock_ms=10, max_concurrency=1
        ).run(
            self.program(
                steps=[
                    {"id": "first", "tool": "slow"},
                    {"id": "second", "tool": "slow", "depends_on": ["first"]},
                ],
                result={"$from": "second"},
            )
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.WALL_CLOCK_EXCEEDED
        assert result.failed_step == "first"

    async def test_total_step_output_is_capped_across_the_whole_program(self) -> None:
        dispatcher = RecordingDispatcher(responses={"bulk": {"body": "x" * 4_000}})

        result = await self.executor(
            dispatcher, tools={"bulk"}, max_total_output_bytes=2_048
        ).run(
            self.program(
                steps=[{"id": "big", "tool": "bulk"}],
                result={"$from": "big"},
            )
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.PAYLOAD_LIMIT_EXCEEDED

    async def test_an_oversized_projection_is_refused_with_advice(self) -> None:
        dispatcher = RecordingDispatcher(responses={"bulk": {"body": "x" * 4_000}})

        result = await self.executor(
            dispatcher, tools={"bulk"}, max_result_bytes=512
        ).run(
            self.program(
                steps=[{"id": "big", "tool": "bulk"}],
                result={"$from": "big"},
            )
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.PAYLOAD_LIMIT_EXCEEDED
        assert "reference path" in (result.safe_message or "")


class TestFailuresNameThemselves(ProgramMixin):
    """ "The batch failed" is not an acceptable answer for any of these."""

    async def test_a_refused_step_names_itself_and_the_rest_are_not_run(self) -> None:
        dispatcher = RecordingDispatcher(refusals={"blocked": "policy says no"})

        result = await self.executor(dispatcher, tools={"ok", "blocked"}).run(
            self.program(
                steps=[
                    {"id": "one", "tool": "ok"},
                    {"id": "two", "tool": "blocked", "depends_on": ["one"]},
                    {"id": "three", "tool": "ok", "depends_on": ["two"]},
                ],
                result={"$from": "three"},
            )
        )

        assert result.status == "failed"
        assert result.failed_step == "two"
        assert "policy says no" in (result.safe_message or "")
        assert {outcome.step_id: outcome.status for outcome in result.steps} == {
            "one": StepStatus.COMPLETED,
            "two": StepStatus.DENIED,
            "three": StepStatus.NOT_RUN,
        }

    async def test_an_approval_gated_step_parks_nothing_and_asks_for_a_direct_call(
        self,
    ) -> None:
        """The decision the code has to state: decline, never silently skip."""

        dispatcher = RecordingDispatcher(interrupts=frozenset({"needs_review"}))

        result = await self.executor(dispatcher, tools={"ok", "needs_review"}).run(
            self.program(
                steps=[
                    {"id": "read", "tool": "ok"},
                    {"id": "write", "tool": "needs_review", "depends_on": ["read"]},
                    {"id": "after", "tool": "ok", "depends_on": ["write"]},
                ],
                result={"$from": "after"},
            )
        )

        assert result.status == "failed"
        assert result.failed_step == "write"
        assert result.error_code is ToolProgramErrorCode.STEP_REQUIRES_APPROVAL
        assert "directly" in (result.safe_message or "")
        statuses = {outcome.step_id: outcome.status for outcome in result.steps}
        assert statuses["write"] is StepStatus.REQUIRES_APPROVAL
        # Not skipped: the step after it did not run either.
        assert statuses["after"] is StepStatus.NOT_RUN

    async def test_a_reference_into_a_shape_that_is_not_there_names_the_reader(
        self,
    ) -> None:
        dispatcher = RecordingDispatcher(responses={"list": {"items": []}})

        result = await self.executor(dispatcher, tools={"list", "fetch"}).run(
            self.program(
                steps=[
                    {"id": "src", "tool": "list"},
                    {
                        "id": "reader",
                        "tool": "fetch",
                        "arguments": {"id": {"$from": "src", "path": ["items", 0]}},
                    },
                ],
                result=None,
            )
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.REFERENCE_UNRESOLVED
        assert result.failed_step == "reader"

    def test_a_dependency_cycle_is_refused_at_compile_time(self) -> None:
        with pytest.raises(ToolProgramError) as raised:
            ToolProgramPlan.compile(
                self.program(
                    steps=[
                        {"id": "a", "tool": "t", "depends_on": ["b"]},
                        {"id": "b", "tool": "t", "depends_on": ["a"]},
                    ],
                    result=None,
                ),
                authorized_tool_names=frozenset({"t"}),
                limits=self.limits(),
            )

        assert raised.value.code is ToolProgramErrorCode.CYCLIC_DEPENDENCY

    def test_a_projection_referencing_an_unknown_step_is_refused(self) -> None:
        with pytest.raises(ToolProgramError) as raised:
            ToolProgramPlan.compile(
                self.program(
                    steps=[{"id": "a", "tool": "t"}],
                    result={"$from": "ghost"},
                ),
                authorized_tool_names=frozenset({"t"}),
                limits=self.limits(),
            )

        assert raised.value.code is ToolProgramErrorCode.UNKNOWN_STEP_REFERENCE


class TestOnlyTheProjectionComesBack(ProgramMixin):
    """The entire justification for the tool: intermediates stay out."""

    async def test_intermediate_step_output_is_absent_from_the_result(self) -> None:
        secret = "intermediate-payload-that-must-not-be-returned"
        dispatcher = RecordingDispatcher(
            responses={
                "list": {"items": [{"id": "keep", "body": secret}]},
                "fetch": {"title": "kept"},
            }
        )

        result = await self.executor(dispatcher, tools={"list", "fetch"}).run(
            self.program(
                steps=[
                    {"id": "src", "tool": "list"},
                    {
                        "id": "one",
                        "tool": "fetch",
                        "arguments": {
                            "id": {"$from": "src", "path": ["items", 0, "id"]}
                        },
                    },
                ],
                result={"title": {"$from": "one", "path": ["title"]}},
            )
        )

        assert result.status == "completed"
        assert result.result == {"title": "kept"}
        assert secret not in json.dumps(result.model_dump(mode="json"))

    async def test_the_step_ledger_reports_every_step_without_its_output(self) -> None:
        dispatcher = RecordingDispatcher(responses={"a": {"body": "payload"}})

        result = await self.executor(dispatcher, tools={"a"}).run(
            self.program(steps=[{"id": "only", "tool": "a"}], result=None)
        )

        assert [
            (outcome.step_id, outcome.tool, outcome.status) for outcome in result.steps
        ] == [("only", "a", StepStatus.COMPLETED)]
        assert "payload" not in json.dumps(result.model_dump(mode="json"))


class TestTheModelFacingTool:
    """What the factory offers, and what it refuses to offer."""

    LIMITS = ToolProgramLimits(
        max_steps=4,
        max_concurrency=2,
        wall_clock_ms=1_000,
        max_total_output_bytes=10_000,
        max_result_bytes=5_000,
    )

    def tool(self, name: str) -> StructuredTool:
        async def _run(value: str = "v") -> str:
            return json.dumps({"value": value})

        return StructuredTool.from_function(
            coroutine=_run, name=name, description=f"{name} description"
        )

    def test_no_tool_is_offered_when_there_is_nothing_to_batch(self) -> None:
        assert (
            ToolProgramToolFactory(limits=self.LIMITS).build_tool(tools_by_name={})
            is None
        )

    def test_the_program_never_lists_itself_as_a_schedulable_tool(self) -> None:
        from agent_runtime.capabilities.tool_program.tool import TOOL_NAME  # noqa: PLC0415

        built = ToolProgramToolFactory(limits=self.LIMITS).build_tool(
            tools_by_name={TOOL_NAME: self.tool(TOOL_NAME)}
        )

        # The only candidate was the program itself, so there is nothing left.
        assert built is None

    async def test_the_tool_returns_one_json_result_for_the_whole_plan(self) -> None:
        built = ToolProgramToolFactory(limits=self.LIMITS).build_tool(
            tools_by_name={"alpha": self.tool("alpha")}
        )
        assert built is not None

        payload = json.loads(
            await built.ainvoke(
                {
                    "steps": [
                        {"id": "s", "tool": "alpha", "arguments": {"value": "hi"}}
                    ],
                    "result": {"$from": "s", "path": ["value"]},
                }
            )
        )

        assert payload["status"] == "completed"
        assert payload["result"] == "hi"

    async def test_an_invalid_plan_comes_back_as_a_typed_failure_not_a_traceback(
        self,
    ) -> None:
        built = ToolProgramToolFactory(limits=self.LIMITS).build_tool(
            tools_by_name={"alpha": self.tool("alpha")}
        )
        assert built is not None

        payload = json.loads(
            await built.ainvoke(
                {"steps": [{"id": "s", "tool": "nowhere"}], "result": None}
            )
        )

        assert payload["status"] == "failed"
        assert payload["error_code"] == ToolProgramErrorCode.UNKNOWN_TOOL.value
        assert payload["failed_step"] == "s"
        assert "Traceback" not in json.dumps(payload)
