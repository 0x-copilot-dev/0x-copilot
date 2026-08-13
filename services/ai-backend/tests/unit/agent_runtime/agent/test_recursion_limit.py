"""The graph's super-step budget: chosen here, carried to Pregel, typed on blow-up.

Three separate things can silently not happen, so each is asserted against the
thing that would actually break:

1. ``runtime_config`` must emit ``recursion_limit`` as a TOP-LEVEL
   ``RunnableConfig`` key. Under ``configurable`` it is inert — Pregel never
   reads it — and every mock-based assertion would still pass.
2. LangGraph must honour the number. Asserted by running a REAL compiled
   ``StateGraph`` through the real ``ainvoke_runtime`` / ``astream_runtime``
   helpers, not by inspecting a captured config: the config is the input to the
   claim, the step counter is the claim.
3. The blow-up must arrive as this service's typed error, not as the library's
   ``GraphRecursionError``. User-facing failure copy is the model paraphrasing a
   typed runtime error, so an untranslated library exception reaches the user as
   whatever the model decides it meant.

Without the ``recursion_limit`` key these tests do not merely lose an assertion —
the looping graphs below simply complete, because langgraph defaults to 10007
super-steps (``langgraph._internal._config.DEFAULT_RECURSION_LIMIT``, measured on
1.2.9). That is the state this change exists to end, and it is why the loop
counts here are asserted through behaviour rather than through a captured dict.
Nothing here depends on that number staying 10007 — only on it not being ours.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph

from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.execution import deep_agent_builder as builder_module
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.deep_agent_builder import (
    DeepAgentBuildRequest,
    build_deep_agent,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.runtime import (
    ainvoke_runtime,
    astream_runtime,
    runtime_config,
)
from agent_runtime.hyperparameters.contracts import ExecutionHyperparameters


class _LoopState(TypedDict, total=False):
    messages: list
    ticks: int


class LoopingGraphMixin:
    """A real compiled LangGraph whose only job is to burn super-steps.

    One node, one self-edge, one counter. Each pass is exactly one super-step,
    so ``target`` is a direct request for a known number of them — which is what
    makes "the configured limit reached Pregel" observable rather than inferred.
    """

    #: Comfortably above any limit these tests configure, so the graph's own stop
    #: condition never fires first and a completion really does mean "the limit
    #: was not enforced".
    RUNAWAY_TARGET = 10_000

    @staticmethod
    def build_graph(target: int):
        graph = StateGraph(_LoopState)
        graph.add_node("tick", lambda state: {"ticks": state.get("ticks", 0) + 1})
        graph.set_entry_point("tick")
        graph.add_conditional_edges(
            "tick",
            lambda state: "tick" if state.get("ticks", 0) < target else END,
            {"tick": "tick", END: END},
        )
        return graph.compile()

    @classmethod
    def harness(
        cls,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
        *,
        recursion_limit: int,
        target: int,
    ) -> RuntimeHarness:
        return RuntimeHarness(
            agent=cls.build_graph(target),
            context=context.model_copy(
                update={
                    "run_id": "run_recursion",
                    "request_id": "request_recursion",
                    "recursion_limit": recursion_limit,
                }
            ),
            dependencies=dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )


class TestRecursionLimitReachesTheInvocationConfig(LoopingGraphMixin):
    def test_config_carries_the_limit_as_a_top_level_key(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        harness = self.harness(
            runtime_context_admin,
            fake_dependencies,
            recursion_limit=37,
            target=1,
        )

        config = runtime_config(harness)

        assert config["recursion_limit"] == 37
        # The failure mode this pins: nested under ``configurable`` the key is
        # accepted, carried, logged — and never read by Pregel.
        assert "recursion_limit" not in config["configurable"]  # type: ignore[operator]

    async def test_pregel_stops_the_graph_at_the_configured_limit(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """A graph that would run forever stops at OUR number, not the library's.

        Fails on unpatched code by *completing*: with no ``recursion_limit`` in
        the config the graph runs to its own 10 000-tick stop condition and no
        error is raised at all.
        """

        harness = self.harness(
            runtime_context_admin,
            fake_dependencies,
            recursion_limit=6,
            target=self.RUNAWAY_TARGET,
        )

        with pytest.raises(AgentRuntimeError) as caught:
            await ainvoke_runtime(harness, [])

        assert caught.value.code is RuntimeErrorCode.RECURSION_LIMIT_EXCEEDED

    async def test_a_run_below_the_limit_still_completes(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The limit is a ceiling, not a step count — 5 steps under a limit of 6 pass."""

        harness = self.harness(
            runtime_context_admin,
            fake_dependencies,
            recursion_limit=6,
            target=5,
        )

        result = await ainvoke_runtime(harness, [])

        assert result["ticks"] == 5


