"""Tests for the P7.5 Library purpose additions to the :class:`Purpose` enum.

Two-part contract per the P7.5 spec (cross-audit §5.5):

1. Both ``LIBRARY_RETRIEVAL`` and ``LIBRARY_INDEXING`` are valid
   :class:`Purpose` enum values with their canonical string values.
2. A :class:`UsageAttributionContext` constructed with either of the
   new purposes round-trips through the recorder boundary without
   tripping the existing subagent/tool-interpretation invariants —
   i.e. Library calls do not require ``subagent_slug`` or
   ``originating_tool_call_id``. This is the precise contract the
   embed endpoint relies on, mirroring how ``TODO_EXTRACTION`` slots
   into the same enum without extending the validator.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.observability.attribution import (
    Purpose,
    UsageAttributionContext,
)
from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord


class TestLibraryPurposeEnumValues:
    """The new enum values exist and carry the expected wire string."""

    def test_library_retrieval_value(self) -> None:
        assert Purpose.LIBRARY_RETRIEVAL.value == "library_retrieval"

    def test_library_indexing_value(self) -> None:
        assert Purpose.LIBRARY_INDEXING.value == "library_indexing"

    def test_library_purposes_distinct_from_existing(self) -> None:
        existing = {
            Purpose.MAIN,
            Purpose.TOOL_PLANNING,
            Purpose.TOOL_INTERPRETATION,
            Purpose.SUBAGENT_WORK,
            Purpose.CONTEXT_COMPRESSION,
            Purpose.TODO_EXTRACTION,
        }
        assert Purpose.LIBRARY_RETRIEVAL not in existing
        assert Purpose.LIBRARY_INDEXING not in existing


class TestLibraryAttributionContextInvariants:
    """Library purposes bypass the subagent / tool-interpretation invariants."""

    @pytest.mark.parametrize(
        "purpose",
        [Purpose.LIBRARY_RETRIEVAL, Purpose.LIBRARY_INDEXING],
    )
    def test_constructs_without_subagent_or_tool_signals(
        self, purpose: Purpose
    ) -> None:
        # Neither subagent_slug nor originating_tool_call_id required:
        # Library calls are system-initiated, not tool-interpretation.
        context = UsageAttributionContext(
            org_id="org_1",
            user_id="user_1",
            run_id="run_1",
            conversation_id="conv_1",
            trace_id="trace_1",
            purpose=purpose,
        )
        assert context.purpose is purpose
        assert context.subagent_slug is None
        assert context.originating_tool_call_id is None


class TestRecorderWritesLibraryPurpose:
    """The existing recorder boundary accepts Library purpose rows."""

    @pytest.mark.parametrize(
        ("purpose", "expected_column_value"),
        [
            (Purpose.LIBRARY_RETRIEVAL, "library_retrieval"),
            (Purpose.LIBRARY_INDEXING, "library_indexing"),
        ],
    )
    async def test_record_call_persists_purpose_column(
        self,
        purpose: Purpose,
        expected_column_value: str,
    ) -> None:
        recorder = InMemoryUsageRecorder()
        record = RuntimeModelCallUsageRecord(
            org_id="org_1",
            run_id="embed-1",
            conversation_id="embed-1",
            trace_id="embed-1",
            model_provider="openai",
            model_name="text-embedding-3-small",
            purpose=purpose.value,
            input_tokens=3,
            output_tokens=0,
            total_tokens=3,
            duration_ms=42,
        )
        await recorder.record_call(
            record, pricing_at=datetime(2026, 5, 18, tzinfo=timezone.utc)
        )
        assert len(recorder.calls) == 1
        assert recorder.calls[0].purpose == expected_column_value
