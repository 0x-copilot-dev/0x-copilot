from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.membership import InMemoryWorkspaceMembershipResolver
from agent_runtime.api.notifications import LoggingNotificationDispatcher
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.persistence.records import OutboxStatus
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRequest,
    ApprovalRequestRecord,
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
    RuntimeRunCommand,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.loop import RuntimeWorker
from runtime_worker.stream_parts import StreamNamespace


class _TestSettings:
    @staticmethod
    def create(*, max_retries: int = 1, max_parallel_runs: int = 2) -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": str(max_retries),
                "RUNTIME_MAX_PARALLEL_RUNS": str(max_parallel_runs),
            }
        )

    @staticmethod
    def runtime_context(run_id: str) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_123",
            org_id="org_123",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id=run_id,
            trace_id=f"trace_{run_id}",
        )


def _make_coordinators(
    store: InMemoryRuntimeApiStore,
    settings: RuntimeSettings,
) -> tuple[
    RunCoordinator, ConversationCoordinator, ApprovalCoordinator, RuntimeEventProducer
]:
    """Build the three core coordinators + event producer from an in-memory store."""
    model_resolver = ModelConfigResolver(settings)
    event_producer = RuntimeEventProducer(
        persistence=store,
        event_store=store,
        on_event_appended=None,
    )
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=event_producer,
        settings=settings,
        model_resolver=model_resolver,
    )
    conv_coordinator = ConversationCoordinator(
        persistence=store,
        settings=settings,
        run_coordinator=run_coordinator,
    )
    approval_coordinator = ApprovalCoordinator(
        persistence=store,
        queue=store,
        event_producer=event_producer,
        membership_resolver=InMemoryWorkspaceMembershipResolver(),
        notification_dispatcher=LoggingNotificationDispatcher(),
    )
    return run_coordinator, conv_coordinator, approval_coordinator, event_producer


class _TestHelpers:
    @staticmethod
    async def create_queued_run(
        store: InMemoryRuntimeApiStore,
        settings: RuntimeSettings,
        *,
        model: dict[str, object] | None = None,
    ) -> str:
        run_coordinator, conv_coordinator, _, _ = _make_coordinators(store, settings)
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id="org_123",
                user_id="user_123",
                assistant_id="assistant_123",
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id="org_123",
                user_id="user_123",
                user_input="Summarize launch risks.",
                model=model or {"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return response.run_id

    @staticmethod
    async def append_subagent_observation(
        event_producer: RuntimeEventProducer,
        store: InMemoryRuntimeApiStore,
        *,
        run_id: str,
        subagent_name: str = "general-purpose",
        task_id: str = "call_subagent_supervisor_123",
        objective: str = (
            "Research the launch risks for next quarter and report sources."
        ),
        completion_summary: str = (
            "Top three launch risks: payments outage, auth migration, "
            "compliance review with sources cited."
        ),
        visibility: RuntimeEventVisibility | None = None,
        redaction_state: RuntimeEventRedactionState | None = None,
    ) -> None:
        started_payload: dict[str, object] = {
            "task_id": task_id,
            "subagent_name": subagent_name,
            "status": "queued",
            "summary": objective,
        }
        if visibility is not None:
            started_payload["visibility"] = visibility.value
        if redaction_state is not None:
            started_payload["redaction_state"] = redaction_state.value
        await event_producer.append_api_event(
            run=store.runs[run_id],
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            payload=started_payload,
        )
        completed_payload: dict[str, object] = {
            "task_id": task_id,
            "subagent_name": subagent_name,
            "status": "completed",
            "summary": completion_summary,
        }
        if visibility is not None:
            completed_payload["visibility"] = visibility.value
        if redaction_state is not None:
            completed_payload["redaction_state"] = redaction_state.value
        await event_producer.append_api_event(
            run=store.runs[run_id],
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
            payload=completed_payload,
        )

    @staticmethod
    async def append_tool_observation(
        event_producer: RuntimeEventProducer,
        store: InMemoryRuntimeApiStore,
        *,
        run_id: str,
        tool_name: str = "jira_search",
        call_id: str = "call_123",
        args: dict[str, object] | None = None,
        output: dict[str, object] | None = None,
        visibility: RuntimeEventVisibility | None = None,
        redaction_state: RuntimeEventRedactionState | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "tool_name": tool_name,
            "call_id": call_id,
            "args": args or {"assignee": "me", "status": "open"},
        }
        if visibility is not None:
            payload["visibility"] = visibility.value
        if redaction_state is not None:
            payload["redaction_state"] = redaction_state.value
        await event_producer.append_api_event(
            run=store.runs[run_id],
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            payload=payload,
        )
        result_payload: dict[str, object] = {
            "tool_name": tool_name,
            "call_id": call_id,
            "output": output
            or {
                "issues": [
                    {"key": "AUTH-123", "priority": "P0"},
                    {"key": "PAY-88", "priority": "P1"},
                ]
            },
        }
        if visibility is not None:
            result_payload["visibility"] = visibility.value
        if redaction_state is not None:
            result_payload["redaction_state"] = redaction_state.value
        await event_producer.append_api_event(
            run=store.runs[run_id],
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload=result_payload,
        )


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


async def test_runtime_worker_processes_queued_run_with_fake_async_invoker() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)
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

    processed = await worker.run_until_idle()

    assert processed == 1
    assert store.runs[run_id].status == "completed"
    assert seen_messages[0][0]["content"] == "Summarize launch risks."
    assert [event.event_type for event in store.events_by_run[run_id]] == [
        "run_queued",
        "run_started",
        "model_call_started",
        "final_response",
        "run_completed",
    ]
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[0].content_text == "Hello from the worker."