class TestRecursionBlowUpIsTyped(LoopingGraphMixin):
    async def test_invoke_raises_the_typed_error_not_the_library_one(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        harness = self.harness(
            runtime_context_admin,
            fake_dependencies,
            recursion_limit=4,
            target=self.RUNAWAY_TARGET,
        )

        with pytest.raises(AgentRuntimeError) as caught:
            await ainvoke_runtime(harness, [])

        error = caught.value
        assert error.code is RuntimeErrorCode.RECURSION_LIMIT_EXCEEDED
        # A graph that just spent its whole allowance re-runs the same loop at the
        # same cost. Retrying is never the right answer, so the envelope must not
        # invite it — and ``GraphRecursionError`` subclasses ``RuntimeError``,
        # which the generic handler would have marked retryable.
        assert error.retryable is False
        assert isinstance(error.__cause__, GraphRecursionError)
        # The safe message is what the model paraphrases to the user, so it has
        # to describe the situation rather than name a library symbol.
        assert "GraphRecursionError" not in error.safe_message
        assert "step limit" in error.safe_message

    async def test_stream_raises_the_typed_error_too(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The worker's default path is ``astream``; translating only ``ainvoke``
        would leave the real path emitting the raw library error."""

        harness = self.harness(
            runtime_context_admin,
            fake_dependencies,
            recursion_limit=4,
            target=self.RUNAWAY_TARGET,
        )

        with pytest.raises(AgentRuntimeError) as caught:
            async for _ in astream_runtime(harness, []):
                pass

        assert caught.value.code is RuntimeErrorCode.RECURSION_LIMIT_EXCEEDED
        assert caught.value.retryable is False


class _ScriptedToolModel(BaseChatModel):
    """Emits ``rounds`` tool calls, one per turn, then a final text answer."""

    rounds: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        emitted = sum(1 for message in messages if isinstance(message, AIMessage))
        if emitted < self.rounds:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": "ping", "args": {"x": "1"}, "id": f"call_{emitted}"}
                ],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(
        self, tools: Sequence[object], **kwargs: Any
    ) -> "_ScriptedToolModel":
        return self


class TestTheDefaultIsHighEnoughForARealTurn:
    """The other way to get this wrong: a limit so low healthy runs die on it.

    ``langchain_core``'s default of 25 is about five tool rounds on this graph —
    below the per-tool-name ``tool_call_budget`` of 10, so picking it would end
    normal runs on an opaque step limit instead of on the legible budget
    message. This pins the default against the REAL Deep Agents graph so the
    number cannot be lowered into that territory without a red test.
    """

    ROUNDS = 12

    @staticmethod
    def _build(rounds: int, monkeypatch: pytest.MonkeyPatch) -> object:
        monkeypatch.setattr(
            builder_module,
            "build_chat_model",
            lambda *args, **kwargs: _ScriptedToolModel(rounds=rounds),
        )
        return build_deep_agent(
            DeepAgentBuildRequest(
                tools=(
                    StructuredTool.from_function(
                        func=lambda x: "pong", name="ping", description="ping"
                    ),
                ),
                model_config=ModelConfig(
                    provider="openai",
                    model_name="gpt-5.4-mini",
                    max_input_tokens=128_000,
                    timeout_seconds=30,
                    temperature=0,
                ),
                system_prompt="Use the ping tool as instructed.",
                # Middleware and a subagent are what make this the production
                # shape rather than a bare graph: both raise the per-round cost,
                # and it is the production cost the default has to clear.
                subagents=(
                    {
                        "name": "researcher",
                        "description": "Research reviewed sources.",
                        "system_prompt": "Return a concise source review.",
                    },
                ),
                middleware=(RuntimeControlMiddleware(),),
                universal_middleware_factories=(RuntimeControlMiddleware,),
            )
        )

    async def test_twelve_tool_rounds_fit_inside_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent = self._build(self.ROUNDS, monkeypatch)

        result = await agent.ainvoke(
            {"messages": [("user", "go")]},
            config={
                "recursion_limit": ExecutionHyperparameters().recursion_limit,
                "configurable": {"thread_id": "t"},
            },
        )

        assert isinstance(result["messages"][-1], AIMessage)

    async def test_the_measured_per_round_cost_is_still_small(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards the arithmetic the default is derived from.

        Measured 6 + 4*rounds on langgraph 1.2.9 / deepagents 0.7.1. Asserted as
        a loose ceiling, not the exact fit: a library or middleware change is
        allowed to move it, but if a round ever costs ~40 super-steps instead of
        ~4 then 500 stops meaning "125 rounds" and the comment deriving it is
        wrong.
        """

        agent = self._build(self.ROUNDS, monkeypatch)
        budget = 6 + 10 * self.ROUNDS

        result = await agent.ainvoke(
            {"messages": [("user", "go")]},
            config={
                "recursion_limit": budget,
                "configurable": {"thread_id": "t"},
            },
        )

        assert isinstance(result["messages"][-1], AIMessage)
