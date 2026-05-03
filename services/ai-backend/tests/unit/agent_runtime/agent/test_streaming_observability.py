from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from agent_runtime.observability.constants import Defaults
from agent_runtime.observability.redaction import ObservabilityRedactor
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


class TestObservabilityRedactorUserContent:
    LONG_TEXT = "x" * 10_000

    def test_user_content_key_bypasses_length_cap(self) -> None:
        result = ObservabilityRedactor.redact_json_object({"message": self.LONG_TEXT})

        assert result["message"] == self.LONG_TEXT
        assert Defaults.TRUNCATED not in result["message"]

    def test_user_content_uncap_is_sticky_through_nested_structures(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"output": {"items": [{"text": self.LONG_TEXT}]}}
        )

        assert result["output"]["items"][0]["text"] == self.LONG_TEXT

    def test_non_user_content_key_still_clipped(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"diagnostic_blob": self.LONG_TEXT}
        )

        clipped = result["diagnostic_blob"]
        assert clipped.endswith(Defaults.TRUNCATED)
        assert len(clipped) == Defaults.MAX_STREAM_FIELD_LENGTH + len(
            Defaults.TRUNCATED
        )

    def test_sensitive_value_inside_user_content_key_still_redacted(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"message": "api_key=sk-leaked-1234"}
        )

        assert result["message"] == Defaults.REDACTED

    def test_sensitive_key_nested_inside_user_content_key_still_redacted(self) -> None:
        result = ObservabilityRedactor.redact_json_object(
            {"message": {"api_key": "sk-leaked-1234", "body": self.LONG_TEXT}}
        )

        assert result["message"]["api_key"] == Defaults.REDACTED
        assert result["message"]["body"] == self.LONG_TEXT
