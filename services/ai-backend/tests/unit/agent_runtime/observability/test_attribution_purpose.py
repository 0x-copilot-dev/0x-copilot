"""Tests for the P7.5 Library + P12 Palette/Memory purpose additions.

Two-part contract per the P7.5 + P12 specs (cross-audit §5.5):

1. ``LIBRARY_RETRIEVAL`` / ``LIBRARY_INDEXING`` (P7.5-A1) and
   ``PALETTE_RANKING`` / ``MEMORY_RETRIEVAL`` / ``MEMORY_INDEXING`` /
   ``MEMORY_EXTRACTION`` (P12-A4/A5) are valid :class:`Purpose` enum
   values with their canonical string values.
2. A :class:`UsageAttributionContext` constructed with any of the new
   purposes round-trips through the recorder boundary without tripping
   the existing subagent/tool-interpretation invariants — i.e. these
   system-initiated calls do not require ``subagent_slug`` or
   ``originating_tool_call_id``. This is the precise contract the
   embed / extractor endpoints rely on, mirroring how
   ``TODO_EXTRACTION`` slots into the same enum without extending the
   validator.
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

    def test_palette_ranking_value(self) -> None:
        assert Purpose.PALETTE_RANKING.value == "palette_ranking"

    def test_memory_retrieval_value(self) -> None:
        assert Purpose.MEMORY_RETRIEVAL.value == "memory_retrieval"

    def test_memory_indexing_value(self) -> None:
        assert Purpose.MEMORY_INDEXING.value == "memory_indexing"

    def test_memory_extraction_value(self) -> None:
        assert Purpose.MEMORY_EXTRACTION.value == "memory_extraction"

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
        assert Purpose.PALETTE_RANKING not in existing
        assert Purpose.MEMORY_RETRIEVAL not in existing
        assert Purpose.MEMORY_INDEXING not in existing
        assert Purpose.MEMORY_EXTRACTION not in existing


class TestLibraryAttributionContextInvariants:
    """Library / palette / memory purposes bypass the subagent / tool-interpretation invariants."""

    @pytest.mark.parametrize(
        "purpose",
        [
            Purpose.LIBRARY_RETRIEVAL,
            Purpose.LIBRARY_INDEXING,
            Purpose.PALETTE_RANKING,
            Purpose.MEMORY_RETRIEVAL,
            Purpose.MEMORY_INDEXING,
            Purpose.MEMORY_EXTRACTION,
        ],
    )
    def test_constructs_without_subagent_or_tool_signals(
        self, purpose: Purpose
    ) -> None:
        # Neither subagent_slug nor originating_tool_call_id required:
        # Library / Palette / Memory calls are system-initiated, not
        # tool-interpretation.
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
    """The existing recorder boundary accepts the new purpose rows."""

    @pytest.mark.parametrize(
        ("purpose", "expected_column_value"),
        [
            (Purpose.LIBRARY_RETRIEVAL, "library_retrieval"),
            (Purpose.LIBRARY_INDEXING, "library_indexing"),
            (Purpose.PALETTE_RANKING, "palette_ranking"),
            (Purpose.MEMORY_RETRIEVAL, "memory_retrieval"),
            (Purpose.MEMORY_INDEXING, "memory_indexing"),
            (Purpose.MEMORY_EXTRACTION, "memory_extraction"),
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