async def test_runtime_worker_builds_history_from_selected_branch() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Original question",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_1",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="Original answer",
            parent_message_id=first.user_message_id,
        )
    )
    await store.append_message(
        MessageRecord(
            message_id="sibling_user",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            role=MessageRole.USER,
            content_text="Sibling branch that should not leak",
            parent_message_id=assistant.message_id,
        )
    )
    edited = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Edited question",
            parent_message_id=assistant.message_id,
            source_message_id="sibling_user",
            branch_id="branch_edit",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    command = store.run_commands[-1]
    messages = await handler._messages_for_run(command, store.runs[edited.run_id])

    message_prompts = [
        message["content"].split("\n\n", maxsplit=1)[0] for message in messages
    ]
    assert message_prompts == [
        "Original question",
        "Original answer",
        "Edited question",
    ]
    assert "Sibling branch that should not leak" not in messages[-1]["content"]


async def test_runtime_worker_resolves_live_assistant_parent_id_for_history() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Remember that the launch is on Tuesday.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="persisted_assistant_1",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="Got it. The launch is on Tuesday.",
            parent_message_id=first.user_message_id,
        )
    )

    follow_up = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="What day is the launch?",
            parent_message_id=f"assistant-{first.run_id}",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    assert (
        store.messages[follow_up.user_message_id].parent_message_id
        == assistant.message_id
    )
    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    command = store.run_commands[-1]
    messages = await handler._messages_for_run(command, store.runs[follow_up.run_id])

    assert [message["content"] for message in messages] == [
        "Remember that the launch is on Tuesday.",
        "Got it. The launch is on Tuesday.",
        "What day is the launch?",
    ]


async def test_runtime_worker_injects_prior_tool_observation_summaries() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="What are my open Jira blockers?",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_tool_observation(
        event_producer, store, run_id=first.run_id
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_with_jira_answer",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="AUTH-123 is the highest priority blocker.",
            parent_message_id=first.user_message_id,
        )
    )
    follow_up = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Which one is highest priority?",
            parent_message_id=assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    messages = await handler._messages_for_run(
        store.run_commands[-1],
        store.runs[follow_up.run_id],
    )

    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "system",
        "user",
    ]
    context = messages[-2]["content"]
    assert "Prior tool and subagent observations from earlier turns" in context
    assert "load_prior_tool_result" in context
    assert "jira_search" in context
    assert "AUTH-123" in context
    assert "Which one is highest priority?" == messages[-1]["content"]


