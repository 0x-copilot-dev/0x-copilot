"""The builtin tool observer fires on the shipped run path, not on a fixture.

The chain under test is the product's own: queue → :class:`RuntimeWorker` →
``RuntimeRunHandler.__init__`` (which calls ``install_builtin_hooks``) →
``RuntimeRunHandler.handle`` → ``ToolCallObservationContext.bind_for_run`` →
a compiled LangGraph agent whose ``middleware[0]`` is the same
:class:`RuntimeControlMiddleware` ``execution/factory.py:565`` composes →
``HookDispatch.tool_execute_before`` / ``tool_execute_after`` → the run's tally
→ ``_emit_tool_observation_summary``.

**Nothing here registers a hook.** That is the point: before this change the
registration table was empty in production, so a test that registered its own
probe proved the dispatcher worked and proved nothing about the product. These
tests fail if ``RuntimeRunHandler`` stops installing the builtin, if it stops
binding the ledger, or if it stops draining it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
)
from agent_runtime.execution.contracts import RuntimeDependencies
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.hooks import HookPhase, RuntimeHookContext, RuntimeHooks
from agent_runtime.hooks.builtin import (
    HOOK_NAME,
    ToolCallObservationContext,
    ToolCallObservationLedger,
    install_builtin_hooks,
)
from agent_runtime.hooks.contracts import (
    ToolExecuteAfterInput,
    ToolExecuteBeforeInput,
)
from agent_runtime.hooks.builtin.tool_observability import (
    _observe_tool_end,
    _observe_tool_start,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker

TOOL_NAME = "observed_tool"
TOOL_RESULT = "forty-two characters of tool output right he"


@pytest.fixture(autouse=True)
def _clean_hook_table():
    """Start every test with an EMPTY process table.

    So a passing assertion can only come from the product installing the
    builtin, never from a registration a previous test left behind.
    """

    RuntimeHooks.clear()
    try:
        yield
    finally:
        RuntimeHooks.clear()


class _ToolCallingModel(FakeListChatModel):
    """Calls the tool once, then answers. Records what it was handed back."""

    seen: list[Any] = []

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        del args, kwargs
        observed = [
            message.content for message in messages if isinstance(message, ToolMessage)
        ]
        if observed:
            _ToolCallingModel.seen.extend(observed)
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="done"))]
            )
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": TOOL_NAME,
                                "args": {"value": "raw"},
                                "id": "call-observed-1",
                            }
                        ],
                    )
                )
            ]
        )

    async def _agenerate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        return self._generate(messages)

    def bind_tools(self, tools: Any, **kwargs: Any):
        del tools, kwargs
        return self


class WorkerToolRunMixin:
    """Drives one run whose model really calls a tool through the middleware."""

    TOOL_SUMMARY_PREFIX = "runtime_hooks.tool_summary"
    RUN_LOGGER = "runtime_worker.handlers.run"

    #: Arguments the tool was actually invoked with, per test.
    invoked_with: list[dict[str, Any]]

    @staticmethod
    def settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    @staticmethod
    def agent_factory(*, context, dependencies: RuntimeDependencies) -> RuntimeHarness:
        return RuntimeHarness(
            agent=object(),
            context=context,
            dependencies=dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )

    @classmethod
    def tool_invoker(cls, *, fail: bool = False):
        """Return an invoker that drives a real graph with the real middleware.

        Only the MODEL is a fake. The middleware is the shipped
        :class:`RuntimeControlMiddleware`, installed where
        ``execution/factory.py:565`` installs it — first, so it is the outermost
        ``wrap_tool_call`` wrapper and the hook seam runs where it really runs.
        """

        recorded: list[dict[str, Any]] = []
        cls.invoked_with = recorded

        def observed_tool(value: str) -> str:
            """Record the arguments it was handed and return fixed text."""

            recorded.append({"value": value})
            if fail:
                raise ValueError("tool refused")
            return TOOL_RESULT

        graph = create_agent(
            model=_ToolCallingModel(responses=["unused"]),
            tools=[StructuredTool.from_function(func=observed_tool, name=TOOL_NAME)],
            middleware=[RuntimeControlMiddleware()],
        )

        async def invoke(_harness, _messages: Sequence[object]):
            _ToolCallingModel.seen = []
            await graph.ainvoke({"messages": [("user", "call the tool")]})
            return {"messages": [{"role": "assistant", "content": "Done."}]}

        return invoke

    @classmethod
    async def drive_run(cls, invoker) -> tuple[str, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = cls.settings()
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversation = await ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        ).create_conversation(
            CreateConversationRequest(
                org_id="org_123",
                user_id="user_123",
                assistant_id="assistant_123",
            )
        )
        created = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="A run whose tool calls must be observed.",
                model={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128_000,
                },
            )
        )
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=RuntimeRunHandler(
                persistence=store,
                event_store=store,
                agent_factory=cls.agent_factory,
                runtime_invoker=invoker,
            ),
        )
        assert await worker.run_until_idle() == 1
        return (created.run_id, store)

    @classmethod
    def tool_summaries(
        cls, caplog: pytest.LogCaptureFixture
    ) -> list[logging.LogRecord]:
        return [
            record
            for record in caplog.records
            if record.getMessage().startswith(cls.TOOL_SUMMARY_PREFIX)
        ]

    @classmethod
    def only_summary_fields(cls, caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
        summaries = cls.tool_summaries(caplog)
        assert len(summaries) == 1, f"expected one tool summary, got {len(summaries)}"
        return summaries[0].args[1]


class TestTheProductRegistersTheBuiltin(WorkerToolRunMixin):
    """The registration itself, from the product rather than from a test."""

    def test_building_a_run_handler_installs_the_builtin(self) -> None:
        assert RuntimeHooks.snapshot().for_phase(HookPhase.TOOL_EXECUTE_BEFORE) == ()

        RuntimeRunHandler(
            persistence=InMemoryRuntimeApiStore(),
            event_store=InMemoryRuntimeApiStore(),
        )

        for phase in (HookPhase.TOOL_EXECUTE_BEFORE, HookPhase.TOOL_EXECUTE_AFTER):
            names = [hook.name for hook in RuntimeHooks.snapshot().for_phase(phase)]
            assert names == [HOOK_NAME]

    def test_installing_twice_is_a_no_op_rather_than_a_raise(self) -> None:
        assert install_builtin_hooks() is True
        assert install_builtin_hooks() is False
        assert (
            len(RuntimeHooks.snapshot().for_phase(HookPhase.TOOL_EXECUTE_BEFORE)) == 1
        )


class TestObservedOnARealRun(WorkerToolRunMixin):
    """The failing-without-the-change assertion.

    With an empty registration table — production's state before this change —
    ``HookDispatch.enabled`` is False on both tool phases, the middleware takes
    its early return, no ledger is bound, and no summary is logged. Every
    assertion below is zero.
    """

    async def test_a_tool_call_lands_in_the_runs_tool_summary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=self.RUN_LOGGER):
            run_id, store = await self.drive_run(self.tool_invoker())

        assert store.runs[run_id].status == AgentRunStatus.COMPLETED
        fields = self.only_summary_fields(caplog)
        assert fields["tool_calls"] == 1
        assert fields["tool_failures"] == 0
        assert fields["tool_unsettled"] == 0
        assert fields["tool_untimed"] == 0
        assert fields["tool_result_chars"] == len(TOOL_RESULT)
        assert set(fields["tool_by_name"]) == {TOOL_NAME}
        assert fields["tool_by_name"][TOOL_NAME]["calls"] == 1
        # Wall time is measured, not defaulted: the before-hook stamped a start
        # and the after-hook found it.
        assert fields["tool_by_name"][TOOL_NAME]["duration_us"] >= 0
        assert fields["tool_by_name"][TOOL_NAME]["result_chars"] == len(TOOL_RESULT)

    async def test_a_tool_that_raises_is_reported_unsettled_at_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A raising tool never reaches ``tool.execute.after``, and says so.

        ``wrap_tool_call`` applies the result hooks on its return path, so an
        exception that escapes the tool skips them. That asymmetry is real and
        is exactly what ``unsettled`` exists to surface: the alternative is a
        started call that silently vanishes from the tally.
        """

        with caplog.at_level(logging.INFO, logger=self.RUN_LOGGER):
            run_id, store = await self.drive_run(self.tool_invoker(fail=True))

        assert store.runs[run_id].status == AgentRunStatus.FAILED
        summaries = self.tool_summaries(caplog)
        assert len(summaries) == 1
        assert summaries[0].levelno == logging.WARNING
        fields = summaries[0].args[1]
        assert fields["tool_calls"] == 0
        assert fields["tool_unsettled"] == 1
        # The tool's own message is not an operator-log fact.
        assert "tool refused" not in summaries[0].getMessage()

    async def test_an_error_result_is_counted_as_a_failure(self) -> None:
        """The other half, driven through the shipped middleware method.

        ``awrap_tool_call`` is what LangChain calls; only the handler it wraps
        is a stand-in here, because making LangGraph's ``ToolNode`` produce an
        error ``ToolMessage`` rather than re-raise is a framework configuration
        question and not the thing under test.
        """

        RuntimeRunHandler(
            persistence=InMemoryRuntimeApiStore(),
            event_store=InMemoryRuntimeApiStore(),
        )
        hook_token = RuntimeHookContext.bind_for_run()
        ledger = ToolCallObservationLedger()
        ledger_token = ToolCallObservationContext.bind_for_run(ledger)
        try:

            async def refuse(request) -> ToolMessage:
                return ToolMessage(
                    content="refused",
                    tool_call_id=request.tool_call["id"],
                    status="error",
                )

            await RuntimeControlMiddleware().awrap_tool_call(
                ToolCallRequest(
                    tool_call={
                        "name": TOOL_NAME,
                        "args": {"value": "raw"},
                        "id": "call-observed-1",
                        "type": "tool_call",
                    },
                    tool=None,
                    state={},
                    runtime=cast(Any, SimpleNamespace(config={})),
                ),
                refuse,
            )
        finally:
            ToolCallObservationContext.unbind(ledger_token)
            RuntimeHookContext.unbind(hook_token)

        summary = ledger.summary()
        assert summary is not None
        assert summary.calls == 1
        assert summary.failures == 1
        assert summary.untimed == 0

    async def test_a_run_that_calls_no_tool_logs_no_tool_summary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def direct_answer(_harness, _messages: Sequence[object]):
            return {"messages": [{"role": "assistant", "content": "Done."}]}

        with caplog.at_level(logging.INFO, logger=self.RUN_LOGGER):
            await self.drive_run(direct_answer)

        assert not self.tool_summaries(caplog)


