"""Steering must reach a run that is *busy*, and reach it at a safe boundary.

Both halves are load bearing and neither implies the other.

**Reachability.** A steer that cannot be claimed until the run it steers has
finished is not a slow steer, it is no steer at all — and every seam-level proof
of delivery above it is vacuous. So the first test drives ``run_forever`` (the
loop both the standalone worker and the desktop's in-process worker enter) with
the execution width saturated, exactly as ``test_stop_cancels_subagent`` proves
it for Stop.

**Boundary.** The whole product claim is that a steer lands as context the model
reads *without* tearing down an in-flight tool call. That is a claim about which
graph seam drains the mailbox, so it is asserted at the seams: ``before_model``
drains, and the tool-call path does not — proven with a steer deposited while a
tool handler is mid-flight, which is the case the claim is about.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.execution.run_steering import (
    RunSteeringContext,
    RunSteeringInbox,
    SteeringMessage,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeSteerCommand,
)
from runtime_worker.handlers.steer import RuntimeSteerHandler
from runtime_worker.loop import RuntimeWorker


_ORG = "org_123"
_USER = "user_123"


class SteeringWorkerMixin:
    """An in-memory store, a real worker, and its real loop."""

    #: Short enough that a genuinely-reachable steer is observed promptly, long
    #: enough that the assertion is about reachability rather than poll racing.
    POLL_SECONDS = 0.01

    @staticmethod
    def settings(**overrides: str) -> RuntimeSettings:
        environ = {
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_RETRIES": "1",
            # One execution slot, deliberately. The claim is that a steer is
            # claimable while *every* slot is busy; with a width of one, one
            # blocked run is a fully saturated worker.
            "RUNTIME_MAX_PARALLEL_RUNS": "1",
            "SURFACES_V2": "false",
        }
        environ.update(overrides)
        return RuntimeSettings.load(environ=environ)

    @classmethod
    async def enqueue_run(
        cls, store: InMemoryRuntimeApiStore, settings: RuntimeSettings
    ) -> str:
        coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversation = await ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=coordinator
        ).create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant_123"
            )
        )
        response = await coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="Summarize launch risks.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return response.run_id

    @staticmethod
    def steer_command(
        run_id: str, text: str = "Focus on EU only."
    ) -> RuntimeSteerCommand:
        return RuntimeSteerCommand(
            run_id=run_id,
            org_id=_ORG,
            requested_by_user_id=_USER,
            steer=SteeringMessage(text=text, requested_by_user_id=_USER),
        )

    @staticmethod
    async def stop_loop(task: asyncio.Task[None]) -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    class BlockingRunHandler:
        """Occupies the single execution slot until released."""

        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(self, command: object) -> None:
            self.entered.set()
            await self.release.wait()

    class RecordingSteerHandler:
        """Answers only the reachability question, not the delivery one."""

        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.commands: list[RuntimeSteerCommand] = []

        async def handle(self, command: RuntimeSteerCommand) -> None:
            self.commands.append(command)
            self.entered.set()


class FakeRuntimeMixin:
    """The ``runtime`` object the middleware's scope resolver actually reads."""

    class FakeRuntime:
        def __init__(self, config: dict[str, object] | None = None) -> None:
            self.config = config or {}

    @classmethod
    def supervisor_runtime(cls) -> "FakeRuntimeMixin.FakeRuntime":
        # No supervisor task-call id in metadata ⇒ the supervisor's own graph.
        return cls.FakeRuntime({"metadata": {}})


class TestSteerCommandReachability(SteeringWorkerMixin):
    async def test_steer_is_claimed_while_every_execution_slot_is_busy(self) -> None:
        settings = self.settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self.enqueue_run(store, settings)
        run_handler = self.BlockingRunHandler()
        steer_handler = self.RecordingSteerHandler()
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=run_handler,  # type: ignore[arg-type]
            steer_handler=steer_handler,  # type: ignore[arg-type]
        )
        loop_task = asyncio.create_task(
            worker.run_forever(poll_interval_seconds=self.POLL_SECONDS)
        )
        try:
            await asyncio.wait_for(run_handler.entered.wait(), timeout=5)
            await store.enqueue_steer(self.steer_command(run_id))
            await asyncio.wait_for(steer_handler.entered.wait(), timeout=5)
        finally:
            run_handler.release.set()
            await self.stop_loop(loop_task)

        assert [command.steer.text for command in steer_handler.commands] == [
            "Focus on EU only."
        ]

    async def test_steer_reaches_the_mailbox_of_the_run_this_process_executes(
        self,
    ) -> None:
        """The real handler, joined to the run through the live-run registry.

        Reachability alone is not delivery: the claim above proves the loop
        entered *a* handler, this proves the handler found the executing run's
        own mailbox rather than dropping the message as a multi-worker miss.
        """

        settings = self.settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self.enqueue_run(store, settings)
        run_handler = self.BlockingRunHandler()
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=run_handler,  # type: ignore[arg-type]
        )
        loop_task = asyncio.create_task(
            worker.run_forever(poll_interval_seconds=self.POLL_SECONDS)
        )
        try:
            await asyncio.wait_for(run_handler.entered.wait(), timeout=5)
            inbox = worker.live_runs.steering_for(run_id)
            assert inbox is not None
            await store.enqueue_steer(self.steer_command(run_id, "Only EU markets."))

            async def _delivered() -> None:
                while inbox.pending == 0:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(_delivered(), timeout=5)
        finally:
            run_handler.release.set()
            await self.stop_loop(loop_task)

        assert [message.text for message in inbox.drain()] == ["Only EU markets."]

    async def test_steer_for_a_run_this_process_is_not_executing_is_a_miss(
        self,
    ) -> None:
        """A miss must be silent, not an exception the queue would replay.

        A raised steer is a retried steer, and a retried steer is one user
        message handed to the model twice.
        """

        settings = self.settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self.enqueue_run(store, settings)
        handler = RuntimeSteerHandler(persistence=store, live_runs=None)

        await handler.handle(self.steer_command(run_id))


