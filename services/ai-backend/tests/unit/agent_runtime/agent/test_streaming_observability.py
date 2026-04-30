from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.agent.contracts import (
    AgentRuntimeContext,
    ObservationEvent,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
    ToolCallEvent,
)
from agent_runtime.observability.constants import Defaults, Messages
from tests.unit.agent_runtime.agent.helpers import StreamingObservabilityTestMixin


class TestStreamingAndObservability(StreamingObservabilityTestMixin):
    def test_stream_contracts_validate_and_redact_payloads(self) -> None:
        event = StreamEvent(
            source=StreamEventSource.MAIN_AGENT,
            event_type=StreamEventType.PROGRESS,
            trace_id=self.Values.TRACE_ID,
            payload={
                "api_key": self.Values.SECRET,
                "safe": "visible",
                "nested": {"authorization": f"bearer {self.Values.SECRET}"},
            },
        )
        tool_call = ToolCallEvent(
            tool_name="Doc_Search",
            call_id=self.Values.CALL_ID,
            args={"password": self.Values.SECRET, "query": "board plan"},
        )
        observation = ObservationEvent(
            metric_name="Agent.Latency",
            value=42,
            trace_id=self.Values.TRACE_ID,
            tags={"token": self.Values.SECRET, "phase": "streaming"},
        )

        assert event.payload["api_key"] == Defaults.REDACTED
        assert event.payload["safe"] == "visible"
        assert event.payload["nested"] == {"authorization": Defaults.REDACTED}
        assert tool_call.tool_name == self.Values.TOOL_NAME
        assert tool_call.args["password"] == Defaults.REDACTED
        assert observation.metric_name == "agent.latency"
        assert observation.tags["token"] == Defaults.REDACTED

        with pytest.raises(ValidationError):
            StreamEvent(
                source=StreamEventSource.MAIN_AGENT,
                event_type=StreamEventType.PROGRESS,
                trace_id="../trace",
            )

    def test_normalizes_main_agent_updates_without_raw_namespace(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        events = self.make_normalizer().normalize(
            self.main_update_chunk(),
            runtime_context_admin,
        )

        assert len(events) == 1
        event = events[0]
        assert event.source is StreamEventSource.MAIN_AGENT
        assert event.event_type is StreamEventType.PROGRESS
        assert event.trace_id == self.Values.TRACE_ID
        assert event.payload["message"] == self.Values.SAFE_MESSAGE
        assert event.payload["api_key"] == Defaults.REDACTED
        assert "ns" not in event.payload

    def test_normalizes_subagent_events_with_parent_task(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        events = self.make_normalizer().normalize(
            self.subagent_progress_chunk(),
            runtime_context_admin,
        )

        assert len(events) == 1
        event = events[0]
        assert event.source is StreamEventSource.SUBAGENT
        assert event.event_type is StreamEventType.CUSTOM
        assert event.parent_task_id == self.Values.TASK_ID
        assert event.metadata["namespace"] == ["supervisor", "subagent:researcher"]

    def test_normalizes_tool_call_and_oversized_tool_result(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        tool_call_events = self.make_normalizer().normalize(
            self.tool_call_chunk(),
            runtime_context_admin,
        )
        tool_result_events = self.make_normalizer().normalize(
            self.tool_result_chunk("x" * (Defaults.MAX_STREAM_FIELD_LENGTH + 20)),
            runtime_context_admin,
        )

        assert len(tool_call_events) == 1
        tool_call = tool_call_events[0]
        assert tool_call.source is StreamEventSource.TOOL
        assert tool_call.event_type is StreamEventType.TOOL_CALL
        assert tool_call.payload["tool_name"] == self.Values.TOOL_NAME
        assert tool_call.payload["call_id"] == self.Values.CALL_ID
        assert tool_call.payload["args"] == {
            "query": "board plan",
            "authorization": Defaults.REDACTED,
        }

        tool_result = tool_result_events[0]
        assert tool_result.event_type is StreamEventType.TOOL_RESULT
        assert tool_result.payload["output"]["content"].endswith(Defaults.TRUNCATED)
        assert tool_result.payload["output"]["token"] == Defaults.REDACTED

    def test_handles_malformed_unknown_and_early_subagent_lifecycle(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        unknown_events = self.make_normalizer().normalize(
            {"mode": "unknown", "chunk": {"message": "raw unsupported event"}},
            runtime_context_admin,
        )
        malformed_events = self.make_normalizer().normalize(
            {
                "mode": "custom",
                "event_type": "observation",
                "chunk": {"metric_name": "bad metric name", "value": 1},
            },
            runtime_context_admin,
        )
        lifecycle_events = self.make_normalizer().normalize(
            self.lifecycle_chunk_without_task_metadata(),
            runtime_context_admin,
        )

        assert unknown_events[0].event_type is StreamEventType.ERROR
        assert unknown_events[0].payload["message"] == Messages.Events.UNKNOWN_STREAM_MODE
        assert malformed_events[0].event_type is StreamEventType.ERROR
        assert malformed_events[0].payload["message"] == Messages.Events.MALFORMED_CHUNK
        assert lifecycle_events[0].source is StreamEventSource.SUBAGENT
        assert lifecycle_events[0].payload["task_id"] == "unknown"
        assert lifecycle_events[0].payload["subagent_name"] == self.Values.SUBAGENT_NAME

    def test_filters_summarization_tokens_from_user_facing_streams(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        raw_event = {
            "mode": "messages",
            "ns": ("summarization",),
            "chunk": {"content": "internal summary token"},
        }

        user_events = self.make_normalizer().normalize(raw_event, runtime_context_admin)
        internal_events = self.make_normalizer().normalize(
            raw_event,
            runtime_context_admin,
            include_internal=True,
        )

        assert user_events == ()
        assert len(internal_events) == 1
        assert internal_events[0].source is StreamEventSource.SUMMARIZATION
