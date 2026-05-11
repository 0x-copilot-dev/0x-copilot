from __future__ import annotations


from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_worker.stream_events import StreamOrchestrator as RuntimeStreamPartAdapter


class RuntimeEventTimelineTestMixin:
    class Values:
        CALL_ID = "call_123"
        CONVERSATION_TITLE = "Launch review"
        RAW_THOUGHT = "private chain of thought that must not be exposed"
        SUBAGENT_NAME = "researcher"
        TASK_ID = "task_123"
        USER_INPUT = "Find launch risks."

    async def create_store_and_run(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> tuple[InMemoryRuntimeApiStore, RuntimeApiService, RunRecord]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        conversation = await service.create_conversation(
            CreateConversationRequest(
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
                title=self.Values.CONVERSATION_TITLE,
            )
        )
        run_response = await service.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
                user_input=self.Values.USER_INPUT,
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
                request_context={
                    "roles": sorted(runtime_context_admin.roles),
                    "permission_scopes": sorted(
                        runtime_context_admin.permission_scopes
                    ),
                    "connector_scopes": {
                        key: sorted(value)
                        for key, value in runtime_context_admin.connector_scopes.items()
                    },
                    "feature_flags": [
                        flag.value for flag in runtime_context_admin.feature_flags
                    ],
                },
            )
        )
        return store, service, store.runs[run_response.run_id]


