"""Unit tests for :class:`McpToolCallOutcome` protocol-error classification."""

from __future__ import annotations

from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.outcomes import McpToolCallOutcome


class McpOutcomeFixturesMixin:
    """Shared envelope fixtures for protocol-error detection tests."""

    class TestValues:
        ERROR_TEXT = (
            "MCP error -32602: Invalid arguments for tool list_issues: "
            "unrecognized_keys: ['parameters']"
        )
        OK_TEXT = "found tasks"
        NESTED_TEXT = "nested error detail"

    def outer_error_envelope(self) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": self.TestValues.ERROR_TEXT}],
            "isError": True,
        }

    def nested_error_envelope(self) -> dict[str, object]:
        return {
            "output": {
                "content": [{"type": "text", "text": self.TestValues.NESTED_TEXT}],
                "isError": True,
            }
        }

    def healthy_envelope(self) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": self.TestValues.OK_TEXT}],
        }


class TestIsProtocolError(McpOutcomeFixturesMixin):
    def test_returns_true_when_outer_envelope_marks_is_error(self) -> None:
        assert McpToolCallOutcome.is_protocol_error(self.outer_error_envelope())

    def test_returns_true_when_nested_envelope_marks_is_error(self) -> None:
        assert McpToolCallOutcome.is_protocol_error(self.nested_error_envelope())

    def test_returns_false_when_is_error_is_absent(self) -> None:
        assert not McpToolCallOutcome.is_protocol_error(self.healthy_envelope())

    def test_returns_false_when_is_error_is_explicit_false(self) -> None:
        envelope = {"content": [], "isError": False}
        assert not McpToolCallOutcome.is_protocol_error(envelope)

    def test_returns_false_for_non_mapping_input(self) -> None:
        assert not McpToolCallOutcome.is_protocol_error([])  # type: ignore[arg-type]

    def test_returns_false_when_nested_output_is_not_mapping(self) -> None:
        envelope = {"output": "not-a-dict"}
        assert not McpToolCallOutcome.is_protocol_error(envelope)


class TestExtractErrorText(McpOutcomeFixturesMixin):
    def test_reads_first_text_block_from_outer_content(self) -> None:
        assert (
            McpToolCallOutcome.extract_error_text(self.outer_error_envelope())
            == self.TestValues.ERROR_TEXT
        )

    def test_reads_first_text_block_from_nested_content(self) -> None:
        assert (
            McpToolCallOutcome.extract_error_text(self.nested_error_envelope())
            == self.TestValues.NESTED_TEXT
        )

    def test_outer_text_wins_over_nested(self) -> None:
        envelope = {
            **self.outer_error_envelope(),
            "output": {
                "content": [{"type": "text", "text": self.TestValues.NESTED_TEXT}],
                "isError": True,
            },
        }
        assert (
            McpToolCallOutcome.extract_error_text(envelope)
            == self.TestValues.ERROR_TEXT
        )

    def test_skips_empty_or_non_text_blocks(self) -> None:
        envelope = {
            "content": [
                {"type": "image", "data": "..."},
                {"type": "text", "text": ""},
                {"type": "text", "text": self.TestValues.ERROR_TEXT},
            ],
            "isError": True,
        }
        assert (
            McpToolCallOutcome.extract_error_text(envelope)
            == self.TestValues.ERROR_TEXT
        )

    def test_returns_safe_fallback_when_no_text_block_present(self) -> None:
        envelope = {"content": [], "isError": True}
        assert (
            McpToolCallOutcome.extract_error_text(envelope)
            == Messages.Loader.PROTOCOL_ERROR_FALLBACK
        )

    def test_returns_safe_fallback_for_non_mapping_input(self) -> None:
        assert (
            McpToolCallOutcome.extract_error_text(None)  # type: ignore[arg-type]
            == Messages.Loader.PROTOCOL_ERROR_FALLBACK
        )