class TestTheBuiltinCannotWiden(WorkerToolRunMixin):
    """Non-widening, asserted on the behaviour rather than on the docstring."""

    async def test_arguments_and_results_reach_the_graph_unchanged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=self.RUN_LOGGER):
            await self.drive_run(self.tool_invoker())

        # The tool ran (no veto) with the model's own arguments (no rewrite)...
        assert self.invoked_with == [{"value": "raw"}]
        # ...and the model saw the tool's own text (no result rewrite).
        assert _ToolCallingModel.seen == [TOOL_RESULT]

    def test_both_handlers_return_none_whatever_they_are_handed(self) -> None:
        """``None`` is what ``HookDispatch._invoke`` reads as "declined to act".

        A handler can only veto, rewrite arguments, or rewrite a result by
        RETURNING an outcome object. These two never return anything, with a
        ledger bound and without.
        """

        before = ToolExecuteBeforeInput(
            tool_name=TOOL_NAME,
            tool_call_id="call-1",
            execution_scope="supervisor",
            arguments={"value": "raw"},
        )
        after = ToolExecuteAfterInput(
            tool_name=TOOL_NAME,
            tool_call_id="call-1",
            execution_scope="supervisor",
            arguments={"value": "raw"},
            result_text=TOOL_RESULT,
            succeeded=True,
        )

        assert _observe_tool_start(before) is None
        assert _observe_tool_end(after) is None

        token = ToolCallObservationContext.bind_for_run()
        try:
            assert _observe_tool_start(before) is None
            assert _observe_tool_end(after) is None
        finally:
            ToolCallObservationContext.unbind(token)

    def test_an_unbound_ledger_makes_the_handlers_inert_not_broken(self) -> None:
        assert ToolCallObservationContext.current() is None
        _observe_tool_start(
            ToolExecuteBeforeInput(
                tool_name=TOOL_NAME,
                tool_call_id="call-1",
                execution_scope="supervisor",
            )
        )
        _observe_tool_end(
            ToolExecuteAfterInput(
                tool_name=TOOL_NAME,
                tool_call_id="call-1",
                execution_scope="supervisor",
                result_text=None,
            )
        )


