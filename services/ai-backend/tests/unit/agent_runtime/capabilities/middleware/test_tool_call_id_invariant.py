"""The tool_call_id invariant — a tool result must never carry an unusable id.

Regression cover for a defect that reached a user: a wrapped tool emitted a
``ToolMessage`` whose ``tool_call_id`` was the empty string. Nothing internal
objected, because "" is a legitimate-looking value. It surfaced turns later as

    BadRequestError: 400 - Invalid 'input[3].call_id': empty string.

raised inside ``langchain_openai`` — a traceback with no mention of the tool
that caused it.

It is provider-luck, not correctness, that hid this: OpenAI validates the field
and rejects it, Anthropic accepts it. The same build was fine on Claude and
fatal on GPT.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.middleware.display_metadata import (
    UNINJECTED_TOOL_CALL_ID,
    MissingToolCallIdError,
    _DispatchEnvelope,
    require_tool_call_id,
)


class TestRequireToolCallId:
    def test_returns_a_real_id_unchanged(self) -> None:
        assert require_tool_call_id("call_abc123", tool_name="t") == "call_abc123"

    @pytest.mark.parametrize("bad", ["", UNINJECTED_TOOL_CALL_ID])
    def test_rejects_unusable_ids(self, bad: str) -> None:
        with pytest.raises(MissingToolCallIdError):
            require_tool_call_id(bad, tool_name="write_artifact")

    def test_error_names_the_tool_so_the_cause_is_locatable(self) -> None:
        """The whole point of failing here is not having to guess later."""
        with pytest.raises(MissingToolCallIdError, match="write_artifact"):
            require_tool_call_id("", tool_name="write_artifact")


class TestDispatchEnvelope:
    def test_builds_with_a_real_id(self) -> None:
        envelope = _DispatchEnvelope.build(
            args={"path": "forecast.csv"}, name="write_artifact", tool_call_id="call_1"
        )
        assert envelope[_DispatchEnvelope.KEY_ID] == "call_1"
        assert envelope[_DispatchEnvelope.KEY_TYPE] == _DispatchEnvelope.TYPE_TOOL_CALL

    @pytest.mark.parametrize("bad", ["", UNINJECTED_TOOL_CALL_ID])
    def test_refuses_to_build_an_envelope_around_an_unusable_id(self, bad: str) -> None:
        """This envelope becomes the ToolMessage that enters history.

        A bad id here is not a local error — it poisons every subsequent model
        call in the run, which is why it is refused at construction rather than
        somewhere further downstream.
        """
        with pytest.raises(MissingToolCallIdError):
            _DispatchEnvelope.build(args={}, name="write_artifact", tool_call_id=bad)


class TestSentinel:
    def test_sentinel_is_not_empty(self) -> None:
        """An empty default is what made the original bug silent.

        A non-empty sentinel cannot be mistaken for "legitimately blank", and it
        is greppable if it ever does escape.
        """
        assert UNINJECTED_TOOL_CALL_ID
        assert UNINJECTED_TOOL_CALL_ID.strip() == UNINJECTED_TOOL_CALL_ID