async def test_create_run_response_returns_prior_run_ids_for_chain() -> None:
    """The run-create response surfaces prior run ids reached via the parent
    chain so on-call can correlate a turn back to the runs whose events
    shaped its prompt context."""

    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="What are my open Jira blockers?",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    assert first.prior_run_ids == ()
    first_assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_first",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="AUTH-123 is the highest priority blocker.",
            parent_message_id=first.user_message_id,
        )
    )
    second = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="And the medium-priority ones?",
            parent_message_id=first_assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    assert second.prior_run_ids == (first.run_id,)


async def test_runtime_worker_injects_prior_subagent_results_into_next_turn() -> None:
    """SUBAGENT_COMPLETED events from prior turns must surface in the next turn's
    prompt context so the model can reuse the subagent's research instead of
    re-dispatching."""

    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Call a research subagent on AI agents.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_subagent_observation(
        event_producer,
        store,
        run_id=first.run_id,
        subagent_name="general-purpose",
        objective="Research AI agents and report sources.",
        completion_summary=(
            "AI agents combine ReAct planning, tool use, and memory; key risks "
            "are hallucinations and tool misuse. Sources: openai.com, anthropic.com."
        ),
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_with_subagent_summary",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="Here is the report on AI agents.",
            parent_message_id=first.user_message_id,
        )
    )
    follow_up = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Based on the prior research, what are the top 3 risks?",
            parent_message_id=assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    messages = await handler._messages_for_run(
        store.run_commands[-1],
        store.runs[follow_up.run_id],
    )

    context = messages[-2]["content"]
    assert "Prior tool and subagent observations from earlier turns" in context
    assert "subagent:general-purpose" in context
    assert "hallucinations and tool misuse" in context
    assert "Research AI agents" in context
    assert "Reuse a prior subagent summary instead of re-dispatching" in context


async def test_runtime_worker_prior_tool_loader_returns_full_persisted_result() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Find blockers.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_tool_observation(
        event_producer, store, run_id=first.run_id
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_with_prior_tool",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="I found two blockers.",
            parent_message_id=first.user_message_id,
        )
    )
    follow_up = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Show full details for the first result.",
            parent_message_id=assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    command = store.run_commands[-1]
    run = store.runs[follow_up.run_id]
    index = await handler._tool_observation_index(command, run)
    dependencies = handler._dependencies_for_run(command, index)

    assert dependencies.prior_tool_result_loader is not None
    observation_id = index.observations[0].observation_id
    result = dependencies.prior_tool_result_loader.load_prior_tool_result(
        observation_id=observation_id,
        runtime_context=command.runtime_context,
    )
    missing = dependencies.prior_tool_result_loader.load_prior_tool_result(
        observation_id="obs_missing",
        runtime_context=command.runtime_context,
    )

    assert result["ok"] is True
    assert result["tool_name"] == "jira_search"
    assert result["result"]["output"]["issues"][0]["key"] == "AUTH-123"
    assert missing["ok"] is False
    assert missing["error_code"] == "observation_not_found"


async def test_runtime_worker_prior_tool_observations_are_branch_safe() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Find blockers.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_tool_observation(
        event_producer,
        store,
        run_id=first.run_id,
        output={"issues": [{"key": "AUTH-123"}]},
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_branch_root",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="AUTH-123 is blocking launch.",
            parent_message_id=first.user_message_id,
        )
    )
    sibling = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Different branch question.",
            parent_message_id=assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_tool_observation(
        event_producer,
        store,
        run_id=sibling.run_id,
        call_id="call_sibling",
        output={"issues": [{"key": "SIBLING-999"}]},
    )
    follow_up = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Continue from the original answer.",
            parent_message_id=assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    messages = await handler._messages_for_run(
        store.run_commands[-1],
        store.runs[follow_up.run_id],
    )
    context = messages[-2]["content"]

    assert "AUTH-123" in context
    assert "SIBLING-999" not in context


