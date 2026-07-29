"""SMELL-01 — the graph tool seam consults F6, and defaults to serial without it.

The sibling module ``test_runtime_tool_control`` pins the Step-2 posture: a
multi-tool turn is serial, both through ``awrap_tool_call`` directly and through
a live LangGraph turn. Those assertions are untouched and must stay green — they
are the feature-off parity proof.

This module adds the other half: the same seam, the same middleware ordering,
now widened *only* when an installed F6 source positively names the call.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
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

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.control_plane.context import (
    RunControlBinding,
    RunControlContext,
    RunSerialAdmission,
)
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.feature_modes import FeatureModeSet
from agent_runtime.control_plane.parallel_admission import (
    ParallelAdmissionGrant,
    ToolAdmissionRequest,
)
from runtime_worker.run_control import RunControlAssignment

_COHORT = "batch-1:segment-0"


def _request(
    *, name: str = "observed_tool", call_id: str = "call-1"
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": {"value": 1},
            "id": call_id,
            "type": "tool_call",
        },
        tool=None,
        state={},
        runtime=cast(Any, object()),
    )


class _CohortPort:
    """Stand-in for the F6 source root will install: names calls, not tools."""

    def __init__(self, *, call_ids: set[str], max_parallelism: int = 3) -> None:
        self._call_ids = call_ids
        self._max_parallelism = max_parallelism
        self.asked: list[ToolAdmissionRequest] = []

    def grant_for(self, request: ToolAdmissionRequest) -> ParallelAdmissionGrant | None:
        self.asked.append(request)
        if request.tool_call_id not in self._call_ids:
            return None
        return ParallelAdmissionGrant(
            tool_call_id=request.tool_call_id,
            cohort_id=_COHORT,
            max_parallelism=self._max_parallelism,
        )


class _FanoutModel(BaseChatModel):
    """Emit three sibling calls in one turn, then a final answer."""

    @property
    def _llm_type(self) -> str:
        return "granted-fanout-test"

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


def _install(middleware: RuntimeToolControlMiddleware, port: object) -> None:
    """Install a source on the middleware's own fallback admission.

    The fallback is the admission a graph without a verified run binding uses,
    which is the one these unit-level graphs have. Production installs on the
    run-scoped admission through ``RunControlContext.install_parallel_admission``
    instead; both reach the same :class:`RunSerialAdmission` seam.
    """

    admission = cast(RunSerialAdmission, middleware._fallback_serial_admission)
    admission.install_parallel_admission(cast(Any, port))


class TestAdmissionIsConsultedAtTheSeam:
    async def test_every_graph_visible_call_is_offered_to_the_admission(self) -> None:
        """Coverage is the point: the source sees the call, then decides."""

        middleware = RuntimeToolControlMiddleware()
        port = _CohortPort(call_ids=set())
        _install(middleware, port)

        async def handler(request: ToolCallRequest) -> ToolMessage:
            return ToolMessage(
                content="ok",
                tool_call_id=request.tool_call["id"],
            )

        # ``write_todos`` is framework-injected by Deep Agents, not a tool the
        # caller registered. It crosses the same seam as everything else.
        await middleware.awrap_tool_call(
            _request(name="write_todos", call_id="call-injected"),
            handler,
        )
        await middleware.awrap_tool_call(
            _request(name="observed_tool", call_id="call-registered"),
            handler,
        )

        assert [item.tool_call_id for item in port.asked] == [
            "call-injected",
            "call-registered",
        ]
        assert [item.tool_name for item in port.asked] == [
            "write_todos",
            "observed_tool",
        ]

    async def test_a_framework_injected_tool_is_serial_without_a_grant(self) -> None:
        middleware = RuntimeToolControlMiddleware()
        _install(middleware, _CohortPort(call_ids={"call-0"}))
        active = 0
        maximum_active = 0

        async def handler(request: ToolCallRequest) -> ToolMessage:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            for _ in range(4):
                await asyncio.sleep(0)
            active -= 1
            return ToolMessage(
                content="ok",
                tool_call_id=request.tool_call["id"],
            )

        # Only ``call-0`` is granted; the injected siblings are not, so nothing
        # may overlap.
        await asyncio.gather(
            middleware.awrap_tool_call(
                _request(name="write_todos", call_id="call-0"),
                handler,
            ),
            middleware.awrap_tool_call(
                _request(name="write_todos", call_id="call-1"),
                handler,
            ),
            middleware.awrap_tool_call(
                _request(name="write_todos", call_id="call-2"),
                handler,
            ),
        )

        assert maximum_active == 1


class TestGrantedFanoutOverlaps:
    async def test_a_granted_cohort_overlaps_at_the_seam(self) -> None:
        middleware = RuntimeToolControlMiddleware()
        _install(
            middleware,
            _CohortPort(call_ids={"call-0", "call-1", "call-2"}, max_parallelism=2),
        )
        active = 0
        maximum_active = 0

        async def handler(request: ToolCallRequest) -> ToolMessage:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            for _ in range(6):
                await asyncio.sleep(0)
            active -= 1
            return ToolMessage(
                content="ok",
                tool_call_id=request.tool_call["id"],
            )

        await asyncio.gather(
            *(
                middleware.awrap_tool_call(
                    _request(call_id=f"call-{index}"),
                    handler,
                )
                for index in range(3)
            )
        )

        assert maximum_active == 2

    async def test_live_langchain_turn_overlaps_only_up_to_the_allowance(self) -> None:
        """The real graph, the real fan-out, the F6 answer actually observed."""

        active = 0
        maximum_active = 0

        async def observed_tool(value: int) -> str:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            for _ in range(6):
                await asyncio.sleep(0)
            active -= 1
            return str(value)

        tool = StructuredTool.from_function(
            name="observed_tool",
            description="Record one observed value.",
            coroutine=observed_tool,
        )
        middleware = RuntimeToolControlMiddleware()
        _install(
            middleware,
            _CohortPort(
                call_ids={"call-0", "call-1", "call-2"},
                max_parallelism=2,
            ),
        )
        graph = create_agent(
            model=_FanoutModel(),
            tools=[tool],
            middleware=[middleware],
        )

        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Run all observations.")]}
        )

        assert maximum_active == 2
        assert result["messages"][-1].content == "done"

    async def test_live_langchain_turn_stays_serial_when_only_one_is_granted(
        self,
    ) -> None:
        active = 0
        maximum_active = 0

        async def observed_tool(value: int) -> str:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            for _ in range(6):
                await asyncio.sleep(0)
            active -= 1
            return str(value)

        tool = StructuredTool.from_function(
            name="observed_tool",
            description="Record one observed value.",
            coroutine=observed_tool,
        )
        middleware = RuntimeToolControlMiddleware()
        _install(middleware, _CohortPort(call_ids={"call-0"}, max_parallelism=3))
        graph = create_agent(
            model=_FanoutModel(),
            tools=[tool],
            middleware=[middleware],
        )

        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Run all observations.")]}
        )

        assert maximum_active == 1
        assert result["messages"][-1].content == "done"


class TestProductionInstallSeam:
    """The path root will actually use: bind the run, then install the source.

    The other tests here reach the middleware's instance-local fallback, which
    is what an unbound legacy graph uses. This one goes through the run-scoped
    admission the context var carries — the object supervisor and every local
    Deep Agents subagent share — so the seam root threads is exercised rather
    than assumed.
    """

    @staticmethod
    def _binding() -> RunControlBinding:
        assignment = RunControlAssignment.safe_active_v1()
        snapshot = RunControlSnapshot.create(
            run_id="run_smell01",
            conversation_id="conv_1",
            subject_fingerprint="a" * 64,
            deployment_profile="single_user_desktop",
            harness_variant_ref=assignment.harness_variant_ref,
            task_policy_selection_ref=assignment.task_policy_selection_ref,
            policy_revisions=assignment.policy_revisions,
            feature_modes=FeatureModeSet(),
            budget_envelope_ref=assignment.budget_envelope_ref,
            assignment_revision=assignment.assignment_revision,
        )
        return RunControlBinding(
            snapshot=snapshot,
            effective_modes=FeatureModeSet(),
            decisions=(),
        )

    async def _observe(self, *, install: bool) -> int:
        middleware = RuntimeToolControlMiddleware()
        active = 0
        maximum_active = 0

        async def handler(request: ToolCallRequest) -> ToolMessage:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            for _ in range(6):
                await asyncio.sleep(0)
            active -= 1
            return ToolMessage(
                content="ok",
                tool_call_id=request.tool_call["id"],
            )

        token = RunControlContext.bind_for_run(self._binding())
        try:
            if install:
                RunControlContext.install_parallel_admission(
                    cast(
                        Any,
                        _CohortPort(
                            call_ids={"call-0", "call-1", "call-2"},
                            max_parallelism=2,
                        ),
                    )
                )
            await asyncio.gather(
                *(
                    middleware.awrap_tool_call(
                        _request(call_id=f"call-{index}"),
                        handler,
                    )
                    for index in range(3)
                )
            )
        finally:
            RunControlContext.unbind(token)
        return maximum_active

    async def test_a_bound_run_is_serial_until_a_source_is_installed(self) -> None:
        """Feature-off parity on the run-scoped admission, not just the fallback."""

        assert await self._observe(install=False) == 1

    async def test_installing_on_the_bound_run_widens_the_graph_seam(self) -> None:
        assert await self._observe(install=True) == 2

    def test_installing_without_a_bound_run_fails_closed(self) -> None:
        with pytest.raises(RuntimeError, match="run control is not bound"):
            RunControlContext.install_parallel_admission(
                cast(Any, _CohortPort(call_ids={"call-0"}))
            )


class TestMalformedCallsStaySerial:
    async def test_a_call_without_a_provider_id_still_refuses_inside_the_permit(
        self,
    ) -> None:
        """Building the admission request must not move where the refusal is."""

        from agent_runtime.execution.tool_errors import BudgetExceeded

        middleware = RuntimeToolControlMiddleware()
        _install(middleware, _CohortPort(call_ids={"call-0"}))

        async def handler(request: ToolCallRequest) -> ToolMessage:
            raise AssertionError("a call with no id must never reach the handler")

        request = ToolCallRequest(
            tool_call={
                "name": "observed_tool",
                "args": {},
                "id": "",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=cast(Any, object()),
        )

        try:
            await middleware.awrap_tool_call(request, handler)
        except BudgetExceeded as exc:
            assert "model call id" in str(exc)
        else:  # pragma: no cover - the refusal is the assertion
            raise AssertionError("a call with no id must be refused")

    async def test_the_sync_seam_stays_serial_with_a_source_installed(self) -> None:
        middleware = RuntimeToolControlMiddleware()
        _install(
            middleware,
            _CohortPort(call_ids={"call-0", "call-1"}, max_parallelism=2),
        )
        entered: list[str] = []

        def handler(request: ToolCallRequest) -> ToolMessage:
            entered.append(str(request.tool_call["id"]))
            return ToolMessage(
                content="ok",
                tool_call_id=request.tool_call["id"],
            )

        for index in range(2):
            middleware.wrap_tool_call(
                _request(call_id=f"call-{index}"),
                handler,
            )

        assert entered == ["call-0", "call-1"]
