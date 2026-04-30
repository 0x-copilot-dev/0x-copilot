from __future__ import annotations

import asyncio
from collections.abc import Sequence

from agent_runtime.agent.contracts import AgentRuntimeContext, RuntimeDependencies, RuntimeErrorCode
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest, RuntimeRunCommand
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker


def _settings(*, max_retries: int = 1, max_parallel_runs: int = 2) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-4.1-mini",
            "RUNTIME_MAX_RETRIES": str(max_retries),
            "RUNTIME_MAX_PARALLEL_RUNS": str(max_parallel_runs),
        }
    )


def _runtime_context(run_id: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_123",
        roles=["employee"],
        model_profile={
            "provider": "openai",
            "model_name": "gpt-4.1-mini",
            "max_input_tokens": 128000,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
        run_id=run_id,
        trace_id=f"trace_{run_id}",
    )


def _create_queued_run(store: InMemoryRuntimeApiStore, settings: RuntimeSettings) -> str:
    service = RuntimeApiService(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
    )
    conversation = service.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    response = service.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Summarize launch risks.",
            model={"provider": "openai", "model_name": "gpt-4.1-mini"},
        )
    )
    return response.run_id


def test_runtime_worker_processes_queued_run_with_fake_async_invoker() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings()
    run_id = _create_queued_run(store, settings)
    seen_messages: list[Sequence[object]] = []

    def fake_agent_factory(
        *,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
    ) -> RuntimeHarness:
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

    async def fake_invoker(_harness: RuntimeHarness, messages: Sequence[object]) -> object:
        seen_messages.append(messages)
        return {"messages": [{"role": "assistant", "content": "Hello from the worker."}]}

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_invoker=fake_invoker,
        ),
    )

    processed = asyncio.run(worker.run_until_idle())

    assert processed == 1
    assert store.runs[run_id].status == "completed"
    assert seen_messages[0][0]["content"] == "Summarize launch risks."
    assert [event.event_type for event in store.events_by_run[run_id]] == [
        "run_queued",
        "run_started",
        "final_response",
        "run_completed",
    ]
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[0].content_text == "Hello from the worker."


def test_runtime_worker_streams_model_deltas_before_final_response() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings()
    run_id = _create_queued_run(store, settings)

    class FakeChunk:
        def __init__(self, content: object) -> None:
            self.content = content

    def fake_agent_factory(
        *,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
    ) -> RuntimeHarness:
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

    async def fake_streamer(
        _harness: RuntimeHarness,
        _messages: Sequence[object],
    ):
        yield ("messages", (FakeChunk([{"type": "text", "text": "Hello"}]), {}))
        yield ("messages", (FakeChunk([{"type": "text", "text": " there"}]), {}))
        yield ("values", {"messages": [{"role": "assistant", "content": "Hello there"}]})

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_streamer=fake_streamer,
        ),
    )

    processed = asyncio.run(worker.run_until_idle())

    assert processed == 1
    events = store.events_by_run[run_id]
    assert [event.event_type for event in events] == [
        "run_queued",
        "run_started",
        "model_delta",
        "model_delta",
        "final_response",
        "run_completed",
    ]
    assert [event.payload for event in events if event.event_type == "model_delta"] == [
        {"delta": "Hello", "message": "Hello"},
        {"delta": " there", "message": " there"},
    ]
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[0].content_text == "Hello there"


def test_runtime_worker_retries_then_dead_letters_retryable_failures() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings(max_retries=1)
    command = RuntimeRunCommand(
        run_id="run_retry",
        conversation_id="conversation_123",
        org_id="org_123",
        user_id="user_123",
        trace_id="trace_retry",
        runtime_context=_runtime_context("run_retry"),
    )
    store.enqueue_run(command)

    class FailingRunHandler:
        attempts = 0

        async def handle(self, _command: RuntimeRunCommand) -> None:
            self.attempts += 1
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Fake retryable failure.",
                retryable=True,
            )

    handler = FailingRunHandler()
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        retry_delay_seconds=0,
        run_handler=handler,
    )

    assert asyncio.run(worker.run_once())
    assert asyncio.run(worker.run_once())
    assert not asyncio.run(worker.run_once())
    assert handler.attempts == 2


def test_runtime_worker_respects_max_parallel_runs() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings(max_parallel_runs=2)
    for run_id in ("run_1", "run_2"):
        store.enqueue_run(
            RuntimeRunCommand(
                run_id=run_id,
                conversation_id="conversation_123",
                org_id="org_123",
                user_id="user_123",
                trace_id=f"trace_{run_id}",
                runtime_context=_runtime_context(run_id),
            )
        )

    class SlowRunHandler:
        active = 0
        max_active = 0

        async def handle(self, _command: RuntimeRunCommand) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1

    handler = SlowRunHandler()
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=handler,
    )

    processed = asyncio.run(worker.run_until_idle())

    assert processed == 2
    assert handler.max_active == 2
