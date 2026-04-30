from __future__ import annotations

import asyncio
from collections.abc import Sequence

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRunCommand,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker
from runtime_worker.stream_events import StreamNamespace


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


def _create_queued_run(
    store: InMemoryRuntimeApiStore, settings: RuntimeSettings
) -> str:
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


def test_stream_namespace_parses_documented_deep_agents_subagent_segments() -> None:
    main = StreamNamespace.from_value(())
    subagent = StreamNamespace.from_value(("tools:task_123", "model_request:req_456"))
    unsupported = StreamNamespace.from_value(("research_subagent",))

    assert main.is_subagent is False
    assert main.subagent_task_id is None
    assert subagent.is_subagent is True
    assert subagent.subagent_task_id == "task_123"
    assert unsupported.is_subagent is False
    assert unsupported.subagent_task_id is None


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

    async def fake_invoker(
        _harness: RuntimeHarness, messages: Sequence[object]
    ) -> object:
        seen_messages.append(messages)
        return {
            "messages": [{"role": "assistant", "content": "Hello from the worker."}]
        }

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
        yield {
            "type": "messages",
            "ns": (),
            "data": (FakeChunk([{"type": "text", "text": "Hello"}]), {}),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (FakeChunk([{"type": "text", "text": "\n"}]), {}),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (FakeChunk([{"type": "text", "text": " there"}]), {}),
        }
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "model": {
                    "messages": [
                        "content='Hello there' usage_metadata={'input_token_details': {'cache_read': 1}}"
                    ]
                }
            },
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": [{"role": "assistant", "content": "Hello\n there"}]},
        }

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
        "model_delta",
        "final_response",
        "run_completed",
    ]
    model_delta_events = [
        event for event in events if event.event_type == "model_delta"
    ]
    assert [event.payload for event in model_delta_events] == [
        {"delta": "Hello", "message": "Hello"},
        {"delta": "\n", "message": "\n"},
        {"delta": " there", "message": " there"},
    ]
    assert [event.summary for event in model_delta_events] == ["Hello", None, "there"]
    assert "progress" not in [event.event_type for event in events]
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[0].content_text == "Hello\n there"


def test_runtime_worker_persists_mcp_auth_required_event() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings()
    run_id = _create_queued_run(store, settings)

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
        yield {
            "type": "custom",
            "ns": (),
            "data": {
                "api_event_type": "mcp_auth_required",
                "server_id": "server_123",
                "server_name": "drive_mcp",
                "display_name": "Drive MCP",
                "auth_url": "https://mcp.example.com/oauth/authorize",
                "expires_at": "2026-04-30T18:30:00+00:00",
                "message": "Authenticate Drive MCP to continue.",
            },
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [{"role": "assistant", "content": "Please authenticate."}]
            },
        }

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
    auth_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "mcp_auth_required"
    ]
    assert auth_events[0].source == "mcp"
    assert (
        auth_events[0].payload["auth_url"] == "https://mcp.example.com/oauth/authorize"
    )