class TestSteerDeliveryBoundary(FakeRuntimeMixin):
    """Which seam drains the mailbox — the whole "never mid-tool" claim."""

    STEER_TEXT = "Stop after the EU section."

    @staticmethod
    def tool_call_request() -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={
                "name": "write_todos",
                "args": {"items": ["one"]},
                "id": "call-1",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=cast(Any, object()),
        )

    def test_before_model_hands_the_steer_to_the_next_model_call(self) -> None:
        inbox = RunSteeringInbox()
        token = RunSteeringContext.bind_for_run(inbox)
        try:
            inbox.deposit(
                SteeringMessage(text=self.STEER_TEXT, requested_by_user_id=_USER)
            )
            update = RuntimeControlMiddleware().before_model(
                {}, self.supervisor_runtime()
            )
        finally:
            RunSteeringContext.unbind(token)

        messages = update["messages"]
        assert len(messages) == 1
        assert self.STEER_TEXT in messages[0].content
        assert SteeringMessage.Prompt.OPEN in messages[0].content
        # Consume-once: the message rides conversation state from here, and a
        # mailbox that re-served it would re-append the same interjection at
        # every subsequent turn of the run.
        assert inbox.pending == 0

    async def test_a_steer_deposited_mid_tool_call_waits_for_the_next_model_step(
        self,
    ) -> None:
        """The steer arrives at 40% of a tool call and does not disturb it.

        The deposit happens *inside* the tool handler — the literal case the
        design is about — so this asserts what a user watching a 30-second tool
        call actually gets: the call runs to completion with its result
        untouched, the message is still waiting when it settles, and the next
        ``before_model`` is what hands it over. Tearing an in-flight external
        effect down is Stop's job, and Stop already has it.
        """

        inbox = RunSteeringInbox()
        token = RunSteeringContext.bind_for_run(inbox)
        middleware = RuntimeControlMiddleware()
        settled: list[str] = []

        async def handler(request: ToolCallRequest) -> ToolMessage:
            inbox.deposit(
                SteeringMessage(text=self.STEER_TEXT, requested_by_user_id=_USER)
            )
            await asyncio.sleep(0)
            settled.append("tool ran to completion")
            return ToolMessage(content="todos written", tool_call_id="call-1")

        try:
            result = await middleware.awrap_tool_call(self.tool_call_request(), handler)
            assert settled == ["tool ran to completion"]
            assert result.content == "todos written"
            # The tool path never drains: the steer is exactly where it was.
            assert inbox.pending == 1

            update = middleware.before_model({}, self.supervisor_runtime())
        finally:
            RunSteeringContext.unbind(token)

        assert self.STEER_TEXT in update["messages"][0].content

    def test_a_subagent_model_step_never_takes_the_supervisor_s_steer(self) -> None:
        """The correction is aimed at the plan, and the plan is the supervisor's.

        A subagent inherits both this middleware and the run's context binding,
        so an unscoped drain would hand the user's course correction to whichever
        child reached a model step first — and the supervisor would never see it.
        """

        inbox = RunSteeringInbox()
        token = RunSteeringContext.bind_for_run(inbox)
        subagent_runtime = self.FakeRuntime(
            {"metadata": {"supervisor_task_call_id": "call_abc"}}
        )
        try:
            inbox.deposit(
                SteeringMessage(text=self.STEER_TEXT, requested_by_user_id=_USER)
            )
            subagent_update = RuntimeControlMiddleware().before_model(
                {}, subagent_runtime
            )
            assert "messages" not in subagent_update
            assert inbox.pending == 1

            supervisor_update = RuntimeControlMiddleware().before_model(
                {}, self.supervisor_runtime()
            )
        finally:
            RunSteeringContext.unbind(token)

        assert self.STEER_TEXT in supervisor_update["messages"][0].content

    def test_an_unsteered_run_adds_no_messages_at_all(self) -> None:
        """This seam is invisible to every run nobody is steering."""

        update = RuntimeControlMiddleware().before_model({}, self.supervisor_runtime())

        assert "messages" not in update
