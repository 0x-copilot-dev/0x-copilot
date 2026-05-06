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

    def test_sensitive_value_pattern_inside_user_content_key_is_preserved(
        self,
    ) -> None:
        # Regression for the "[redacted]" assistant message bug: the
        # SENSITIVE_VALUE regex is heuristic and over-fires on any user
        # content (prose, code) containing `key = value` patterns where
        # the key looks credential-shaped. The whole streamed model
        # response was being destroyed when the model wrote even one
        # illustrative `api_key = "..."` line. User-content keys
        # (message/delta/output/etc) now bypass the value-regex entirely;
        # the structural SENSITIVE_KEY scan still protects nested dicts.
        result = ObservabilityRedactor.redact_json_object(
            {"message": "Here is a snippet: api_key = 'placeholder'"}
        )

        assert result["message"] == "Here is a snippet: api_key = 'placeholder'"

    def test_sensitive_value_outside_user_content_key_still_redacted(self) -> None:
        # The fix narrows scope to user-visible content only; structural
        # diagnostic / metadata fields keep the existing redaction
        # behaviour so a misbehaving tool can't leak via, say, a
        # `diagnostic_blob` field.
        result = ObservabilityRedactor.redact_json_object(
            {"diagnostic_blob": "api_key=sk-leaked-1234"}
        )

        assert result["diagnostic_blob"] == Defaults.REDACTED

    def test_sensitive_key_nested_inside_user_content_key_still_redacted(self) -> None:
        # Structural SENSITIVE_KEY scrub still applies inside user
        # content — a tool that emits `{"api_key": "..."}` inside a
        # message payload still gets that key dropped. Free-form prose
        # under a non-credential key in the same subtree is preserved.
        result = ObservabilityRedactor.redact_json_object(
            {"message": {"api_key": "sk-leaked-1234", "body": self.LONG_TEXT}}
        )

        assert result["message"]["api_key"] == Defaults.REDACTED
        assert result["message"]["body"] == self.LONG_TEXT

    def test_sensitive_value_pattern_inside_nested_user_content_is_preserved(
        self,
    ) -> None:
        # User-content territory propagates through nested structures
        # so model output framed as Anthropic content blocks
        # (`output.content[*].text` etc.) is not destroyed by the
        # heuristic regex on inner string leaves.
        result = ObservabilityRedactor.redact_json_object(
            {
                "output": {
                    "items": [
                        {"text": "the secret = my-secret was set"},
                    ]
                }
            }
        )

        assert result["output"]["items"][0]["text"] == "the secret = my-secret was set"
