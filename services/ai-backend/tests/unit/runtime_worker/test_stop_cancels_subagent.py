"""Stop must actually stop — including a subagent already executing in-process.

Three properties, each proven where production runs it rather than by calling a
handler directly:

1. **The cancel command is reachable.**  ``run_forever`` is the loop the
   in-process (desktop) worker and the standalone worker both enter.  A cancel
   command that cannot be *claimed* until the run it cancels has finished is not
   a slow cancel, it is no cancel at all, and every handler-level proof of
   cancellation is vacuous above it.

2. **Cancellation reaches the running graph.**  Subagents are in-process: the
   ``task`` tool awaits the child graph inside the parent's tool call, so the
   only thing that stops a subagent is stopping the coroutine executing the run.

3. **The subagent's card closes inside the sealed prefix.**  A run whose
   ``subagent_started`` never gets a terminal sibling leaves the cockpit showing
   "1 live" forever, because the stream closes on the run's terminal event and
   nothing after it is ever delivered.

The one substitution is the concrete chat model (the repository's env-gated
deterministic fake), wrapped so the *subagent's* model call blocks.  The
supervisor, the ``task`` tool, the Deep Agents graph, the streaming executor,
the worker loop, and the real cancel handler are all the production objects.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.fake_model import DeterministicFakeChatModel
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CancelRunRequest,
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
    RuntimeCancelCommand,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.handlers.cancel import RuntimeCancelHandler
from runtime_worker.loop import RuntimeWorker
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor


_ORG = "org_123"
_USER = "user_123"


class WorkerLoopMixin:
    """Build an in-memory store, a real worker, and drive its real loop."""

    #: Short enough that a genuinely-reachable cancel is observed promptly, long
    #: enough that the assertion is about reachability rather than about racing
    #: the poll.
    POLL_SECONDS = 0.01

    @staticmethod
    def _settings(**overrides: str) -> RuntimeSettings:
        environ = {
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_RETRIES": "1",
            "RUNTIME_MAX_PARALLEL_RUNS": "2",
            "SURFACES_V2": "false",
        }
        environ.update(overrides)
        return RuntimeSettings.load(environ=environ)

    @staticmethod
    def _run_coordinator(
        store: InMemoryRuntimeApiStore, settings: RuntimeSettings
    ) -> RunCoordinator:
        return RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )

    @classmethod
    async def _enqueue_run(
        cls,
        store: InMemoryRuntimeApiStore,
        settings: RuntimeSettings,
        *,
        user_input: str = "Summarize launch risks.",
    ) -> str:
        run_coordinator = cls._run_coordinator(store, settings)
        conversation = await ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        ).create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant_123"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input=user_input,
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return response.run_id

    @staticmethod
    async def _await_event(
        store: InMemoryRuntimeApiStore,
        run_id: str,
        event_type: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        """Wait for one projected event type to land on the run's ledger."""

        async def _poll() -> None:
            while True:
                events = store.events_by_run.get(run_id, [])
                if any(event.event_type == event_type for event in events):
                    return
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=timeout)

    @staticmethod
    def _event_types(store: InMemoryRuntimeApiStore, run_id: str) -> list[str]:
        return [event.event_type for event in store.events_by_run.get(run_id, [])]

    @staticmethod
    async def _stop_loop(task: asyncio.Task[None]) -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class BlockingHandlerMixin:
    """Handler doubles for the loop-reachability proof.

    Deliberately not the real handlers: property (1) is about whether the loop
    can *reach* a cancel handler at all, and a real run handler would answer
    that question with model plumbing rather than with the loop.
    """

    class BlockingRunHandler:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.cancelled = False

        async def handle(self, command: object) -> None:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    class RecordingCancelHandler:
        def __init__(self) -> None:
            self.entered = asyncio.Event()

        async def handle(self, command: object) -> None:
            self.entered.set()


