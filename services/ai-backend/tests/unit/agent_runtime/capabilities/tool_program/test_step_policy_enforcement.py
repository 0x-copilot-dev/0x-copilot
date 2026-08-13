"""A batched step must be enforced exactly like a direct one.

This is the file that decides whether ``run_tool_program`` is a legitimate
batching primitive or a policy hole with a schema. The claim under test is not
"the program calls something that looks like the enforcement point" — it is that
a step is admitted, charged, settled and result-capped by **the same objects** a
directly-dispatched tool call is.

So nothing here fakes the guard. Every test binds a real
:class:`ToolBudgetGuard` over a real :class:`ToolCallLedger` and a real
:class:`ToolBudgetMiddleware`, runs a program, and then reads that guard's own
ledger. A dispatcher that reached the tool by any other route would leave the
ledger empty and every one of these red.

Verified against the pre-change tree: the WIP dispatched steps through the
interpreter bridge (``HitlPolicyToolInvoker`` → ``LangChainToolDispatcher`` →
``tool.ainvoke``), which charges nothing on this ledger and applies no result
cap, so the budget and cap tests below fail on it while the tool still
"succeeds".
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.capabilities.tool_program import (
    MiddlewareStepDispatcher,
    RunToolProgramInput,
    StepStatus,
    ToolProgramErrorCode,
    ToolProgramExecutor,
    ToolProgramLimits,
)
from agent_runtime.context.memory.contracts import (
    ContextCompressionStrategy,
    TokenBudgetPolicy,
)
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from agent_runtime.persistence.records import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from runtime_adapters.in_memory.offload import InMemoryOffloadWriter
from runtime_worker.tool_call_ledger import ToolCallLedger


class ProgramUnderRealGuardMixin:
    """One program, one real budget guard, and the tools it may schedule."""

    RUN_ID = "run-tool-program-enforcement"

    def limits(self, **overrides: int) -> ToolProgramLimits:
        values: dict[str, int] = {
            "max_steps": 8,
            "max_concurrency": 4,
            "wall_clock_ms": 5_000,
            "max_total_output_bytes": 1_000_000,
            "max_result_bytes": 500_000,
        }
        values.update(overrides)
        return ToolProgramLimits(**values)

    def echo_tool(self, name: str, *, payload: str = "ok") -> StructuredTool:
        """A tool whose result is a JSON document, like most real ones."""

        async def _echo(value: str = payload) -> str:
            return json.dumps({"tool": name, "value": value})

        return StructuredTool.from_function(
            coroutine=_echo, name=name, description=f"Return {name}'s value."
        )

    def guard(
        self,
        *,
        max_calls: int | None = None,
        tool_result_admission: ToolResultAdmissionAdapter | None = None,
    ) -> tuple[ToolBudgetGuard, ToolCallLedger]:
        records = (
            [
                ToolBudgetRecord(
                    id="tool-program-enforcement",
                    org_id=None,
                    tool_name="*",
                    max_calls_per_run=max_calls,
                    enforcement=ToolBudgetEnforcement.HARD,
                )
            ]
            if max_calls is not None
            else []
        )
        ledger = ToolCallLedger(self.RUN_ID)
        return (
            ToolBudgetGuard(
                middleware=ToolBudgetMiddleware(records),
                ledger=ledger,
                max_surfaced_rejections=10_000,
                tool_result_admission=tool_result_admission,
            ),
            ledger,
        )

    def executor(self, tools: dict[str, StructuredTool], **overrides: int):
        return ToolProgramExecutor(
            dispatcher=MiddlewareStepDispatcher(tools_by_name=tools),
            authorized_tool_names=frozenset(tools),
            limits=self.limits(**overrides),
        )

    async def run_program(
        self,
        *,
        tools: dict[str, StructuredTool],
        program: dict[str, object],
        guard: ToolBudgetGuard,
        **overrides: int,
    ):
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            return await self.executor(tools, **overrides).run(
                RunToolProgramInput.model_validate(program)
            )
        finally:
            ToolBudgetGuard.unbind(token)

    @staticmethod
    def charged_tools(ledger: ToolCallLedger, *names: str) -> list[str]:
        """The budget-scoped charges the run's own ledger recorded, by name.

        Read through :meth:`ToolCallLedger.charged_calls`, the same accessor the
        per-tool budget middleware answers from, so a charge this sees is a
        charge that would have refused the next call.
        """

        return sorted(name for name in names for _ in range(ledger.charged_calls(name)))


class TestEveryStepPassesTheSharedEnforcementPoint(ProgramUnderRealGuardMixin):
    """The budget ledger is the witness: a step that skipped it leaves no row."""

    async def test_each_step_is_charged_against_the_run_tool_budget(self) -> None:
        tools = {"alpha": self.echo_tool("alpha"), "beta": self.echo_tool("beta")}
        guard, ledger = self.guard(max_calls=10)

        result = await self.run_program(
            tools=tools,
            guard=guard,
            program={
                "steps": [
                    {"id": "a", "tool": "alpha", "arguments": {"value": "1"}},
                    {"id": "b", "tool": "beta", "arguments": {"value": "2"}},
                ],
                "result": {"$from": "b", "path": ["value"]},
            },
        )

        assert result.status == "completed"
        assert result.result == "2"
        # Two steps ran, so the run's own tool budget shows two charges. A
        # dispatcher that called the tools directly would show none.
        assert self.charged_tools(ledger, "alpha", "beta") == ["alpha", "beta"]

    async def test_a_step_over_the_budget_is_refused_and_the_tool_never_runs(
        self,
    ) -> None:
        invocations: list[str] = []

        async def _counted(value: str = "x") -> str:
            invocations.append(value)
            return json.dumps({"value": value})

        counted = StructuredTool.from_function(
            coroutine=_counted, name="counted", description="Count invocations."
        )
        tools = {"counted": counted}
        # One call allowed; the program asks for two, sequenced so the second
        # cannot be admitted before the first has been charged.
        guard, ledger = self.guard(max_calls=1)

        result = await self.run_program(
            tools=tools,
            guard=guard,
            program={
                "steps": [
                    {"id": "first", "tool": "counted", "arguments": {"value": "1"}},
                    {
                        "id": "second",
                        "tool": "counted",
                        "arguments": {"value": "2"},
                        "depends_on": ["first"],
                    },
                ],
                "result": {"$from": "second"},
            },
        )

        assert result.status == "failed"
        assert result.failed_step == "second"
        assert result.error_code is ToolProgramErrorCode.STEP_DENIED
        # The batch does not collapse into "the batch failed": the refused step
        # names itself, and the step that did run says so.
        statuses = {outcome.step_id: outcome.status for outcome in result.steps}
        assert statuses == {
            "first": StepStatus.COMPLETED,
            "second": StepStatus.DENIED,
        }
        assert invocations == ["1"], "the refused step still reached its tool"
        assert self.charged_tools(ledger, "counted") == ["counted"]

    async def test_a_refused_step_reports_a_safe_message_naming_itself(self) -> None:
        tools = {"counted": self.echo_tool("counted")}
        guard, ledger = self.guard(max_calls=1)
        # Spend the run's whole allowance before the program starts, so the
        # only step is refused by budget the moment it is admitted.
        ledger.started("prior-direct-call", tool_name="counted", budget_scoped=True)

        result = await self.run_program(
            tools=tools,
            guard=guard,
            program={
                "steps": [{"id": "only", "tool": "counted", "arguments": {}}],
                "result": {"$from": "only"},
            },
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.STEP_DENIED
        # A safe, model-facing sentence — no traceback, no internal identifiers.
        assert result.safe_message is not None
        assert "only" in result.safe_message
        assert "Traceback" not in result.safe_message
        # And the spend a *direct* call made is what refused the batched one:
        # one shared allowance, not one per dispatch route.
        assert self.charged_tools(ledger, "counted") == ["counted"]


class TestStepResultsPassTheModelAdmissionCap(ProgramUnderRealGuardMixin):
    """An oversized step result is bounded before the program can reference it."""

    #: Comfortably past the adapter's inline budget once serialized.
    OVERSIZED_CHARS = 200_000

    def admission(self) -> ToolResultAdmissionAdapter:
        return ToolResultAdmissionAdapter(
            InMemoryOffloadWriter(),
            policy=TokenBudgetPolicy(max_input_tokens=4_000, recent_context_ratio=0.25),
        )

    async def test_an_oversized_step_result_is_capped_not_carried_whole(self) -> None:
        body = "y" * self.OVERSIZED_CHARS

        async def _huge() -> str:
            return body

        huge = StructuredTool.from_function(
            coroutine=_huge, name="huge", description="Return an oversized payload."
        )
        admission = self.admission()
        # Sanity: this payload really is one the cap acts on, so a green test
        # cannot mean "nothing was oversized".
        assert (
            admission.admit(body, trace_id="probe").strategy
            is ContextCompressionStrategy.OFFLOAD
        )
        guard, _ = self.guard(max_calls=10, tool_result_admission=admission)

        result = await self.run_program(
            tools={"huge": huge},
            guard=guard,
            program={
                "steps": [{"id": "big", "tool": "huge", "arguments": {}}],
                "result": {"$from": "big"},
            },
        )

        assert result.status == "completed"
        assert isinstance(result.result, str)
        assert len(result.result) < self.OVERSIZED_CHARS, (
            "the step's raw result reached the projection — a batched step "
            "bypassed the model-visible result cap"
        )

    async def test_a_later_step_sees_the_capped_value_not_the_raw_one(self) -> None:
        body = "z" * self.OVERSIZED_CHARS
        seen: list[str] = []

        async def _huge() -> str:
            return body

        async def _observe(value: str = "") -> str:
            seen.append(value)
            return json.dumps({"length": len(value)})

        tools = {
            "huge": StructuredTool.from_function(
                coroutine=_huge, name="huge", description="Return an oversized payload."
            ),
            "observe": StructuredTool.from_function(
                coroutine=_observe, name="observe", description="Observe a value."
            ),
        }
        guard, _ = self.guard(max_calls=10, tool_result_admission=self.admission())

        result = await self.run_program(
            tools=tools,
            guard=guard,
            program={
                "steps": [
                    {"id": "big", "tool": "huge", "arguments": {}},
                    {
                        "id": "read",
                        "tool": "observe",
                        "arguments": {"value": {"$from": "big"}},
                    },
                ],
                "result": {"$from": "read", "path": ["length"]},
            },
        )

        assert result.status == "completed"
        assert seen and len(seen[0]) < self.OVERSIZED_CHARS, (
            "a downstream step received the uncapped payload; the cap has to "
            "hold between steps, not only on the way out"
        )


class TestAuthorizationIsEnforcedAtDispatchNotOnlyAtCompile(ProgramUnderRealGuardMixin):
    """Plan-time authorization is a courtesy; the dispatcher is the gate."""

    async def test_a_tool_absent_from_the_bound_surface_is_refused(self) -> None:
        tools = {"alpha": self.echo_tool("alpha")}
        guard, ledger = self.guard(max_calls=10)
        # Deliberately compile against a WIDER name set than the dispatcher was
        # bound to: this is the shape a future plan-validation bug would take,
        # and it must still not reach a tool.
        executor = ToolProgramExecutor(
            dispatcher=MiddlewareStepDispatcher(tools_by_name=tools),
            authorized_tool_names=frozenset({"alpha", "forbidden"}),
            limits=self.limits(),
        )

        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = await executor.run(
                RunToolProgramInput.model_validate(
                    {
                        "steps": [
                            {"id": "bad", "tool": "forbidden", "arguments": {}},
                        ],
                        "result": {"$from": "bad"},
                    }
                )
            )
        finally:
            ToolBudgetGuard.unbind(token)

        assert result.status == "failed"
        assert result.failed_step == "bad"
        assert result.error_code is ToolProgramErrorCode.STEP_DENIED
        assert self.charged_tools(ledger, "alpha", "forbidden") == []

    async def test_a_plan_naming_an_unauthorized_tool_is_refused_before_any_step(
        self,
    ) -> None:
        tools = {"alpha": self.echo_tool("alpha")}
        guard, ledger = self.guard(max_calls=10)

        result = await self.run_program(
            tools=tools,
            guard=guard,
            program={
                "steps": [
                    {"id": "ok", "tool": "alpha", "arguments": {}},
                    {"id": "bad", "tool": "nowhere", "arguments": {}},
                ],
                "result": {"$from": "ok"},
            },
        )

        assert result.status == "failed"
        assert result.error_code is ToolProgramErrorCode.UNKNOWN_TOOL
        assert result.failed_step == "bad"
        # Compile-time refusal means the *authorized* step did not run either;
        # a plan is accepted or rejected whole.
        assert self.charged_tools(ledger, "alpha", "nowhere") == []


class TestTheExecutorHoldsNoRouteToATool(ProgramUnderRealGuardMixin):
    """The structural half of the claim, asserted rather than described."""

    def test_the_executor_takes_a_dispatcher_and_nothing_tool_shaped(self) -> None:
        import inspect  # noqa: PLC0415

        parameters = set(inspect.signature(ToolProgramExecutor.__init__).parameters) - {
            "self"
        }

        assert parameters == {
            "dispatcher",
            "authorized_tool_names",
            "limits",
            "clock",
        }, (
            "the executor grew a second way to reach a tool; every step must "
            "go through the dispatcher or the enforcement claim is void"
        )

    def test_no_tool_is_reachable_without_a_dispatcher(self) -> None:
        with pytest.raises(TypeError):
            ToolProgramExecutor(  # type: ignore[call-arg]
                authorized_tool_names=frozenset({"alpha"}),
                limits=self.limits(),
            )
