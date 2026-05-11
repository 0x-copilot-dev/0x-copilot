from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from tests.unit.agent_runtime.agent.helpers import StreamingObservabilityTestMixin


class TestStreamingAndObservability(StreamingObservabilityTestMixin):
    def test_stream_contracts_validate_payloads_without_redaction(self) -> None:
        # P11.5: ``StreamEvent.payload`` flows through ``JsonObjectCoercer``
        # — structural coercion only. Sensitive data is NOT scrubbed
        # at this boundary; logs filter at their own emission point
        # via ``DENY_KEYS`` and field tagging.
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

        assert event.payload["api_key"] == self.Values.SECRET
        assert event.payload["safe"] == "visible"
        assert event.payload["nested"] == {
            "authorization": f"bearer {self.Values.SECRET}"
        }

        with pytest.raises(ValidationError):
            StreamEvent(
                source=StreamEventSource.MAIN_AGENT,
                event_type=StreamEventType.PROGRESS,
                trace_id="../trace",
            )


# P11.6: ``TestObservabilityRedactorUserContent`` (length-cap + user-content
# carve-out + value-regex behavior) was removed. Those behaviors no longer
# exist anywhere in the system:
#
# - Value scanning was deleted in P11.2.
# - Length clipping outside log paths was deleted in P11.5.
# - ``ObservabilityRedactor`` was deleted in P11.6.
#
# Current coverage of the redaction surface:
# - ``DENY_KEYS`` membership: tests/unit/agent_runtime/observability/test_deny_keys.py
# - Coercion: tests/unit/agent_runtime/observability/test_json_object_coercer.py
# - Field tagging: tests/unit/agent_runtime/observability/test_field_tagging.py
# - Log-record deny-key filter: tests/unit/agent_runtime/observability/test_logging.py
# - HTTP-log deny-key filter: tests/unit/agent_runtime/observability/test_http_logging.py