class BlockingSubagentModelMixin:
    """Script the real graph into one ``task`` dispatch whose child never returns.

    The block is keyed on the subagent's own prompt (its task description), so
    the supervisor's turn streams normally and only the child hangs — which is
    exactly the state the bug report describes: "Agents 1 live", card spinning.
    """

    SENTINEL = "SUBAGENT-BLOCK"

    @classmethod
    def install(cls, monkeypatch: pytest.MonkeyPatch) -> "_SubagentBlock":
        block = _SubagentBlock()
        original = DeterministicFakeChatModel._astream

        async def _astream(
            self: DeterministicFakeChatModel,
            messages: list[object],
            stop: list[str] | None = None,
            run_manager: object | None = None,
            **kwargs: object,
        ) -> AsyncIterator[object]:
            if cls.SENTINEL in DeterministicFakeChatModel._latest_human_text(
                cast_messages(messages)
            ):
                block.live.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    block.cancelled = True
                    raise
            async for chunk in original(self, messages, stop, run_manager, **kwargs):  # type: ignore[arg-type]
                yield chunk

        monkeypatch.setattr(DeterministicFakeChatModel, "_astream", _astream)
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_CALLS", "1")
        monkeypatch.setenv("RUNTIME_FAKE_MODEL_TOOL_NAME", "task")
        monkeypatch.setenv(
            "RUNTIME_FAKE_MODEL_TOOL_ARGS",
            json.dumps(
                {
                    "description": f"{cls.SENTINEL} research the launch risks",
                    "subagent_type": "general-purpose",
                }
            ),
        )
        return block


class _SubagentBlock:
    """Observation handles for the blocked subagent model call."""

    def __init__(self) -> None:
        self.live = asyncio.Event()
        self.cancelled = False


def cast_messages(messages: object) -> Sequence[object]:
    """Narrow the monkeypatched signature's messages back to a sequence."""

    return messages if isinstance(messages, Sequence) else ()


class TestCancelCommandReachability(WorkerLoopMixin, BlockingHandlerMixin):
    async def test_cancel_is_claimed_while_its_run_is_still_executing(self) -> None:
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._enqueue_run(store, settings)
        run_handler = self.BlockingRunHandler()
        cancel_handler = self.RecordingCancelHandler()
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            run_handler=run_handler,  # type: ignore[arg-type]
            cancel_handler=cancel_handler,  # type: ignore[arg-type]
        )
        loop_task = asyncio.create_task(
            worker.run_forever(poll_interval_seconds=self.POLL_SECONDS)
        )
        try:
            await asyncio.wait_for(run_handler.entered.wait(), timeout=5)
            await store.enqueue_cancel(
                RuntimeCancelCommand(
                    run_id=run_id,
                    org_id=_ORG,
                    requested_by_user_id=_USER,
                    reason="user pressed stop",
                )
            )
            await asyncio.wait_for(cancel_handler.entered.wait(), timeout=5)
        finally:
            run_handler.release.set()
            await self._stop_loop(loop_task)


class SubagentLedgerMixin:
    """A recording producer plus one fleet-of-one dispatch already on the wire."""

    class RecordingEventProducer:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def append_api_event(self, **kwargs: object) -> None:
            self.events.append(kwargs)

        def types(self) -> list[str]:
            return [str(event["event_type"].value) for event in self.events]  # type: ignore[union-attr]

    @staticmethod
    def run_record() -> RunRecord:
        return RunRecord(
            run_id="run_123",
            conversation_id="conversation_123",
            org_id=_ORG,
            user_id=_USER,
            user_message_id="message_123",
            trace_id="trace_123",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                user_id=_USER,
                org_id=_ORG,
                roles=["employee"],
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                run_id="run_123",
                trace_id="trace_123",
            ),
        )

    @classmethod
    async def dispatch_one_subagent(
        cls,
        processor: StreamUpdateProcessor,
        run: RunRecord,
        *,
        call_id: str = "call_subagent_1",
    ) -> None:
        await processor.append_subagent_lifecycle_events(
            run=run,
            namespace=StreamNamespace(()),
            data={
                "messages": [
                    SimpleNamespace(
                        tool_call_chunks=[
                            {
                                "name": "task",
                                "id": call_id,
                                "args": json.dumps(
                                    {
                                        "subagent_type": "general-purpose",
                                        "description": "Research the launch risks.",
                                    }
                                ),
                            }
                        ]
                    )
                ]
            },
            metadata={},
        )


