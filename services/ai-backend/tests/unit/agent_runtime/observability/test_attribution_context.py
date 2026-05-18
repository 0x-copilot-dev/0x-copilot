"""Tests for :class:`Purpose` and :class:`UsageAttributionContext`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.observability.attribution import (
    Purpose,
    UsageAttributionContext,
)


def _base_kwargs(**overrides: object) -> dict[str, object]:
    """Sufficient required fields for a MAIN context — tests override
    purpose / optional fields to exercise each invariant."""

    base: dict[str, object] = {
        "org_id": "org_1",
        "user_id": "user_1",
        "run_id": "run_1",
        "conversation_id": "conv_1",
        "trace_id": "trace_1",
        "purpose": Purpose.MAIN,
    }
    base.update(overrides)
    return base


class TestPurposeDerivePrecedence:
    """Top-wins precedence: compression > subagent > interpretation > planning > main."""

    def test_compression_wins_over_everything(self) -> None:
        # Every other signal is true; compression still wins.
        assert (
            Purpose.derive(
                input_has_tool_message=True,
                output_has_tool_calls=True,
                is_subagent=True,
                is_compression=True,
            )
            == Purpose.CONTEXT_COMPRESSION
        )

    def test_subagent_wins_over_tool_interpretation(self) -> None:
        # A subagent's tool-interpretation call collapses to SUBAGENT_WORK.
        assert (
            Purpose.derive(
                input_has_tool_message=True,
                output_has_tool_calls=False,
                is_subagent=True,
                is_compression=False,
            )
            == Purpose.SUBAGENT_WORK
        )

    def test_interpretation_wins_over_planning(self) -> None:
        # A main-loop call that interprets prior results AND plans next
        # tool collapses to TOOL_INTERPRETATION — interpretation is the
        # user-facing semantic.
        assert (
            Purpose.derive(
                input_has_tool_message=True,
                output_has_tool_calls=True,
                is_subagent=False,
                is_compression=False,
            )
            == Purpose.TOOL_INTERPRETATION
        )

    def test_planning_when_tools_in_output_only(self) -> None:
        assert (
            Purpose.derive(
                input_has_tool_message=False,
                output_has_tool_calls=True,
                is_subagent=False,
                is_compression=False,
            )
            == Purpose.TOOL_PLANNING
        )

    def test_main_when_no_signals(self) -> None:
        assert (
            Purpose.derive(
                input_has_tool_message=False,
                output_has_tool_calls=False,
                is_subagent=False,
                is_compression=False,
            )
            == Purpose.MAIN
        )


class TestPurposeEnum:
    def test_string_values(self) -> None:
        # Pin the wire shape — these strings land in the ``purpose``
        # column and any rollup queries key on them.
        assert Purpose.MAIN == "main"
        assert Purpose.TOOL_PLANNING == "tool_planning"
        assert Purpose.TOOL_INTERPRETATION == "tool_interpretation"
        assert Purpose.SUBAGENT_WORK == "subagent_work"
        assert Purpose.CONTEXT_COMPRESSION == "context_compression"

    def test_value_count_pinned(self) -> None:
        # Adding a Purpose requires a deliberate test update + parent
        # PRD §6.2 review — they shape rollup tables and FE filters.
        # P3-A2 added TODO_EXTRACTION for the post-run extractor worker.
        # P7.5-A1 added LIBRARY_RETRIEVAL + LIBRARY_INDEXING for the
        # Library hybrid retrieval / offline embedding worker paths
        # (sub-PRD library §6.5 / §6.6; cross-audit §5.5 single-tracker).
        assert len(Purpose) == 8

    def test_todo_extraction_purpose_present(self) -> None:
        # P3-A2 — the extractor worker job persists usage rows with this
        # purpose so cross-audit §5.5's "single tracker" invariant holds.
        assert Purpose.TODO_EXTRACTION == "todo_extraction"


class TestUsageAttributionContextConstruction:
    def test_main_context_with_required_fields_only(self) -> None:
        ctx = UsageAttributionContext(**_base_kwargs())
        assert ctx.purpose == Purpose.MAIN
        assert ctx.task_id is None
        assert ctx.subagent_slug is None
        assert ctx.originating_tool_call_id is None

    def test_subagent_context_round_trip(self) -> None:
        ctx = UsageAttributionContext(
            **_base_kwargs(
                purpose=Purpose.SUBAGENT_WORK,
                task_id="call_xyz",
                subagent_slug="researcher",
            )
        )
        assert ctx.purpose == Purpose.SUBAGENT_WORK
        assert ctx.task_id == "call_xyz"
        assert ctx.subagent_slug == "researcher"

    def test_tool_interpretation_context_round_trip(self) -> None:
        ctx = UsageAttributionContext(
            **_base_kwargs(
                purpose=Purpose.TOOL_INTERPRETATION,
                originating_tool_call_id="call_abc",
                originating_tool_name="jira_search",
            )
        )
        assert ctx.purpose == Purpose.TOOL_INTERPRETATION
        assert ctx.originating_tool_call_id == "call_abc"
        assert ctx.originating_tool_name == "jira_search"


class TestUsageAttributionContextInvariants:
    def test_subagent_without_slug_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            UsageAttributionContext(**_base_kwargs(purpose=Purpose.SUBAGENT_WORK))
        assert "subagent_slug required" in str(excinfo.value)

    def test_tool_interpretation_without_tool_call_id_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            UsageAttributionContext(**_base_kwargs(purpose=Purpose.TOOL_INTERPRETATION))
        assert "originating_tool_call_id required" in str(excinfo.value)

    def test_subagent_slug_without_task_id_rejected(self) -> None:
        # Subagent slug implies we know which task it belongs to; the
        # task_id is the supervisor's ``task`` tool call_id.
        with pytest.raises(ValidationError) as excinfo:
            UsageAttributionContext(
                **_base_kwargs(
                    purpose=Purpose.SUBAGENT_WORK,
                    subagent_slug="researcher",
                    # task_id omitted
                )
            )
        assert "task_id required" in str(excinfo.value)


class TestUsageAttributionContextImmutability:
    def test_frozen(self) -> None:
        ctx = UsageAttributionContext(**_base_kwargs())
        with pytest.raises(ValidationError):
            ctx.purpose = Purpose.MAIN  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            UsageAttributionContext(
                **_base_kwargs(mystery_field="value")  # type: ignore[call-arg]
            )
