"""Graph-wide F4/F5 admission and F6 serial-default middleware tests."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetAdmit,
    ToolBudgetReject,
)
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.persistence.records import ToolBudgetEnforcement, ToolBudgetRecord
from agent_runtime.capabilities.tools.tool_use_enforcement import PolicyBlockedTool


def _request(
    *,
    name: str = "write_todos",
    call_id: str = "call-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": {"items": ["one"]},
            "id": call_id,
            "type": "tool_call",
        },
        tool=None,
        state={},
        runtime=cast(Any, object()),
    )


class _RecordingGuard:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.started: list[tuple[str, int]] = []
        self.settled: list[tuple[str, int]] = []
        self.policy_outcomes: list[bool] = []
        self.admissions: list[object] = []
        self.policy_blocks: list[str] = []
        self.model_turns: list[tuple[int, str]] = []

    def admit_task_policy(self, **_kwargs: object) -> None:
        return None

    def observe_upstream_policy_block(
        self,
        *,
        tool_name: str,
        **_kwargs: object,
    ) -> None:
        self.policy_blocks.append(tool_name)

    async def aobserve_upstream_policy_block(self, **kwargs: object) -> None:
        self.observe_upstream_policy_block(
            tool_name=str(kwargs.get("tool_name", "")),
        )

    def admit_model_turn(
        self,
        *,
        model_turn: int,
        execution_scope: str,
    ) -> None:
        self.model_turns.append((model_turn, execution_scope))

    async def aadmit_model_turn(
        self,
        *,
        model_turn: int,
        execution_scope: str,
    ) -> None:
        self.admit_model_turn(
            model_turn=model_turn,
            execution_scope=execution_scope,
        )

    def check_admit(self, **_kwargs: object) -> object:
        if not self.reject:
            return ToolBudgetAdmit()
        return ToolBudgetReject(
            budget=ToolBudgetRecord(
                id="budget-1",
                org_id=None,
                tool_name="*",
                max_calls_per_run=1,
                enforcement=ToolBudgetEnforcement.HARD,
            ),
            kind="calls",
            current=1,
            limit=1,
            tool_name="write_todos",
        )

    def rejection_error(self, _decision: object) -> Exception:
        return ToolBudgetRejected("Stop calling this tool and finalize.")

    def record_started(
        self,
        *,
        tool_name: str,
        estimated_input_tokens: int,
    ) -> str:
        self.started.append((tool_name, estimated_input_tokens))
        return "budget-call-1"

    def record_settled(
        self,
        *,
        call_id: str,
        observed_input_tokens: int,
    ) -> None:
        self.settled.append((call_id, observed_input_tokens))

    def record_task_policy_outcome(
        self,
        *,
        succeeded: bool,
        **_kwargs: object,
    ) -> None:
        self.policy_outcomes.append(succeeded)

    async def arecord_task_policy_outcome(
        self,
        *,
        succeeded: bool,
        **_kwargs: object,
    ) -> None:
        self.policy_outcomes.append(succeeded)

    def admit_model_visible_result(
        self,
        result: object,
        **_kwargs: object,
    ) -> str:
        self.admissions.append(result)
        return f"bounded:{result}"


class _SyncOnlyAdmissionGuard(_RecordingGuard):
    """Legacy adapter shape: canonical async seam must use its sync admission."""

    def __init__(self) -> None:
        super().__init__()
        self.task_policy_admissions = 0

    def admit_task_policy(self, **_kwargs: object) -> None:
        self.task_policy_admissions += 1


class _AsyncDurableAdmissionGuard(_RecordingGuard):
    """Durable adapter shape: completion proves admission precedes dispatch."""

    def __init__(self, observations: list[str]) -> None:
        super().__init__()
        self._observations = observations
        self.task_policy_admissions = 0

    async def aadmit_task_policy(self, **_kwargs: object) -> None:
        self.task_policy_admissions += 1
        self._observations.append("admission_persisted")


class _FanoutModel(BaseChatModel):
    """Emit three sibling calls in one turn, then a final answer."""

    @property
    def _llm_type(self) -> str:
        return "serial-fanout-test"

    @staticmethod
    def _reply(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "observed_tool",
                    "args": {"value": value},
                    "id": f"call-{value}",
                }
                for value in range(3)
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        del tools, kwargs
        return self


async def test_async_multi_tool_fanout_is_serial_by_default() -> None:
    middleware = RuntimeToolControlMiddleware()
    active = 0
    maximum_active = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return ToolMessage(
            content=request.tool_call["name"],
            tool_call_id=request.tool_call["id"],
        )

    await asyncio.gather(
        middleware.awrap_tool_call(_request(call_id="call-1"), handler),
        middleware.awrap_tool_call(_request(call_id="call-2"), handler),
        middleware.awrap_tool_call(_request(call_id="call-3"), handler),
    )

    assert maximum_active == 1


async def test_live_langchain_multi_tool_turn_is_serial_by_default() -> None:
    active = 0
    maximum_active = 0

    async def observed_tool(value: int) -> str:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return str(value)

    tool = StructuredTool.from_function(
        name="observed_tool",
        description="Record one observed value.",
        coroutine=observed_tool,
    )
    graph = create_agent(
        model=_FanoutModel(),
        tools=[tool],
        middleware=[RuntimeToolControlMiddleware()],
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Run all observations.")]}
    )

    assert maximum_active == 1
    assert result["messages"][-1].content == "done"


async def test_injected_tool_result_crosses_one_budget_and_result_boundary() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard()
    handler_calls = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_calls
        handler_calls += 1
        return ToolMessage(
            content="raw framework result",
            tool_call_id=request.tool_call["id"],
        )

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert handler_calls == 1
    assert result.content == "bounded:raw framework result"
    assert guard.started[0][0] == "write_todos"
    assert len(guard.started) == len(guard.settled) == 1
    assert guard.policy_outcomes == [True]
    assert guard.admissions == ["raw framework result"]


async def test_async_tool_seam_admits_a_legacy_sync_guard_once() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _SyncOnlyAdmissionGuard()

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="raw", tool_call_id=request.tool_call["id"])

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert result.content == "bounded:raw"
    assert guard.task_policy_admissions == 1
    assert len(guard.started) == len(guard.settled) == 1


async def test_async_tool_seam_awaits_durable_admission_once_before_dispatch() -> None:
    middleware = RuntimeToolControlMiddleware()
    observations: list[str] = []
    guard = _AsyncDurableAdmissionGuard(observations)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        assert observations == ["admission_persisted"]
        observations.append("tool_dispatched")
        return ToolMessage(content="raw", tool_call_id=request.tool_call["id"])

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert result.content == "bounded:raw"
    assert guard.task_policy_admissions == 1
    assert observations == ["admission_persisted", "tool_dispatched"]


async def test_command_tool_messages_are_bounded_before_model_admission() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard()

    async def handler(request: ToolCallRequest) -> Command[Any]:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="raw command result",
                        tool_call_id=request.tool_call["id"],
                    )
                ],
                "safe_state": True,
            }
        )

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert isinstance(result, Command)
    assert result.update["messages"][0].content == "bounded:raw command result"
    assert result.update["safe_state"] is True


async def test_list_tool_responses_cross_the_same_result_boundary() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard()

    async def handler(request: ToolCallRequest) -> list[ToolMessage | Command[Any]]:
        return [
            ToolMessage(
                content="raw list result",
                tool_call_id=request.tool_call["id"],
            ),
            Command(update={"safe_state": True}),
        ]

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert isinstance(result, list)
    assert result[0].content == "bounded:raw list result"
    assert result[1].update["safe_state"] is True


async def test_budget_rejection_short_circuits_framework_tool_safely() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard(reject=True)
    handler_calls = 0

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal handler_calls
        handler_calls += 1
        raise AssertionError("rejected handler must not execute")

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert handler_calls == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "finalize" in str(result.content)
    assert guard.started == []


def test_sync_tool_calls_use_the_same_result_boundary() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard()

    def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="raw sync result",
            tool_call_id=request.tool_call["id"],
        )

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = middleware.wrap_tool_call(_request(), handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert result.content == "bounded:raw sync result"
    assert guard.admissions == ["raw sync result"]


async def test_policy_blocked_tool_precedes_budget_admission() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard()
    blocked_tool = PolicyBlockedTool(
        name="call_mcp_tool",
        description="Call a connector.",
        safe_message="Blocked by policy.",
    )
    request = _request(name="call_mcp_tool")
    request = request.override(tool=blocked_tool)

    async def handler(inner_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Blocked by policy.",
            tool_call_id=inner_request.tool_call["id"],
        )

    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        result = await middleware.awrap_tool_call(request, handler)
    finally:
        ToolBudgetGuard.unbind(token)

    assert result.content == "Blocked by policy."
    assert guard.policy_blocks == ["call_mcp_tool"]
    assert guard.started == []
    assert guard.admissions == []


def test_model_turn_admission_uses_the_same_graph_wide_guard() -> None:
    middleware = RuntimeToolControlMiddleware()
    guard = _RecordingGuard()
    token = ToolBudgetGuard.bind_for_run(cast(ToolBudgetGuard, guard))
    try:
        update = middleware.before_model({}, object())
    finally:
        ToolBudgetGuard.unbind(token)

    assert update == {"runtime_control_model_turn": 1}
    assert guard.model_turns == [(1, "supervisor")]
