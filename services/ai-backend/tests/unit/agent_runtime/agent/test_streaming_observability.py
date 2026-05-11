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
        # Original regression context: the SENSITIVE_VALUE regex used
        # to destroy assistant messages whenever the model wrote even
        # one illustrative `api_key = "..."` line. The user-content
        # carve-out was added to skip the value regex on string leaves
        # under message/delta/output/etc.
        #
        # P11.2 removed value-pattern scanning entirely (parent PRD
        # §8). This test still passes — the assistant text is preserved
        # — but now it passes for a stronger reason: nothing scans
        # string values for credential shapes anywhere in the system.
        result = ObservabilityRedactor.redact_json_object(
            {"message": "Here is a snippet: api_key = 'placeholder'"}
        )

        assert result["message"] == "Here is a snippet: api_key = 'placeholder'"

    def test_sensitive_value_in_non_user_content_now_passes_through(self) -> None:
        # P11.2: value-pattern scrubbing is gone. A string literally
        # containing `api_key=sk-...` outside a user-content key used
        # to be replaced with `[redacted]`. The new direction (parent
        # PRD §8) treats sensitivity as a field-level property, not a
        # value-shape one — so the value passes through unchanged.
        #
        # The structural deny set still scrubs literal `{"api_key": "..."}`
        # dict keys; this test only documents that free-text values
        # containing credential-shaped substrings are no longer
        # auto-redacted. Logs (which DO need to drop such content) get
        # there via P11.3 field tagging on the Pydantic model that
        # carries the field, not via value scanning here.
        result = ObservabilityRedactor.redact_json_object(
            {"diagnostic_blob": "api_key=sk-leaked-1234"}
        )

        assert result["diagnostic_blob"] == "api_key=sk-leaked-1234"

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
        # User-content territory still propagates through nested
        # structures for length-cap purposes. P11.2 removed value
        # scanning entirely, so this test now passes structurally
        # rather than via the user-content carve-out — the inner
        # ``text`` value would be preserved even at top level.
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
