"""Durable contract for the Context Occupancy Ledger's persistence record.

The record is what survives the run, so the invariants worth pinning here are
the ones a later reader cannot re-derive: that the two residuals of design §4.4
mean what they claim, that ``None`` never silently becomes ``0``, and that
segments carry identifiers rather than content (§6.5) — the last one matters
because this table is exposed over an HTTP read API.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.observability.context_occupancy import GraphScope
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)


class ContextOccupancyRecordMixin:
    """Shared valid-row builder plus the constants the assertions read."""

    class Values:
        ORG_ID = "org-a"
        RUN_ID = "run-a"
        CONVERSATION_ID = "conversation-a"
        MODEL_CALL_ID = "model-call-a"
        ASSEMBLY_RECORD_ID = "prompt_assembled:" + "a" * 64
        PROVIDER = "anthropic"
        MODEL_FAMILY = "claude-opus-5"
        WINDOW = 200_000
        CREATED_AT = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        PUBLISH_ARTIFACT_LABEL = "agent_runtime.capabilities.backends:publish_artifact"

    def segment(
        self,
        *,
        label: str | None = None,
        estimated_tokens: int = 650,
        detail: str | None = "publish_artifact",
    ) -> dict[str, object]:
        return {
            "segment_class": "tools",
            "label": label or self.Values.PUBLISH_ARTIFACT_LABEL,
            "lifecycle": "resident",
            "detail": detail,
            "byte_count": 2_600,
            "estimated_tokens": estimated_tokens,
            "item_count": 1,
            "counter_source": "tokenizer",
        }

    def record(self, **overrides: object) -> RuntimeContextOccupancyRecord:
        values: dict[str, object] = {
            "org_id": self.Values.ORG_ID,
            "run_id": self.Values.RUN_ID,
            "conversation_id": self.Values.CONVERSATION_ID,
            "model_call_id": self.Values.MODEL_CALL_ID,
            "provider": self.Values.PROVIDER,
            "model_family": self.Values.MODEL_FAMILY,
            "context_window_tokens": self.Values.WINDOW,
            "estimated_input_tokens": 1_200,
            "provider_input_tokens": 1_180,
            "segments": (self.segment(),),
            "created_at": self.Values.CREATED_AT,
        }
        values.update(overrides)
        return RuntimeContextOccupancyRecord.from_measurement(**values)  # type: ignore[arg-type]


class TestContextOccupancyRecordShape(ContextOccupancyRecordMixin):
    def test_valid_snapshot_normalizes_provider_and_envelope(self) -> None:
        record = self.record(provider="  Anthropic  ")

        assert record.provider == self.Values.PROVIDER
        assert record.schema_version == 1
        assert record.graph_scope is RuntimeContextGraphScope.ROOT
        assert record.idempotency_key == (self.Values.MODEL_CALL_ID, 1)
        assert record.segment_count == 1
        assert record.segments[0]["label"] == self.Values.PUBLISH_ARTIFACT_LABEL
        # The envelope is canonical whatever the caller passed, so a reader
        # never has to distinguish "no segments key" from "empty list".
        assert set(record.segments_json) == {
            RuntimeContextOccupancyRecord.Keys.SEGMENTS
        }

    def test_empty_measurement_still_yields_the_canonical_envelope(self) -> None:
        record = RuntimeContextOccupancyRecord(
            org_id=self.Values.ORG_ID,
            run_id=self.Values.RUN_ID,
            conversation_id=self.Values.CONVERSATION_ID,
            model_call_id=self.Values.MODEL_CALL_ID,
            provider=self.Values.PROVIDER,
            model_family=self.Values.MODEL_FAMILY,
        )

        assert record.segments == ()
        assert record.segments_json == {RuntimeContextOccupancyRecord.Keys.SEGMENTS: []}
        # No window and no provider total: every derived answer is "unknown",
        # never a confident zero.
        assert record.free_tokens is None
        assert record.provider_input_tokens is None
        assert record.unattributed_delta == 0

    def test_blank_assembly_link_is_stored_as_absent(self) -> None:
        assert self.record(assembly_record_id="   ").assembly_record_id is None
        assert (
            self.record(
                assembly_record_id=self.Values.ASSEMBLY_RECORD_ID
            ).assembly_record_id
            == self.Values.ASSEMBLY_RECORD_ID
        )

    def test_record_is_frozen_like_every_observation_row(self) -> None:
        record = self.record()

        with pytest.raises(ValidationError):
            record.estimated_input_tokens = 9  # type: ignore[misc]

    def test_naive_created_at_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            self.record(created_at=datetime(2026, 7, 29, 12, 0))

    def test_blank_provider_and_model_family_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="provider must be non-empty"):
            self.record(provider="   ")
        with pytest.raises(ValidationError, match="model_family must be non-empty"):
            self.record(model_family="   ")


class TestContextOccupancyResiduals(ContextOccupancyRecordMixin):
    def test_signed_delta_round_trips_in_both_directions(self) -> None:
        over_counted = self.record(
            estimated_input_tokens=1_200, provider_input_tokens=1_180
        )
        under_counted = self.record(
            estimated_input_tokens=1_180, provider_input_tokens=1_200
        )

        # Negative is the honest report that our tokenizer over-counted; the
        # field is never clamped, because clamping hides the drift it exists
        # to expose.
        assert over_counted.unattributed_delta == -20
        assert under_counted.unattributed_delta == 20

        for record in (over_counted, under_counted):
            restored = RuntimeContextOccupancyRecord.model_validate(
                record.model_dump(mode="json")
            )
            assert restored == record
            assert restored.unattributed_delta == record.unattributed_delta

    def test_delta_is_zero_when_the_provider_reported_nothing(self) -> None:
        record = self.record(provider_input_tokens=None)

        assert record.unattributed_delta == 0
        # A delta against an absent total is not a small residual, it is no
        # measurement at all.
        with pytest.raises(ValidationError, match="requires a provider input total"):
            RuntimeContextOccupancyRecord.model_validate(
                {**record.model_dump(mode="json"), "unattributed_delta": -7}
            )

    def test_a_delta_that_does_not_reconcile_is_refused(self) -> None:
        record = self.record()

        with pytest.raises(ValidationError, match="unattributed delta must equal"):
            RuntimeContextOccupancyRecord.model_validate(
                {**record.model_dump(mode="json"), "unattributed_delta": 5}
            )

    def test_undeclared_tokens_cannot_exceed_what_was_measured(self) -> None:
        # undeclared bytes are measured bytes, so they are a subset of the
        # estimate — a larger value means the two counts came from different
        # passes.
        assert self.record(undeclared_tokens=650).undeclared_tokens == 650
        with pytest.raises(ValidationError, match="undeclared tokens exceed"):
            self.record(undeclared_tokens=1_201)

    def test_cache_subsets_cannot_exceed_the_gross_provider_input(self) -> None:
        record = self.record(cached_input_tokens=1_000, cache_creation_input_tokens=180)

        assert record.cached_input_tokens == 1_000
        with pytest.raises(ValidationError, match="cache token subsets exceed"):
            self.record(cached_input_tokens=1_000, cache_creation_input_tokens=181)

    def test_free_tokens_is_none_when_the_model_is_absent_from_pricing(self) -> None:
        assert self.record(context_window_tokens=None).free_tokens is None
        assert self.record().free_tokens == self.Values.WINDOW - 1_180
        # Falls back to our estimate rather than reporting a full window when
        # the provider never answered.
        assert (
            self.record(provider_input_tokens=None).free_tokens
            == self.Values.WINDOW - 1_200
        )


class TestContextOccupancyScope(ContextOccupancyRecordMixin):
    def test_persisted_scope_vocabulary_matches_the_domain_enum(self) -> None:
        # The persistence enum is a deliberate duplicate of the observability
        # one (persistence.records is a leaf layer). This is the gate that
        # keeps the duplicate honest: a third scope upstream fails here, and
        # the CHECK constraint on runtime_context_occupancy has to move too.
        assert {scope.value for scope in RuntimeContextGraphScope} == {
            scope.value for scope in GraphScope
        }

    def test_a_domain_scope_projects_onto_the_column_vocabulary(self) -> None:
        record = self.record(graph_scope=GraphScope.SUBAGENT)

        assert record.graph_scope is RuntimeContextGraphScope.SUBAGENT

    def test_an_unknown_scope_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            self.record(graph_scope="orchestrator")


class TestContextOccupancySegmentsAreContentFree(ContextOccupancyRecordMixin):
    def test_a_segment_carrying_message_content_is_refused(self) -> None:
        leaked = self.segment(
            detail="x"
            * (RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_TEXT_CHARS + 1)
        )

        with pytest.raises(ValidationError, match="identifiers only"):
            self.record(segments=(leaked,))

    def test_nested_content_is_refused_too(self) -> None:
        nested = {
            **self.segment(),
            "notes": [
                "y" * (RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_TEXT_CHARS + 1)
            ],
        }

        with pytest.raises(ValidationError, match="identifiers only"):
            self.record(segments=(nested,))

    def test_an_unbounded_segment_list_is_refused(self) -> None:
        too_many = tuple(
            self.segment(label=f"owner:tool-{index}", estimated_tokens=1)
            for index in range(RuntimeContextOccupancyRecord.Limits.MAX_SEGMENTS + 1)
        )

        with pytest.raises(ValidationError, match="segment count exceeds"):
            self.record(segments=too_many, estimated_input_tokens=0)

    def test_unknown_envelope_keys_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="unknown keys"):
            RuntimeContextOccupancyRecord.model_validate(
                {
                    **self.record().model_dump(mode="json"),
                    "segments_json": {"segments": [], "raw_prompt": "hello"},
                }
            )

    def test_a_non_object_segment_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be an object"):
            RuntimeContextOccupancyRecord.model_validate(
                {
                    **self.record().model_dump(mode="json"),
                    "segments_json": {"segments": ["publish_artifact"]},
                }
            )
