"""Contract tests for the Context Occupancy Ledger's public read shapes.

The route suite proves the endpoints behave. This one proves the *contracts*
behave, at the level the route tests cannot reach: the record→wire projection
field by field, the fail-open segment parse, and the two bounds that keep §6.5's
"counts and identifiers, never content" rule enforceable at the read edge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from runtime_api.schemas.context_occupancy import (
    ContextOccupancyResponse,
    ContextOccupancySegment,
    ContextOccupancySnapshotPayload,
    ConversationContextOccupancyResponse,
)


class OccupancyRecordMixin:
    """Build durable rows to project from."""

    SEGMENT: dict[str, Any] = {
        "segment_class": "system",
        "label": "agent_runtime.prompts:default_instructions",
        "lifecycle": "resident",
        "third_party": False,
        "detail": "fragment_id=default_instructions",
        "byte_count": 3_884,
        "estimated_tokens": 971,
        "item_count": 1,
        "cache_eligibility": "stable_prefix",
        "counter_source": "tokenizer",
    }

    def record(self, **overrides: Any) -> RuntimeContextOccupancyRecord:
        values: dict[str, Any] = {
            "org_id": "org_schema",
            "run_id": "run_schema",
            "conversation_id": "conv_schema",
            "model_call_id": "call_schema",
            "graph_scope": RuntimeContextGraphScope.ROOT,
            "provider": "OpenAI",
            "model_family": "gpt-5.4-mini",
            "context_window_tokens": 400_000,
            "estimated_input_tokens": 971,
            "provider_input_tokens": 1_010,
            "cached_input_tokens": 900,
            "cache_creation_input_tokens": 0,
            "undeclared_tokens": 0,
            "segments": (self.SEGMENT,),
            "created_at": datetime(2026, 7, 30, 9, 30, tzinfo=timezone.utc),
        }
        values.update(overrides)
        return RuntimeContextOccupancyRecord.from_measurement(**values)


class TestSnapshotProjection(OccupancyRecordMixin):
    """Every measured field survives the trip to the wire — and ``org_id`` does not."""

    def test_projects_every_measured_field(self) -> None:
        payload = ContextOccupancySnapshotPayload.from_record(self.record())

        assert payload.schema_version == 1
        assert payload.model_call_id == "call_schema"
        assert payload.attempt_ordinal == 1
        assert payload.graph_scope is RuntimeContextGraphScope.ROOT
        # The record folds provider casing so "OpenAI" and "openai" group as one;
        # the wire inherits that rather than re-deriving a second normalization.
        assert payload.provider == "openai"
        assert payload.model_family == "gpt-5.4-mini"
        assert payload.measured_at == datetime(2026, 7, 30, 9, 30, tzinfo=timezone.utc)
        assert payload.context_window_tokens == 400_000
        assert payload.estimated_input_tokens == 971
        assert payload.provider_input_tokens == 1_010
        assert payload.cached_input_tokens == 900
        assert payload.cache_creation_input_tokens == 0
        assert payload.undeclared_tokens == 0
        assert payload.unattributed_delta == 39
        assert payload.free_tokens == 400_000 - 1_010
        assert payload.unreadable_segment_count == 0
        assert len(payload.segments) == 1

    def test_tenant_identifier_is_absent_from_the_wire_shape(self) -> None:
        """A read is already tenant-scoped; echoing ``org_id`` would only leak it."""

        assert "org_id" not in ContextOccupancySnapshotPayload.model_fields
        assert "org_id" not in ContextOccupancySnapshotPayload.from_record(
            self.record()
        ).model_dump(mode="json")

    def test_free_tokens_matches_the_records_own_derivation(self) -> None:
        """One rule for free space, so the read API cannot disagree with the row."""

        record = self.record(context_window_tokens=8_000)
        payload = ContextOccupancySnapshotPayload.from_record(record)

        assert payload.free_tokens == record.free_tokens

    def test_free_tokens_is_none_when_the_window_is_unknown(self) -> None:
        """``None`` states we do not know the denominator; ``0`` would assert a full window."""

        payload = ContextOccupancySnapshotPayload.from_record(
            self.record(context_window_tokens=None)
        )

        assert payload.free_tokens is None

    def test_free_tokens_may_be_negative(self) -> None:
        """A request over the window we believe the model has is worth seeing.

        Clamping would hide a stale pricing row behind a plausible number.
        """

        payload = ContextOccupancySnapshotPayload.from_record(
            self.record(context_window_tokens=500, provider_input_tokens=1_010)
        )

        assert payload.free_tokens == -510

    def test_unreported_provider_usage_keeps_the_delta_at_zero(self) -> None:
        """A delta against an absent total is no measurement, not a small residual."""

        payload = ContextOccupancySnapshotPayload.from_record(
            self.record(provider_input_tokens=None, cached_input_tokens=0)
        )

        assert payload.provider_input_tokens is None
        assert payload.unattributed_delta == 0
        # With no provider total, free space falls back to our own estimate.
        assert payload.free_tokens == 400_000 - 971


class TestSegmentParsingIsFailOpen(OccupancyRecordMixin):
    """One unreadable segment costs that segment and nothing else (§6.4)."""

    def test_unknown_field_drops_only_the_offending_segment(self) -> None:
        record = self.record(
            segments=(
                self.SEGMENT,
                {**self.SEGMENT, "label": "x:y", "invented_by_a_newer_writer": 1},
            )
        )

        payload = ContextOccupancySnapshotPayload.from_record(record)

        assert [segment.label for segment in payload.segments] == [
            "agent_runtime.prompts:default_instructions"
        ]
        assert payload.unreadable_segment_count == 1

    def test_unknown_vocabulary_value_drops_only_that_segment(self) -> None:
        """The structural taxonomy is closed; a fifth class means a newer writer."""

        record = self.record(
            segments=(self.SEGMENT, {**self.SEGMENT, "segment_class": "sidecar"})
        )

        payload = ContextOccupancySnapshotPayload.from_record(record)

        assert len(payload.segments) == 1
        assert payload.unreadable_segment_count == 1

    def test_rollups_stay_exact_when_segments_are_unreadable(self) -> None:
        """Totals are stored columns, never sums of the list this reader parsed."""

        record = self.record(
            segments=({**self.SEGMENT, "invented": True},),
            estimated_input_tokens=971,
        )

        payload = ContextOccupancySnapshotPayload.from_record(record)

        assert payload.segments == ()
        assert payload.unreadable_segment_count == 1
        assert payload.estimated_input_tokens == 971
        assert payload.undeclared_tokens == 0

    def test_a_row_with_no_segments_is_a_valid_partial_snapshot(self) -> None:
        """The fail-open capture path can write totals with nothing decomposed."""

        payload = ContextOccupancySnapshotPayload.from_record(self.record(segments=()))

        assert payload.segments == ()
        assert payload.unreadable_segment_count == 0


class TestSegmentContract(OccupancyRecordMixin):
    """The wire bounds are the durability bounds, so nothing readable is unwritable."""

    def test_bounds_are_inherited_from_the_persistence_boundary(self) -> None:
        assert (
            ContextOccupancySegment.Limits.MAX_TEXT
            == RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_TEXT_CHARS
        )

    def test_a_detail_at_the_stored_bound_round_trips(self) -> None:
        """A value the write path accepted must never be unreadable."""

        longest = "a" * RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_TEXT_CHARS

        segment = ContextOccupancySegment.from_stored(
            {**self.SEGMENT, "detail": longest}
        )

        assert segment.detail == longest

    def test_a_detail_beyond_the_stored_bound_is_refused(self) -> None:
        """Past the persistence bound it is content, and no row could hold it."""

        too_long = "a" * (
            RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_TEXT_CHARS + 1
        )

        with pytest.raises(ValidationError):
            ContextOccupancySegment.from_stored({**self.SEGMENT, "detail": too_long})

    def test_missing_counter_source_is_refused(self) -> None:
        """A tokenizer count and a fail-open proxy are not interchangeable."""

        stored = {key: value for key, value in self.SEGMENT.items()}
        stored.pop("counter_source")

        with pytest.raises(ValidationError):
            ContextOccupancySegment.from_stored(stored)

    @pytest.mark.parametrize(
        "lifecycle", ["resident", "per_turn", "per_result", "on_demand"]
    )
    def test_every_declared_lifecycle_is_representable(self, lifecycle: str) -> None:
        """The report is actionable only if all four survive to the client."""

        segment = ContextOccupancySegment.from_stored(
            {**self.SEGMENT, "lifecycle": lifecycle}
        )

        assert segment.lifecycle.value == lifecycle


class TestResponseEnvelopes(OccupancyRecordMixin):
    """The two response shapes default to honest emptiness."""

    def test_run_response_defaults_to_an_unfiltered_empty_series(self) -> None:
        response = ContextOccupancyResponse(run_id="run_schema")

        assert response.graph_scope is None
        assert response.snapshots == ()

    def test_run_response_echoes_the_applied_scope_filter(self) -> None:
        """The series is summable only within a scope, so the filter is part of the answer."""

        response = ContextOccupancyResponse(
            run_id="run_schema",
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
        )

        assert response.graph_scope is RuntimeContextGraphScope.SUBAGENT

    def test_conversation_response_separates_no_snapshot_from_no_run(self) -> None:
        response = ConversationContextOccupancyResponse(conversation_id="conv_schema")

        assert response.run_id is None
        assert response.snapshot is None

    def test_conversation_response_names_the_run_a_snapshot_came_from(self) -> None:
        response = ConversationContextOccupancyResponse(
            conversation_id="conv_schema",
            run_id="run_schema",
            snapshot=ContextOccupancySnapshotPayload.from_record(self.record()),
        )

        assert response.run_id == "run_schema"
        assert response.snapshot is not None
        assert response.snapshot.model_call_id == "call_schema"
