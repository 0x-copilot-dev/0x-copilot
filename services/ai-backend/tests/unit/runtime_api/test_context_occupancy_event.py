"""The ``context_occupancy`` transport event and its presentation projection.

Two separable concerns, one per class:

- **Contract.** The payload wraps the *same* snapshot shape the read API
  returns, is content-free by construction, and rejects anything else — a
  malformed payload projects to ``{}`` rather than reaching a client half-formed.
- **Projection.** ``activity_kind`` is decided explicitly by the backend, never
  inferred from the event name or the emitting source. An occupancy meter is
  state to merge, so it must land in ``EVENT`` even though measurement happens
  inside a ``MODEL``-sourced call.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from runtime_api.schemas import (
    ContextOccupancyPayload,
    ContextOccupancySnapshotPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)


class OccupancyEventFixtureMixin:
    """One realistic snapshot, reachable from every case below."""

    SEGMENT = {
        "segment_class": "tools",
        "label": "agent_runtime.capabilities.backends:publish_artifact",
        "lifecycle": "resident",
        "third_party": False,
        "detail": "publish_artifact",
        "byte_count": 2_600,
        "estimated_tokens": 650,
        "item_count": 1,
        "cache_eligibility": "stable_prefix",
        "counter_source": "tokenizer",
    }

    def snapshot(self) -> ContextOccupancySnapshotPayload:
        record = RuntimeContextOccupancyRecord.from_measurement(
            org_id="org_evt",
            run_id="run_evt",
            conversation_id="conv_evt",
            model_call_id="call_evt",
            graph_scope=RuntimeContextGraphScope.ROOT,
            provider="anthropic",
            model_family="claude-opus-4-7",
            context_window_tokens=200_000,
            estimated_input_tokens=950,
            provider_input_tokens=1_000,
            segments=(self.SEGMENT,),
            created_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        )
        return ContextOccupancySnapshotPayload.from_record(record)

    def payload(self) -> dict[str, object]:
        return ContextOccupancyPayload(snapshot=self.snapshot()).model_dump(mode="json")


class TestContextOccupancyPayloadContract(OccupancyEventFixtureMixin):
    """The stream and the read API share one shape, and it carries no content."""

    def test_payload_wraps_the_same_snapshot_the_read_api_returns(self) -> None:
        """A client folds SSE and fetch through one reducer, not two."""

        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.CONTEXT_OCCUPANCY,
            payload=self.payload(),
        )

        assert set(projected) == {"snapshot"}
        assert set(projected["snapshot"]) == set(
            ContextOccupancySnapshotPayload.model_fields
        )
        assert projected["snapshot"]["model_call_id"] == "call_evt"
        assert projected["snapshot"]["graph_scope"] == "root"
        assert projected["snapshot"]["free_tokens"] == 199_000

    def test_segment_detail_carries_an_identifier_never_content(self) -> None:
        """§6.5 — occupancy is externally readable, so this is a wire invariant."""

        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.CONTEXT_OCCUPANCY,
            payload=self.payload(),
        )

        segment = projected["snapshot"]["segments"][0]
        assert segment["detail"] == "publish_artifact"
        assert segment["estimated_tokens"] == 650
        assert "content" not in segment
        assert "text" not in segment

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({}, id="empty"),
            pytest.param({"snapshot": {}}, id="empty-snapshot"),
            pytest.param(
                {"snapshot": {"model_call_id": "c", "prompt": "the whole system text"}},
                id="smuggled-content-field",
            ),
            pytest.param({"unexpected_key": 1}, id="unknown-key"),
        ],
    )
    def test_malformed_payload_projects_to_empty(self, payload: dict) -> None:
        """A rejected payload must not cost the run the event's ordering slot.

        The event still lands with its ``sequence_no``; only its body is dropped.
        Matching every sibling journal projector, because occupancy is
        observability and a bad measurement must never break the stream.
        """

        assert (
            RuntimeEventPresentationProjector.payload_for_event(
                event_type=RuntimeApiEventType.CONTEXT_OCCUPANCY,
                payload=payload,
            )
            == {}
        )

    def test_extra_keys_are_refused_rather_than_passed_through(self) -> None:
        """The contract IS the allow-list; nothing rides alongside it."""

        payload = self.payload()
        payload["shadow"] = {"anything": "at all"}

        assert (
            RuntimeEventPresentationProjector.payload_for_event(
                event_type=RuntimeApiEventType.CONTEXT_OCCUPANCY,
                payload=payload,
            )
            == {}
        )


class TestContextOccupancyActivityProjection(OccupancyEventFixtureMixin):
    """Display fields are projected explicitly, never inferred from the name."""

    @pytest.mark.parametrize(
        "source",
        [
            StreamEventSource.MODEL,
            StreamEventSource.RUNTIME,
            StreamEventSource.TOOL,
            StreamEventSource.SUBAGENT,
        ],
    )
    def test_activity_kind_is_event_regardless_of_source(
        self,
        source: StreamEventSource,
    ) -> None:
        """Measurement happens inside a MODEL-sourced call, and TOOL/SUBAGENT
        sources both have their own default buckets. An explicit branch is what
        stops an occupancy meter turning into a timeline card or a message."""

        assert (
            RuntimeEventPresentationProjector.activity_kind_for(
                event_type=RuntimeApiEventType.CONTEXT_OCCUPANCY,
                source=source,
            )
            is RuntimeActivityKind.EVENT
        )

    def test_no_display_title_or_status_is_projected(self) -> None:
        """There is no per-turn "measured the window" beat to render."""

        fields = RuntimeEventPresentationProjector.presentation_fields(
            event_type=RuntimeApiEventType.CONTEXT_OCCUPANCY,
            source=StreamEventSource.MODEL,
            parent_task_id=None,
            payload=self.payload(),
            metadata={},
        )

        assert fields["display_title"] is None
        assert fields["status"] is None
        assert fields["activity_kind"] is RuntimeActivityKind.EVENT

    def test_wire_value_is_the_stable_event_name(self) -> None:
        """Pinned: the transport name is mirrored in ``packages/api-types``."""

        assert RuntimeApiEventType.CONTEXT_OCCUPANCY.value == "context_occupancy"