class TestOpenSubagentsCloseOnCancellation(SubagentLedgerMixin):
    async def test_an_open_child_gets_a_cancelled_terminal_frame_and_closes_its_fleet(
        self,
    ) -> None:
        producer = self.RecordingEventProducer()
        processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
        run = self.run_record()
        await self.dispatch_one_subagent(processor, run)
        assert producer.types() == ["subagent_fleet_started", "subagent_started"]

        await processor.close_open_subagents_as_cancelled(run=run)

        assert producer.types()[2:] == [
            "subagent_completed",
            "subagent_fleet_finished",
        ]
        started = producer.events[1]["payload"]
        terminal = producer.events[2]["payload"]
        assert isinstance(started, dict) and isinstance(terminal, dict)
        assert terminal["status"] == "cancelled"
        assert terminal["task_id"] == "call_subagent_1"
        # The terminal frame names the child the started frame named — the
        # cockpit keys its card on ``task_id`` but labels it by name.
        assert terminal["subagent_name"] == started["subagent_name"]

    async def test_closing_twice_emits_nothing_the_second_time(self) -> None:
        producer = self.RecordingEventProducer()
        processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
        run = self.run_record()
        await self.dispatch_one_subagent(processor, run)
        await processor.close_open_subagents_as_cancelled(run=run)
        emitted = len(producer.events)

        await processor.close_open_subagents_as_cancelled(run=run)

        assert len(producer.events) == emitted

    async def test_a_child_that_already_completed_is_left_alone(self) -> None:
        producer = self.RecordingEventProducer()
        processor = StreamUpdateProcessor(event_producer=producer)  # type: ignore[arg-type]
        run = self.run_record()
        await self.dispatch_one_subagent(processor, run)
        await processor.append_task_lifecycle_event(
            run=run,
            event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
            payload={
                "task_id": "call_subagent_1",
                "subagent_name": "general-purpose",
                "status": "completed",
            },
            metadata={},
        )
        emitted = len(producer.events)

        await processor.close_open_subagents_as_cancelled(run=run)

        assert len(producer.events) == emitted
        assert producer.types().count("subagent_completed") == 1


class TestCancelHandlerAcceptsTheStatusTheApiLeaves(WorkerLoopMixin):
    """The handler must act on ``cancelling``, which is the only status it sees.

    ``RunCoordinator.cancel_run`` flips the run to ``cancelling`` *before* it
    enqueues the command, so a guard that omits that member is not a narrow
    edge case — it rejects every cancel a user can produce. Building the run
    directly in ``running``, which every other cancel test does, cannot see it.
    """

    async def test_a_cancelling_run_reaches_a_terminal_cancelled_event(self) -> None:
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._enqueue_run(store, settings)
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.RUNNING)

        await self._run_coordinator(store, settings).cancel_run(
            org_id=_ORG,
            user_id=_USER,
            run_id=run_id,
            request=CancelRunRequest(requested_by_user_id=_USER),
        )
        assert store.runs[run_id].status is AgentRunStatus.CANCELLING

        await RuntimeCancelHandler(persistence=store, event_store=store).handle(
            RuntimeCancelCommand(
                run_id=run_id,
                org_id=_ORG,
                requested_by_user_id=_USER,
                reason="user pressed stop",
            )
        )

        assert store.runs[run_id].status is AgentRunStatus.CANCELLED
        assert "run_cancelled" in self._event_types(store, run_id)


class TestStopCancelsAnInFlightSubagent(WorkerLoopMixin, BlockingSubagentModelMixin):
    async def test_cancel_interrupts_the_subagent_and_closes_its_card(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        block = self.install(monkeypatch)
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._enqueue_run(store, settings)
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        loop_task = asyncio.create_task(
            worker.run_forever(poll_interval_seconds=self.POLL_SECONDS)
        )
        try:
            await asyncio.wait_for(block.live.wait(), timeout=20)
            await self._await_event(store, run_id, "subagent_started")

            await self._run_coordinator(store, settings).cancel_run(
                org_id=_ORG,
                user_id=_USER,
                run_id=run_id,
                request=CancelRunRequest(requested_by_user_id=_USER),
            )
            await self._await_event(store, run_id, "run_cancelled", timeout=15)
            # (2) Sampled here, not after the ``finally``. ``_stop_loop`` cancels
            # the loop task and with it the run, so a reading taken after
            # teardown is ``True`` even when Stop did nothing at all — the
            # assertion would pass for the wrong reason and this test would
            # certify a cancellation it never observed. Taking it inside the live
            # window is deterministic rather than racy: the cancel handler awaits
            # its drain *before* the terminal event, so a child that had not
            # unwound could not have produced the ``run_cancelled`` just awaited.
            stopped_while_the_worker_was_still_running = block.cancelled
        finally:
            await self._stop_loop(loop_task)

        # (2) The subagent stopped executing — it was interrupted, not left to
        # burn tokens behind a run the user believes is over.
        assert stopped_while_the_worker_was_still_running is True

        run = store.runs[run_id]
        assert run.status is AgentRunStatus.CANCELLED

        # (3) …and its card closed inside the sealed prefix, so a live client
        # actually receives the terminal frame.
        names = self._event_types(store, run_id)
        assert "subagent_completed" in names, names
        assert names.index("subagent_completed") < names.index("run_cancelled"), names
        cancelled_child = next(
            event
            for event in store.events_by_run[run_id]
            if event.event_type == "subagent_completed"
        )
        assert cancelled_child.status == "cancelled"
