from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    ObservationEvent,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
    ToolCallEvent,
)
from agent_runtime.observability.constants import Defaults
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