class TestLedgerAccounting:
    """The tally's edges, which a happy-path run cannot reach."""

    @staticmethod
    def ledger() -> ToolCallObservationLedger:
        return ToolCallObservationLedger()

    def test_an_empty_ledger_summarizes_to_none(self) -> None:
        assert self.ledger().summary() is None

    def test_a_call_that_never_settles_is_reported_unsettled(self) -> None:
        ledger = self.ledger()
        ledger.start(execution_scope="supervisor", tool_call_id="call-1")

        summary = ledger.summary()

        assert summary is not None
        assert summary.calls == 0
        assert summary.unsettled == 1

    def test_a_settle_without_a_start_is_counted_untimed(self) -> None:
        ledger = self.ledger()
        ledger.settle(
            execution_scope="supervisor",
            tool_call_id="call-1",
            tool_name=TOOL_NAME,
            succeeded=True,
            result_chars=10,
        )

        summary = ledger.summary()

        assert summary is not None
        assert summary.calls == 1
        assert summary.untimed == 1
        assert summary.total_duration_us == 0

    def test_scope_keeps_two_identical_call_ids_apart(self) -> None:
        ledger = self.ledger()
        ledger.start(execution_scope="supervisor", tool_call_id="call-1")
        ledger.start(execution_scope="subagent:researcher", tool_call_id="call-1")
        ledger.settle(
            execution_scope="supervisor",
            tool_call_id="call-1",
            tool_name=TOOL_NAME,
            succeeded=True,
            result_chars=0,
        )

        summary = ledger.summary()

        assert summary is not None
        assert summary.calls == 1
        assert summary.untimed == 0
        assert summary.unsettled == 1

    def test_tool_names_are_bounded_into_an_other_bucket(self) -> None:
        ledger = self.ledger()
        for index in range(300):
            ledger.settle(
                execution_scope="supervisor",
                tool_call_id=f"call-{index}",
                tool_name=f"tool_{index}",
                succeeded=True,
                result_chars=1,
            )

        summary = ledger.summary()

        assert summary is not None
        assert summary.calls == 300
        assert len(summary.by_tool) <= 257
        assert "other" in {observation.tool_name for observation in summary.by_tool}


class TestRunHandlerBindsAndUnbinds(WorkerToolRunMixin):
    """The binding is run-scoped: nothing leaks past the run that owns it."""

    async def test_the_ledger_and_hook_session_are_unbound_after_the_run(
        self,
    ) -> None:
        assert ToolCallObservationContext.current() is None

        await self.drive_run(self.tool_invoker())

        assert ToolCallObservationContext.current() is None
        assert RuntimeHookContext.current() is None