async def test_runtime_worker_skips_unsafe_prior_tool_observations() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    first = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Search sensitive logs.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_tool_observation(
        event_producer,
        store,
        run_id=first.run_id,
        visibility=RuntimeEventVisibility.INTERNAL,
        output={"secret": "internal-only"},
    )
    await _TestHelpers.append_tool_observation(
        event_producer,
        store,
        run_id=first.run_id,
        call_id="call_offloaded",
        redaction_state=RuntimeEventRedactionState.OFFLOADED,
        output={"payload_ref": "/large_tool_results/result_1"},
    )
    assistant = await store.append_message(
        MessageRecord(
            message_id="assistant_sensitive",
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            run_id=first.run_id,
            role=MessageRole.ASSISTANT,
            content_text="I checked the logs.",
            parent_message_id=first.user_message_id,
        )
    )
    follow_up = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="What did the logs say?",
            parent_message_id=assistant.message_id,
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    messages = await handler._messages_for_run(
        store.run_commands[-1],
        store.runs[follow_up.run_id],
    )

    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert "Prior tool observations" not in "\n".join(
        message["content"] for message in messages
    )


async def test_runtime_worker_excludes_current_run_tool_results_from_initial_prompt() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    current = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Search now.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    await _TestHelpers.append_tool_observation(
        event_producer,
        store,
        run_id=current.run_id,
        output={"issues": [{"key": "CURRENT-1"}]},
    )

    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    messages = await handler._messages_for_run(
        store.run_commands[-1],
        store.runs[current.run_id],
    )

    assert messages == ({"role": "user", "content": "Search now."},)


async def test_runtime_worker_includes_structured_composer_context() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_123",
            user_id="user_123",
            assistant_id="assistant_123",
        )
    )
    response = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id="org_123",
            user_id="user_123",
            user_input="Review the launch brief.",
            content=[
                {"type": "text", "text": "Review the launch brief."},
                {
                    "type": "document",
                    "filename": "launch-plan.md",
                    "mime_type": "text/markdown",
                    "text": "Launch plan risks",
                },
            ],
            attachments=[
                {
                    "id": "attachment_1",
                    "type": "document",
                    "name": "brief.txt",
                    "content_type": "text/plain",
                    "size": 12,
                    "content": [{"type": "text", "text": "Budget risk"}],
                }
            ],
            quote={"text": "quoted selection", "source": "assistant_1"},
            branch_id="branch_edit",
            branch={"replace_from_message_id": "assistant_old"},
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )

    handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=settings,
    )
    command = store.run_commands[-1]
    messages = await handler._messages_for_run(command, store.runs[response.run_id])
    content = messages[-1]["content"]

    assert "Review the launch brief." in content
    assert "Quoted context:\nquoted selection\nSource: assistant_1" in content
    assert "Structured content:\n- document launch-plan.md" in content
    assert "Launch plan risks" in content
    assert "Attachments:\n- brief.txt (text/plain, 12 bytes): Budget risk" in content
    assert "Branch metadata:" in content
    assert "- branch_id: branch_edit" in content
    assert "- replace_from_message_id: assistant_old" in content


