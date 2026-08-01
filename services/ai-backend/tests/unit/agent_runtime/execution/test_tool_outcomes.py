"""Unit tests for :class:`ToolInvocationOutcome` message projection.

Focus: a failed tool's model-facing message is clipped to a fixed budget, and
a clipped message carries an explicit truncation marker so the model can tell a
severed sentence from one that genuinely ended at the budget.
"""

from __future__ import annotations

from agent_runtime.execution.tool_error_sanitizer import ErrorSanitizer
from agent_runtime.execution.tool_outcomes import (
    ToolInvocationOutcome,
    _TRUNCATION_MARKER,
)
from agent_runtime.persistence.records.common import ToolInvocationStatus


class _OutcomeFixtures:
    """Shared payload builders for outcome-projection tests."""

    MAX = ToolInvocationOutcome._MAX_ERROR_MESSAGE_LENGTH

    def failed_with_message(self, message: str) -> dict[str, object]:
        return {"status": "failed", "error_message": message}

    def failed_with_output(self, output: object) -> dict[str, object]:
        return {"status": "failed", "output": output}


class TestErrorMessageTruncationMarker(_OutcomeFixtures):
    def test_short_message_is_returned_verbatim_without_a_marker(self) -> None:
        closed = ToolInvocationOutcome.from_result_payload(
            self.failed_with_message('column "team_id" does not exist')
        )
        assert closed["status"] is ToolInvocationStatus.FAILED
        assert closed["safe_error_message"] == 'column "team_id" does not exist'
        assert _TRUNCATION_MARKER not in closed["safe_error_message"]

    def test_long_message_is_clipped_to_the_budget_with_a_marker(self) -> None:
        closed = ToolInvocationOutcome.from_result_payload(
            self.failed_with_message("x" * (self.MAX + 500))
        )
        message = closed["safe_error_message"]
        # THE regression: a bare slice stopped mid-clause with no signal, so the
        # model could not tell truncation from a message that ended at 400.
        assert message.endswith(_TRUNCATION_MARKER)
        assert len(message) <= self.MAX

    def test_message_at_exactly_the_budget_is_not_marked(self) -> None:
        closed = ToolInvocationOutcome.from_result_payload(
            self.failed_with_message("y" * self.MAX)
        )
        message = closed["safe_error_message"]
        assert len(message) == self.MAX
        assert _TRUNCATION_MARKER not in message

    def test_long_output_summary_fallback_is_also_marked(self) -> None:
        # No error_message: the redacted output is rendered as evidence, and it
        # too must not silently stop mid-value.
        closed = ToolInvocationOutcome.from_result_payload(
            self.failed_with_output({"detail": "z" * (self.MAX + 500)})
        )
        message = closed["safe_error_message"]
        assert message.endswith(_TRUNCATION_MARKER)
        assert len(message) <= self.MAX

    def test_marker_matches_the_error_sanitizer_marker(self) -> None:
        # One truncation signal across the service: the marker here is the same
        # bytes ``ErrorSanitizer`` appends when it clips a runaway message.
        sanitized = ErrorSanitizer.sanitize(RuntimeError("q" * 5000))
        assert sanitized.endswith(_TRUNCATION_MARKER)
