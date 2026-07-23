"""Projector allow-lists for the four A3 ledger events (PRD-A3 D5).

Each ``payload_for_event`` branch is a strict allow-list: only the SDR §5 keys
survive with type checks, unknown keys drop, nested ``source`` / ``gen`` rebuild
from their own allow-lists. All four project to ``RuntimeActivityKind.EVENT``.
``read.executed`` / ``surface.created`` carry a ``*_ref`` key ⇒ OFFLOADED;
``action.classified`` has none ⇒ not OFFLOADED.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas.common import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
)
from runtime_api.schemas.events import RuntimeEventPresentationProjector as P


class TestActionClassifiedProjection:
    def test_keeps_only_allowed_keys(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.ACTION_CLASSIFIED,
            payload={
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "class": "unknown",
                "basis": "default",
                "secret": "leak",
                "org_id": "org_x",
            },
        )
        assert safe == {
            "v": 1,
            "call_id": "c1",
            "connector": "linear",
            "op": "get_issue",
            "class": "unknown",
            "basis": "default",
        }

    def test_bad_version_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.ACTION_CLASSIFIED,
            payload={"v": True, "call_id": "c1"},
        )
        assert "v" not in safe
        assert safe["call_id"] == "c1"


class TestReadExecutedProjection:
    def test_keeps_latency_and_payload_ref(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.READ_EXECUTED,
            payload={
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 15,
                "payload_ref": "call:c1",
                "extra": "drop",
            },
        )
        assert safe == {
            "v": 1,
            "call_id": "c1",
            "connector": "linear",
            "op": "get_issue",
            "latency_ms": 15,
            "payload_ref": "call:c1",
        }

    def test_negative_latency_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.READ_EXECUTED,
            payload={"v": 1, "latency_ms": -5, "payload_ref": "call:c1"},
        )
        assert "latency_ms" not in safe


class TestSurfaceCreatedProjection:
    def test_source_rebuilt_from_nested_allow_list(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": "record://linear/get_issue/1",
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue", "evil": "x"},
                "title": "ENG-1",
                "payload_ref": "call:c1",
                "junk": {"nested": "drop"},
            },
        )
        assert safe["source"] == {"connector": "linear", "op": "get_issue"}
        assert "junk" not in safe
        assert safe["surface_id"] == "record://linear/get_issue/1"

    def test_malformed_source_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={"v": 1, "surface_id": "s1", "source": {"connector": "linear"}},
        )
        assert "source" not in safe


class TestViewDerivedProjection:
    def test_gen_rebuilt_and_ms_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={
                "v": 1,
                "surface_id": "s1",
                "tier": "shaped",
                "basis": "generated",
                "spec_ref": "spec/x",
                "gen": {"model": "gpt-5.4-mini", "ms": 820, "extra": "x"},
            },
        )
        assert safe["gen"] == {"model": "gpt-5.4-mini"}
        assert safe["spec_ref"] == "spec/x"
        assert safe["tier"] == "shaped"

    def test_gen_without_model_dropped(self) -> None:
        safe = P.payload_for_event(
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={"v": 1, "surface_id": "s1", "tier": "generic", "gen": {"ms": 1}},
        )
        assert "gen" not in safe


class TestActivityKindAndRedaction:
    def test_all_four_project_to_event_activity(self) -> None:
        for event_type in (
            RuntimeApiEventType.ACTION_CLASSIFIED,
            RuntimeApiEventType.READ_EXECUTED,
            RuntimeApiEventType.SURFACE_CREATED,
            RuntimeApiEventType.VIEW_DERIVED,
        ):
            # Even a TOOL-sourced emit must not reroute into the tool bucket.
            assert (
                P.activity_kind_for(
                    event_type=event_type, source=StreamEventSource.TOOL
                )
                is RuntimeActivityKind.EVENT
            )

    def test_read_executed_payload_marked_offloaded(self) -> None:
        state = P._redaction_state_for(
            payload={"v": 1, "payload_ref": "call:c1"}, metadata={}
        )
        assert state is RuntimeEventRedactionState.OFFLOADED

    def test_action_classified_not_offloaded(self) -> None:
        state = P._redaction_state_for(
            payload={"v": 1, "call_id": "c1", "class": "unknown"}, metadata={}
        )
        assert state is not RuntimeEventRedactionState.OFFLOADED