async def test_runtime_worker_streams_model_deltas_before_final_response() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

    class FakeChunk:
        def __init__(
            self,
            content: object,
            usage_metadata: dict[str, object] | None = None,
        ) -> None:
            self.content = content
            self.usage_metadata = usage_metadata

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
            "data": (
                FakeChunk(
                    [{"type": "text", "text": " there"}],
                    usage_metadata={
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "total_tokens": 15,
                        "input_token_details": {"cache_read": 4},
                    },
                ),
                {},
            ),
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

    processed = await worker.run_until_idle()

    assert processed == 1
    events = store.events_by_run[run_id]
    assert [event.event_type for event in events] == [
        "run_queued",
        "run_started",
        "model_call_started",
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
    metrics = assistant_messages[0].metadata["performance_metrics"]
    assert metrics["chunk_count"] == 3
    assert metrics["usage"]["input"] == 12
    assert metrics["usage"]["output"] == 3
    assert metrics["usage"]["total"] == 15
    assert metrics["usage"]["cached_input"] == 4
    assert metrics["duration_ms"] >= 0
    assert metrics["first_chunk_ms"] >= 0
    assert assistant_messages[0].token_count == 3
    final_response = next(
        event for event in events if event.event_type == "final_response"
    )
    assert final_response.payload["performance_metrics"] == metrics
    run_completed = next(
        event for event in events if event.event_type == "run_completed"
    )
    assert run_completed.metadata["performance_metrics"]["chunk_count"] == 3


async def test_runtime_worker_completes_queue_item_when_stream_times_out() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(
        store,
        settings,
        model={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "timeout_seconds": 0.001,
        },
    )

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

    async def slow_streamer(
        _harness: RuntimeHarness,
        _messages: Sequence[object],
    ):
        await asyncio.sleep(0.05)
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": [{"role": "assistant", "content": "Too late"}]},
        }

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        retry_delay_seconds=0,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_streamer=slow_streamer,
        ),
    )

    processed = await worker.run_until_idle()

    assert processed == 1
    assert store.runs[run_id].status == AgentRunStatus.TIMED_OUT
    assert [event.event_type for event in store.events_by_run[run_id]] == [
        "run_queued",
        "run_started",
        "model_call_started",
        "run_failed",
    ]
    assert store.events_by_run[run_id][-1].summary == "Run timed out"
    assert set(store._queue_statuses.values()) == {OutboxStatus.COMPLETED}


async def test_runtime_worker_settles_inflight_tool_calls_on_run_timeout() -> None:
    """Run-level asyncio.timeout fires while a tool_call is in-flight without
    a matching tool_result. The handler must emit synthetic terminal
    `tool_result` + `tool_call_completed` events for the orphan call BEFORE
    `run_failed` so SSE consumers see lifecycle terminate top-down. The
    synthetic tool_result carries `status='timed_out'` and
    `error_code='tool_run_timeout'` so the frontend renders an error card."""

    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(
        store,
        settings,
        model={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "timeout_seconds": 0.05,
        },
    )

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

    async def streamer_with_orphan_tool(
        _harness: RuntimeHarness,
        _messages: Sequence[object],
    ):
        # First yield a tool_call_started chunk for `web_search`. The
        # streamer never yields the matching tool_result before the
        # outer asyncio.timeout fires.
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                SimpleNamespace(
                    tool_call_chunks=[
                        {
                            "name": "web_search",
                            "id": "call_orphan",
                            "index": 0,
                            "args": '{"query":"will hang"}',
                        }
                    ],
                    content="",
                ),
                {},
            ),
        }
        # Sleep past the run-level timeout so the call orphans.
        await asyncio.sleep(1.0)
        yield {"type": "values", "ns": (), "data": {"messages": []}}

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        retry_delay_seconds=0,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_streamer=streamer_with_orphan_tool,
        ),
    )

    await worker.run_until_idle()

    events = store.events_by_run[run_id]
    event_types = [event.event_type for event in events]
    # Event order is critical: tool lifecycle terminates BEFORE run lifecycle.
    assert event_types == [
        "run_queued",
        "run_started",
        "model_call_started",
        "tool_call_started",
        "tool_result",
        "tool_call_completed",
        "run_failed",
    ]
    # Synthetic tool_result from reconciliation carries timed_out status and
    # the typed error_code so the frontend renders a Failed card.
    synthetic_result = next(
        event for event in events if event.event_type == "tool_result"
    )
    assert synthetic_result.payload["status"] == "timed_out"
    assert synthetic_result.payload["error_code"] == "tool_run_timeout"
    assert synthetic_result.payload["call_id"] == "call_orphan"
    assert store.runs[run_id].status == AgentRunStatus.TIMED_OUT


