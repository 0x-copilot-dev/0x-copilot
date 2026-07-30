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

from agent_runtime.observability.context_occupancy import ContextSegment
from agent_runtime.observability.context_origin import (
    MAX_LABEL_LENGTH as MAX_CONTEXT_LABEL_LENGTH,
)
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
    """The wire bounds mirror the producer's, so the read is never wider (§6.5).

    The invariant these used to assert was "the wire bound equals the durability
    bound", which sounded conservative and was the hole: the durability envelope
    applies one width to every string in a segment, so it has to admit a
    401-character ``owner:name`` label — and reading ``detail`` under that same
    width published 512 characters of arbitrary text for a field whose only
    producer clips it to 200 printable characters from an untrusted MCP tool name.
    """

    def test_label_bound_is_exactly_what_a_declaration_can_spell(self) -> None:
        assert ContextOccupancySegment.Limits.MAX_LABEL == MAX_CONTEXT_LABEL_LENGTH

    def test_detail_bound_is_exactly_what_a_measurement_can_emit(self) -> None:
        """Producer, column, and wire agree on one width, or one of them leaks."""

        assert (
            ContextOccupancySegment.Limits.MAX_DETAIL
            == ContextSegment.MAX_DETAIL_LENGTH
            == RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_DETAIL_CHARS
        )

    def test_a_detail_at_the_producer_bound_round_trips(self) -> None:
        """A value the measurement path can emit must never be unreadable."""

        longest = "a" * ContextSegment.MAX_DETAIL_LENGTH

        segment = ContextOccupancySegment.from_stored(
            {**self.SEGMENT, "detail": longest}
        )

        assert segment.detail == longest

    def test_a_detail_beyond_the_producer_bound_is_refused(self) -> None:
        """Past the bound no measurement produced it, so it reads as content."""

        too_long = "a" * (ContextSegment.MAX_DETAIL_LENGTH + 1)

        with pytest.raises(ValidationError):
            ContextOccupancySegment.from_stored({**self.SEGMENT, "detail": too_long})

    @pytest.mark.parametrize(
        "smuggled",
        [
            pytest.param("tool\nSSN 123-45-6789", id="newline"),
            pytest.param("tool\r\nrow 2", id="crlf"),
            pytest.param("tool\tcolumn", id="tab"),
            pytest.param("tool\x00null", id="nul"),
            pytest.param("tool\x7fdel", id="delete"),
        ],
    )
    def test_a_multi_line_detail_is_refused_even_within_the_bound(
        self,
        smuggled: str,
    ) -> None:
        """The sharp half of §6.5: a length bound cannot express "one token".

        Every detail this runtime produces is a single printable token. Content
        pasted into the field is short enough to pass a length check and still be
        content, so the wire contract restates the closed check ``ContextSegment``
        applies at measurement time.
        """

        with pytest.raises(ValidationError):
            ContextOccupancySegment.from_stored({**self.SEGMENT, "detail": smuggled})

    def test_identifier_fields_are_bounded_not_bare_strings(self) -> None:
        """This contract is also the stream payload's allow-list — so it must bound.

        ``events.RuntimeEventPresentationProjector`` validates-and-re-dumps a
        ``context_occupancy`` payload instead of allow-listing keys, on the
        reasoning that the shape *is* the allow-list. An unbounded ``str`` field
        would make that reasoning false: it is a text channel through a §6.5
        surface no matter how strict its neighbours are.
        """

        over = "x" * (ContextOccupancySnapshotPayload.Limits.MAX_IDENTIFIER + 1)
        for field, value in (
            ("model_call_id", over),
            ("model_family", over),
            ("assembly_record_id", over),
            (
                "provider",
                "y" * (ContextOccupancySnapshotPayload.Limits.MAX_PROVIDER + 1),
            ),
        ):
            base = ContextOccupancySnapshotPayload.from_record(self.record())
            values = base.model_dump(mode="json")
            values[field] = value
            with pytest.raises(ValidationError):
                ContextOccupancySnapshotPayload.model_validate(values)

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