class TestRuntimeEventTimeline(RuntimeEventTimelineTestMixin):
    async def test_tool_call_stream_event_projects_to_ui_lifecycle_envelope(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        store, service, run = await self.create_store_and_run(runtime_context_admin)

        await RuntimeStreamPartAdapter(service.event_producer).append_activity_events(
            run=run,
            chunk={
                "type": "messages",
                "ns": (),
                "data": (
                    {
                        "tool_call_chunks": (
                            {
                                "name": "doc_search",
                                "id": self.Values.CALL_ID,
                                "summary": "Searching launch docs",
                                "args": {
                                    "query": "launch risks",
                                    "authorization": "bearer secret-token",
                                },
                            },
                        ),
                    },
                    {},
                ),
            },
            delta=None,
        )
        envelope = store.events_by_run[run.run_id][-1]

        assert envelope.sequence_no == 2
        assert envelope.event_type is RuntimeApiEventType.TOOL_CALL_STARTED
        assert envelope.span_id == self.Values.CALL_ID
        assert envelope.display_title == "Calling doc_search"
        assert envelope.summary == "Searching launch docs"
        assert envelope.status == "started"
        # P11.5: event payloads pass through whole. Logs would filter
        # ``authorization`` via ``DENY_KEYS`` at emission time; the
        # event envelope itself carries the original value.
        assert envelope.payload["args"] == {
            "query": "launch risks",
            "authorization": "bearer secret-token",
        }
        assert store.runs[run.run_id].latest_sequence_no == 2

    async def test_tool_delta_and_result_events_project_to_specific_api_types(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        _store, service, run = await self.create_store_and_run(runtime_context_admin)

        await RuntimeStreamPartAdapter(service.event_producer).append_activity_events(
            run=run,
            chunk={
                "type": "custom",
                "ns": (),
                "data": {
                    "api_event_type": "tool_call_delta",
                    "tool_name": "doc_search",
                    "call_id": self.Values.CALL_ID,
                    "delta": "Searching",
                },
            },
            delta=None,
        )
        delta_envelope = _store.events_by_run[run.run_id][-1]
        await RuntimeStreamPartAdapter(service.event_producer).append_activity_events(
            run=run,
            chunk={
                "type": "messages",
                "ns": (),
                "data": (
                    {
                        "type": "tool",
                        "name": "doc_search",
                        "tool_call_id": self.Values.CALL_ID,
                        "content": "Found launch risks",
                    },
                    {},
                ),
            },
            delta=None,
        )
        result_envelope = next(
            event
            for event in _store.events_by_run[run.run_id]
            if event.event_type == "tool_result"
        )

        assert delta_envelope.event_type is RuntimeApiEventType.TOOL_CALL_DELTA
        assert delta_envelope.status == "running"
        assert delta_envelope.span_id == self.Values.CALL_ID
        assert result_envelope.event_type is RuntimeApiEventType.TOOL_RESULT
        assert result_envelope.status == "completed"
        assert result_envelope.display_title == "doc_search result"

    async def test_incremental_tool_call_chunks_keep_stable_tool_identity(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        store, service, run = await self.create_store_and_run(runtime_context_admin)
        adapter = RuntimeStreamPartAdapter(service.event_producer)

        for chunk in (
            {
                "name": "write_todos",
                "id": self.Values.CALL_ID,
                "index": 0,
                "args": {"delta": ""},
            },
            {
                "index": 0,
                "args": {"delta": '{"todos":[{"content":"check prime helper"'},
            },
            {"index": 0, "args": {"delta": ',"status":"pending"}]}'}},
        ):
            await adapter.append_activity_events(
                run=run,
                chunk={
                    "type": "messages",
                    "ns": (),
                    "data": ({"tool_call_chunks": (chunk,)}, {}),
                },
                delta=None,
            )

        await adapter.append_activity_events(
            run=run,
            chunk={
                "type": "messages",
                "ns": (),
                "data": (
                    {
                        "type": "tool",
                        "name": "write_todos",
                        "tool_call_id": self.Values.CALL_ID,
                        "content": "Updated todo list.",
                    },
                    {},
                ),
            },
            delta=None,
        )

        tool_events = [
            event
            for event in store.events_by_run[run.run_id]
            if event.event_type
            in {
                RuntimeApiEventType.TOOL_CALL_STARTED,
                RuntimeApiEventType.TOOL_CALL_DELTA,
                RuntimeApiEventType.TOOL_RESULT,
                RuntimeApiEventType.TOOL_CALL_COMPLETED,
            }
        ]
        assert [event.event_type for event in tool_events] == [
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        ]
        assert {event.payload["tool_name"] for event in tool_events} == {"write_todos"}
        assert {event.payload["call_id"] for event in tool_events} == {
            self.Values.CALL_ID
        }
        assert (
            "unknown_tool" not in store.events_by_run[run.run_id][-1].model_dump_json()
        )

    async def test_incremental_task_tool_chunks_project_to_subagent_lifecycle(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        store, service, run = await self.create_store_and_run(runtime_context_admin)
        adapter = RuntimeStreamPartAdapter(service.event_producer)

        for chunk in (
            {
                "name": "task",
                "id": self.Values.TASK_ID,
                "index": 0,
                "args": {"delta": ""},
            },
            {"index": 0, "args": {"delta": "{"}},
            {
                "index": 0,
                "args": {
                    "delta": '"description":"Write a prime checker.","subagent_type":"coder"}'
                },
            },
        ):
            await adapter.append_activity_events(
                run=run,
                chunk={
                    "type": "messages",
                    "ns": (),
                    "data": ({"tool_call_chunks": (chunk,)}, {}),
                },
                delta=None,
            )

        await adapter.append_activity_events(
            run=run,
            chunk={
                "type": "updates",
                "ns": (),
                "data": {
                    "model_request": {
                        "messages": [
                            {
                                "tool_calls": [
                                    {
                                        "name": "task",
                                        "id": self.Values.TASK_ID,
                                        "args": {
                                            "description": "Write a prime checker.",
                                            "subagent_type": "coder",
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                },
            },
            delta=None,
        )
        await adapter.append_activity_events(
            run=run,
            chunk={
                "type": "messages",
                "ns": (),
                "data": (
                    {
                        "type": "tool",
                        "name": "task",
                        "tool_call_id": self.Values.TASK_ID,
                        "content": "Prime checker written.",
                    },
                    {},
                ),
            },
            delta=None,
        )
        await adapter.append_activity_events(
            run=run,
            chunk={
                "type": "updates",
                "ns": (),
                "data": {
                    "tools": {
                        "messages": [
                            {
                                "type": "tool",
                                "name": "task",
                                "tool_call_id": self.Values.TASK_ID,
                                "content": "Prime checker written.",
                            }
                        ]
                    }
                },
            },
            delta=None,
        )

        activity_events = store.events_by_run[run.run_id]
        assert RuntimeApiEventType.TOOL_CALL_STARTED not in [
            event.event_type for event in activity_events
        ]
        subagent_started = next(
            event
            for event in activity_events
            if event.event_type is RuntimeApiEventType.SUBAGENT_STARTED
        )
        subagent_completed = next(
            event
            for event in activity_events
            if event.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
        )
        assert subagent_started.task_id == self.Values.TASK_ID
        assert subagent_started.subagent_id == "coder"
        assert subagent_started.summary == "Write a prime checker."
        assert subagent_completed.task_id == self.Values.TASK_ID
        assert subagent_completed.subagent_id == "coder"
        assert subagent_completed.status == "completed"
        assert [event.event_type for event in activity_events].count(
            RuntimeApiEventType.SUBAGENT_STARTED
        ) == 1
        assert [event.event_type for event in activity_events].count(
            RuntimeApiEventType.SUBAGENT_COMPLETED
        ) == 1

    async def test_reasoning_summary_event_does_not_expose_raw_thought_payload(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        _store, service, run = await self.create_store_and_run(runtime_context_admin)

        await RuntimeStreamPartAdapter(service.event_producer).append_activity_events(
            run=run,
            chunk={
                "type": "custom",
                "ns": (),
                "data": {
                    "api_event_type": "reasoning_summary",
                    "summary": "Checking source coverage",
                    "raw_thought": self.Values.RAW_THOUGHT,
                },
            },
            delta=None,
        )
        envelope = _store.events_by_run[run.run_id][-1]

        assert envelope.event_type is RuntimeApiEventType.REASONING_SUMMARY
        assert envelope.display_title == "Thinking"
        assert envelope.summary == "Checking source coverage"
        assert envelope.payload == {"summary": "Checking source coverage"}
        assert self.Values.RAW_THOUGHT not in envelope.model_dump_json()

    async def test_subagent_lifecycle_event_populates_task_span_fields(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        _store, service, run = await self.create_store_and_run(runtime_context_admin)

        await RuntimeStreamPartAdapter(service.event_producer).append_activity_events(
            run=run,
            chunk={
                "type": "custom",
                "ns": ("tools:parent_task_123",),
                "data": {
                    "api_event_type": "subagent_completed",
                    "task_id": self.Values.TASK_ID,
                    "subagent_name": self.Values.SUBAGENT_NAME,
                    "status": "completed",
                    "summary": "Researcher finished source review",
                },
            },
            delta=None,
        )
        envelope = _store.events_by_run[run.run_id][-1]

        assert envelope.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
        assert envelope.span_id == self.Values.TASK_ID
        assert envelope.parent_span_id == "parent_task_123"
        assert envelope.task_id == self.Values.TASK_ID
        assert envelope.subagent_id == self.Values.SUBAGENT_NAME
        assert envelope.display_title == "researcher subagent"
        assert envelope.status == "completed"