async def test_runtime_worker_reconciles_deltas_with_final_stream_value() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
            "data": (FakeChunk("draft text "), {}),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (FakeChunk("that should be reconciled"), {}),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": [{"role": "assistant", "content": "Clean final."}]},
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

    processed = await worker.run_until_idle()

    assert processed == 1
    model_delta_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "model_delta"
    ]
    assert [event.payload["delta"] for event in model_delta_events] == [
        "draft text ",
        "that should be reconciled",
    ]
    final_response = next(
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "final_response"
    )
    assert final_response.payload["message"] == "Clean final."
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[0].content_text == "Clean final."


async def test_runtime_worker_does_not_merge_subagent_deltas_into_final_response() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
            "data": (FakeChunk("Main answer "), {}),
        }
        yield {
            "type": "messages",
            "ns": ("tools:task_prime",),
            "data": (FakeChunk("subagent-only text "), {}),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (FakeChunk("done."), {}),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [{"role": "assistant", "content": "Main answer done."}]
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

    processed = await worker.run_until_idle()

    assert processed == 1
    model_delta_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "model_delta"
    ]
    assert [event.payload["delta"] for event in model_delta_events] == [
        "Main answer ",
        "done.",
    ]
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[0].content_text == "Main answer done."


async def test_runtime_worker_streams_model_deltas_while_task_subagents_are_active() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
            "data": (
                {
                    "tool_call_chunks": (
                        {
                            "name": "task",
                            "id": "task_abc",
                            "args": {
                                "description": "Write prime code.",
                                "subagent_type": "coder",
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
            "data": (FakeChunk("interleaved subagent text"), {}),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "interleaved subagent text",
                    }
                ]
            },
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (
                {
                    "type": "tool",
                    "name": "task",
                    "tool_call_id": "task_abc",
                    "content": "Subagent answer.",
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "ns": (),
            "data": (FakeChunk("Clean final."), {}),
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

    processed = await worker.run_until_idle()

    assert processed == 1
    model_delta_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "model_delta"
    ]
    assert [event.payload["delta"] for event in model_delta_events] == [
        "interleaved subagent text",
        "Clean final.",
    ]
    final_response = next(
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "final_response"
    )
    assert final_response.payload["message"] == "Clean final."


async def test_runtime_worker_persists_mcp_auth_required_event_and_waits() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
                "approval_id": "mcp_auth_run_123_server_123",
                "action_id": "mcp_auth_run_123_server_123",
                "approval_kind": "mcp_auth",
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

    processed = await worker.run_until_idle()

    assert processed == 1
    auth_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "mcp_auth_required"
    ]
    final_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "final_response"
    ]
    assert store.runs[run_id].status == AgentRunStatus.WAITING_FOR_APPROVAL
    assert auth_events[0].source == "mcp"
    assert (
        auth_events[0].payload["auth_url"] == "https://mcp.example.com/oauth/authorize"
    )
    assert auth_events[0].payload["approval_id"] == "mcp_auth_run_123_server_123"
    approval = await store.get_approval_request(
        org_id="org_123",
        approval_id="mcp_auth_run_123_server_123",
    )
    assert approval is not None
    assert approval.metadata["approval_kind"] == "mcp_auth"
    assert approval.metadata["server_id"] == "server_123"
    assert final_events == []


async def test_runtime_worker_resolves_mcp_auth_action_and_completes_run() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)
    run = await store.update_run_status(
        run_id=run_id,
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
    )
    approval_id = "mcp_auth_run_123_server_123"
    await store.create_approval_request(
        record=ApprovalRequestRecord(
            approval_id=approval_id,
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            org_id=run.org_id,
            user_id=run.user_id,
            metadata={
                "approval_id": approval_id,
                "approval_kind": "mcp_auth",
                "server_id": "server_123",
                "server_name": "drive_mcp",
                "display_name": "Drive MCP",
            },
        )
    )
    run_coordinator, conv_coordinator, approval_coordinator, event_producer = (
        _make_coordinators(store, settings)
    )
    await approval_coordinator.record_approval_decision(
        org_id=run.org_id,
        approval_id=approval_id,
        request=ApprovalDecisionRequest(
            decision="approved",
            decided_by_user_id=run.user_id,
        ),
    )

    class NoopRunHandler:
        async def handle(self, _command: RuntimeRunCommand) -> None:
            return None

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

    async def fake_resumer(_harness: RuntimeHarness, resume: object):
        assert resume == {"approval_id": approval_id, "decision": "approved"}
        yield {
            "type": "values",
            "ns": (),
            "data": {
                "messages": [
                    {"role": "assistant", "content": "Drive MCP is connected."}
                ]
            },
        }

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=NoopRunHandler(),  # type: ignore[arg-type]
        approval_handler=RuntimeApprovalHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_resumer=fake_resumer,
        ),
    )

    processed = await worker.run_until_idle()

    assert processed == 2
    assert store.runs[run_id].status == AgentRunStatus.COMPLETED
    assistant_messages = [
        message for message in store.messages.values() if message.role == "assistant"
    ]
    assert assistant_messages[-1].content_text == "Drive MCP is connected."
    event_types = [event.event_type for event in store.events_by_run[run_id]]
    assert event_types.count("approval_resolved") == 1
    assert event_types.count("final_response") == 1
    assert event_types.count("run_completed") == 1


