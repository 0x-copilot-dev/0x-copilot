from __future__ import annotations

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings


class RuntimeEventTimelineTestMixin:
    class Values:
        CALL_ID = "call_123"
        CONVERSATION_TITLE = "Launch review"
        RAW_THOUGHT = "private chain of thought that must not be exposed"
        SUBAGENT_NAME = "researcher"
        TASK_ID = "task_123"
        USER_INPUT = "Find launch risks."

    def create_store_and_run(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> tuple[InMemoryRuntimeApiStore, RuntimeApiService, RunRecord]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-4.1-mini",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        conversation = service.create_conversation(
            CreateConversationRequest(
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
                title=self.Values.CONVERSATION_TITLE,
            )
        )
        run_response = service.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=runtime_context_admin.org_id,
                user_id=runtime_context_admin.user_id,
                user_input=self.Values.USER_INPUT,
                model={"provider": "openai", "model_name": "gpt-4.1-mini"},
                request_context={
                    "roles": sorted(runtime_context_admin.roles),
                    "permission_scopes": sorted(runtime_context_admin.permission_scopes),
                    "connector_scopes": {
                        key: sorted(value)
                        for key, value in runtime_context_admin.connector_scopes.items()
                    },
                    "feature_flags": [flag.value for flag in runtime_context_admin.feature_flags],
                },
            )
        )
        return store, service, store.runs[run_response.run_id]


class TestRuntimeEventTimeline(RuntimeEventTimelineTestMixin):
    def test_tool_call_stream_event_projects_to_ui_lifecycle_envelope(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        store, service, run = self.create_store_and_run(runtime_context_admin)

        envelope = service.event_producer.append_stream_event(
            run=run,
            stream_event=StreamEvent(
                source=StreamEventSource.TOOL,
                event_type=StreamEventType.TOOL_CALL,
                trace_id=runtime_context_admin.trace_id,
                payload={
                    "tool_name": "doc_search",
                    "call_id": self.Values.CALL_ID,
                    "summary": "Searching launch docs",
                    "args": {
                        "query": "launch risks",
                        "authorization": "bearer secret-token",
                    },
                },
            ),
        )

        assert envelope.sequence_no == 2
        assert envelope.event_type is RuntimeApiEventType.TOOL_CALL_STARTED
        assert envelope.span_id == self.Values.CALL_ID
        assert envelope.display_title == "Calling doc_search"
        assert envelope.summary == "Searching launch docs"
        assert envelope.status == "started"
        assert envelope.payload["args"] == {
            "query": "launch risks",
            "authorization": "[redacted]",
        }
        assert store.runs[run.run_id].latest_sequence_no == 2

    def test_tool_delta_and_result_events_project_to_specific_api_types(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        _store, service, run = self.create_store_and_run(runtime_context_admin)

        delta_envelope = service.event_producer.append_stream_event(
            run=run,
            stream_event=StreamEvent(
                source=StreamEventSource.TOOL,
                event_type=StreamEventType.CUSTOM,
                trace_id=runtime_context_admin.trace_id,
                payload={
                    "api_event_type": "tool_call_delta",
                    "tool_name": "doc_search",
                    "call_id": self.Values.CALL_ID,
                    "delta": "Searching",
                },
            ),
        )
        result_envelope = service.event_producer.append_stream_event(
            run=run,
            stream_event=StreamEvent(
                source=StreamEventSource.TOOL,
                event_type=StreamEventType.TOOL_RESULT,
                trace_id=runtime_context_admin.trace_id,
                payload={
                    "tool_name": "doc_search",
                    "call_id": self.Values.CALL_ID,
                    "status": "completed",
                    "output": {"message": "Found launch risks"},
                },
            ),
        )

        assert delta_envelope.event_type is RuntimeApiEventType.TOOL_CALL_DELTA
        assert delta_envelope.status == "running"
        assert delta_envelope.span_id == self.Values.CALL_ID
        assert result_envelope.event_type is RuntimeApiEventType.TOOL_RESULT
        assert result_envelope.status == "completed"
        assert result_envelope.display_title == "doc_search result"

    def test_reasoning_summary_event_does_not_expose_raw_thought_payload(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        _store, service, run = self.create_store_and_run(runtime_context_admin)

        envelope = service.event_producer.append_stream_event(
            run=run,
            stream_event=StreamEvent(
                source=StreamEventSource.MAIN_AGENT,
                event_type=StreamEventType.CUSTOM,
                trace_id=runtime_context_admin.trace_id,
                payload={
                    "api_event_type": "reasoning_summary",
                    "summary": "Checking source coverage",
                    "raw_thought": self.Values.RAW_THOUGHT,
                },
            ),
        )

        assert envelope.event_type is RuntimeApiEventType.REASONING_SUMMARY
        assert envelope.display_title == "Thinking"
        assert envelope.summary == "Checking source coverage"
        assert envelope.payload == {"summary": "Checking source coverage"}
        assert self.Values.RAW_THOUGHT not in envelope.model_dump_json()

    def test_subagent_lifecycle_event_populates_task_span_fields(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        _store, service, run = self.create_store_and_run(runtime_context_admin)

        envelope = service.event_producer.append_stream_event(
            run=run,
            stream_event=StreamEvent(
                source=StreamEventSource.SUBAGENT,
                event_type=StreamEventType.LIFECYCLE,
                trace_id=runtime_context_admin.trace_id,
                parent_task_id="parent_task_123",
                payload={
                    "task_id": self.Values.TASK_ID,
                    "subagent_name": self.Values.SUBAGENT_NAME,
                    "status": "completed",
                    "summary": "Researcher finished source review",
                },
            ),
        )

        assert envelope.event_type is RuntimeApiEventType.SUBAGENT_COMPLETED
        assert envelope.span_id == self.Values.TASK_ID
        assert envelope.parent_span_id == "parent_task_123"
        assert envelope.task_id == self.Values.TASK_ID
        assert envelope.subagent_id == self.Values.SUBAGENT_NAME
        assert envelope.display_title == "researcher subagent"
        assert envelope.status == "completed"
