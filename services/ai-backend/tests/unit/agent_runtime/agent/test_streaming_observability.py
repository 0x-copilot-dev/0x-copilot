from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    StreamEvent,
    StreamEventSource,
    StreamEventType,
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

        assert event.payload["api_key"] == Defaults.REDACTED
        assert event.payload["safe"] == "visible"
        assert event.payload["nested"] == {"authorization": Defaults.REDACTED}

        with pytest.raises(ValidationError):
            StreamEvent(
                source=StreamEventSource.MAIN_AGENT,
                event_type=StreamEventType.PROGRESS,
                trace_id="../trace",
            )