async def test_runtime_worker_persists_normalized_activity_stream_events() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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

    processed = await worker.run_until_idle()

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
    # P11.5: SSE/persistence paths no longer value-scrub. The
    # ``authorization`` key on tool args passes through whole;
    # tool emitters must not bake credentials into args. Logs are
    # the only filter point (via ``DENY_KEYS``).
    assert tool_event.payload["args"]["authorization"] == "bearer secret-token"
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
    task_progress = [
        event
        for event in events
        if event.event_type == "subagent_progress" and event.task_id == "task_abc"
    ]
    assert task_progress == []
    task_completed = next(
        event
        for event in events
        if event.event_type == "subagent_completed" and event.task_id == "task_abc"
    )
    assert task_completed.summary == "Research complete."


async def test_runtime_worker_collapses_incremental_tool_chunks_to_stable_activity() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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

    processed = await worker.run_until_idle()

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


async def test_runtime_worker_projects_call_mcp_tool_as_visible_tool_lifecycle() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
                            "name": "call_mcp_tool",
                            "id": "call_mcp_123",
                            "args": {
                                "server_name": "mcp_clickup_com",
                                "tool_name": "list_tasks",
                                "arguments": {"include_closed": True},
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
                    "name": "call_mcp_tool",
                    "tool_call_id": "call_mcp_123",
                    "content": "ClickUp returned two tasks.",
                },
                {},
            ),
        }
        yield {
            "type": "values",
            "ns": (),
            "data": {"messages": [{"role": "assistant", "content": "Two tasks."}]},
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

    processed = await worker.run_until_idle()

    assert processed == 1
    events = store.events_by_run[run_id]
    tool_events = [
        event
        for event in events
        if event.event_type
        in {"tool_call_started", "tool_result", "tool_call_completed"}
    ]
    assert [event.payload["tool_name"] for event in tool_events] == [
        "call_mcp_tool",
        "call_mcp_tool",
        "call_mcp_tool",
    ]
    assert {event.visibility for event in tool_events} == {"user"}
    assert tool_events[0].payload["args"]["server_name"] == "mcp_clickup_com"
    assert tool_events[1].payload["output"]["content"] == "ClickUp returned two tasks."


async def test_runtime_worker_persists_mcp_approval_requests_and_waits() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
                "__interrupt__": (
                    SimpleNamespace(
                        id="approval_mcp_123",
                        value={
                            "action_requests": [
                                {
                                    "name": "call_mcp_tool",
                                    "args": {
                                        "server_name": "mcp_clickup_com",
                                        "tool_name": "list_tasks",
                                        "arguments": {"assignee": "me"},
                                    },
                                }
                            ],
                            "review_configs": [
                                {
                                    "action_name": "call_mcp_tool",
                                    "allowed_decisions": [
                                        "approve",
                                        "edit",
                                        "reject",
                                    ],
                                }
                            ],
                        },
                    ),
                )
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

    processed = await worker.run_until_idle()

    assert processed == 1
    assert store.runs[run_id].status == AgentRunStatus.WAITING_FOR_APPROVAL
    approval = await store.get_approval_request(
        org_id="org_123",
        approval_id="approval_mcp_123",
    )
    assert approval is not None
    assert approval.metadata["approval_kind"] == "mcp_tool"
    assert approval.metadata["tool_name"] == "list_tasks"
    assert approval.metadata["native_interrupt_id"] == "approval_mcp_123"


async def test_runtime_worker_projects_native_mcp_interrupt_to_card_event() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)

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
                "__interrupt__": (
                    {
                        "id": "approval_native_123",
                        "value": {
                            "action_requests": [
                                {
                                    "name": "call_mcp_tool",
                                    "args": {
                                        "server_name": "mcp_clickup_com",
                                        "tool_name": "clickup_filter_tasks",
                                        "arguments": {"assignee": "me"},
                                    },
                                }
                            ],
                            "review_configs": [
                                {
                                    "action_name": "call_mcp_tool",
                                    "allowed_decisions": ["approve", "reject"],
                                }
                            ],
                        },
                    },
                )
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

    processed = await worker.run_until_idle()

    assert processed == 1
    assert store.runs[run_id].status == AgentRunStatus.WAITING_FOR_APPROVAL
    approval_events = [
        event
        for event in store.events_by_run[run_id]
        if event.event_type == "approval_requested"
    ]
    assert len(approval_events) == 1
    assert approval_events[0].payload["approval_id"] == "approval_native_123"
    assert approval_events[0].payload["tool_name"] == "clickup_filter_tasks"
    approval = await store.get_approval_request(
        org_id="org_123",
        approval_id="approval_native_123",
    )
    assert approval is not None
    assert approval.metadata["native_interrupt_id"] == "approval_native_123"
    assert approval.metadata["allowed_decisions"] == ["approve", "reject"]


async def test_runtime_worker_retries_then_dead_letters_retryable_failures() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create(max_retries=1)
    command = RuntimeRunCommand(
        run_id="run_retry",
        conversation_id="conversation_123",
        org_id="org_123",
        user_id="user_123",
        trace_id="trace_retry",
        runtime_context=_TestSettings.runtime_context("run_retry"),
    )
    await store.enqueue_run(command)

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

    assert await worker.run_once()
    assert await worker.run_once()
    assert not await worker.run_once()
    assert handler.attempts == 2


async def test_runtime_worker_respects_max_parallel_runs() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create(max_parallel_runs=2)
    for run_id in ("run_1", "run_2"):
        await store.enqueue_run(
            RuntimeRunCommand(
                run_id=run_id,
                conversation_id="conversation_123",
                org_id="org_123",
                user_id="user_123",
                trace_id=f"trace_{run_id}",
                runtime_context=_TestSettings.runtime_context(run_id),
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

    processed = await worker.run_until_idle()

    assert processed == 2
    assert handler.max_active == 2