def test_runtime_worker_persists_normalized_activity_stream_events() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings()
    run_id = _create_queued_run(store, settings)

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
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "model_request": {
                    "messages": [
                        {
                            "tool_calls": [
                                {
                                    "name": "task",
                                    "id": "task_abc",
                                    "args": {
                                        "subagent_type": "researcher",
                                        "description": "Research launch risks.",
                                    },
                                }
                            ]
                        }
                    ]
                }
            },
        }
        yield {
            "type": "updates",
            "ns": ("tools:task_abc", "model_request:req_456"),
            "data": {"model_request": {"messages": [{"content": "Reading sources."}]}},
        }
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "tools": {
                    "messages": [
                        {
                            "type": "tool",
                            "name": "task",
                            "tool_call_id": "task_abc",
                            "content": "Research complete.",
                        }
                    ]
                }
            },
        }
        yield {
            "type": "custom",
            "ns": (),
            "data": {
                "api_event_type": "reasoning_summary_delta",
                "summary": "Checking source coverage",
                "delta": "Checking source coverage",
                "raw_thought": "private hidden reasoning",
            },
        }
        yield {
            "type": "custom",
            "ns": ("tools:task_123",),
            "data": {
                "api_event_type": "subagent_started",
                "task_id": "task_123",
                "subagent_name": "researcher",
                "status": "started",
                "summary": "Researcher is reading sources.",
            },
        }
        yield {
            "type": "custom",
            "ns": ("tools:task_123",),
            "data": {
                "api_event_type": "reasoning_summary_delta",
                "summary": "Researcher is comparing source confidence.",
                "delta": "Comparing source confidence",
            },
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "name": "doc_search",
                            "id": "call_123",
                            "args": {
                                "query": "launch risks",
                                "authorization": "bearer secret-token",
                            },
                        },
                    ),
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "type": "tool",
                    "name": "doc_search",
                    "tool_call_id": "call_123",
                    "content": "Found two launch risks.",
                },
                {},
            ),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [{"role": "assistant", "content": "Two risks found."}]
            },
        }

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
    event_types = [event.event_type for event in events]
    assert "reasoning_summary_delta" in event_types
    assert "subagent_started" in event_types
    assert "subagent_completed" in event_types
    assert "tool_call_started" in event_types
    assert "tool_result" in event_types
    assert "tool_call_completed" in event_types
    reasoning_event = next(
        event for event in events if event.event_type == "reasoning_summary_delta"
    )
    assert reasoning_event.payload == {
        "summary": "Checking source coverage",
        "delta": "Checking source coverage",
    }
    assert "private hidden reasoning" not in reasoning_event.model_dump_json()
    tool_event = next(
        event for event in events if event.event_type == "tool_call_started"
    )
    assert tool_event.payload["args"]["authorization"] == "[redacted]"
    assert tool_event.span_id == "call_123"
    subagent_event = next(
        event
        for event in events
        if event.event_type == "subagent_started" and event.task_id == "task_123"
    )
    assert subagent_event.task_id == "task_123"
    assert subagent_event.subagent_id == "researcher"
    subagent_reasoning_event = next(
        event
        for event in events
        if event.event_type == "reasoning_summary_delta"
        and event.parent_task_id == "task_123"
    )
    assert subagent_reasoning_event.source == "subagent"
    task_started = next(
        event
        for event in events
        if event.event_type == "subagent_started" and event.task_id == "task_abc"
    )
    assert task_started.subagent_id == "researcher"
    task_progress = next(
        event
        for event in events
        if event.event_type == "subagent_progress" and event.task_id == "task_abc"
    )
    assert task_progress.parent_task_id == "task_abc"
    task_completed = next(
        event
        for event in events
        if event.event_type == "subagent_completed" and event.task_id == "task_abc"
    )
    assert task_completed.summary == "Research complete."


def test_runtime_worker_collapses_incremental_tool_chunks_to_stable_activity() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _settings()
    run_id = _create_queued_run(store, settings)

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
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "name": "write_todos",
                            "id": "call_123",
                            "index": 0,
                            "args": {"delta": ""},
                        },
                    )
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "index": 0,
                            "args": {
                                "delta": '{"todos":[{"content":"check prime helper"'
                            },
                        },
                    )
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "index": 0,
                            "args": {"delta": ',"status":"pending"}]}'},
                        },
                    )
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "type": "tool",
                    "name": "write_todos",
                    "tool_call_id": "call_123",
                    "content": "Updated todo list.",
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "name": "task",
                            "id": "task_123",
                            "index": 0,
                            "args": {"delta": ""},
                        },
                    )
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "index": 0,
                            "args": {
                                "delta": '{"description":"Write prime code","subagent_type":"coder"}'
                            },
                        },
                    )
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "type": "tool",
                    "name": "task",
                    "tool_call_id": "task_123",
                    "content": "Prime code written.",
                },
                {},
            ),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": [{"role": "assistant", "content": "Done."}]},
        }

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
    assert "unknown_tool" not in " ".join(str(event.payload) for event in events)
    tool_events = [
        event
        for event in events
        if event.event_type
        in {
            "tool_call_started",
            "tool_call_delta",
            "tool_result",
            "tool_call_completed",
        }
    ]
    assert {event.payload["tool_name"] for event in tool_events} == {"write_todos"}
    assert {event.payload["call_id"] for event in tool_events} == {"call_123"}
    assert any(
        event.event_type == "subagent_started" and event.task_id == "task_123"
        for event in events
    )
    assert any(
        event.event_type == "subagent_completed" and event.task_id == "task_123"
        for event in events
    )


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
