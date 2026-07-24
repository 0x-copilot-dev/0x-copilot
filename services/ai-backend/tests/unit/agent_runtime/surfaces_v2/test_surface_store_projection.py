"""``SurfaceStoreProjection.fold`` — the pure, rebuildable canvas fold (PRD-A3 D6).

Pins the A1 golden fixture against a checked-in expected state (the exact
`(golden events, expected state)` pair B1's TypeScript fold must reproduce),
plus the deterministic fold invariants: repeat ``surface.created`` upserts while
keeping the first ``ledger_id``; future vocabulary + junk types are skipped
without error; a ``view.derived`` for an unseen surface is ignored; out-of-order
input is sorted by ``sequence_no``.
"""

from __future__ import annotations

import json
from pathlib import Path

from copilot_service_contracts.work_ledger import load_ledger_golden_events

from agent_runtime.surfaces_v2.projection import (
    SurfaceStoreProjection,
    SurfaceStoreState,
)

_GOLDEN_STATE_PATH = (
    Path(__file__).parent / "fixtures" / "surface_store_golden_state.json"
)


class GoldenFoldMixin:
    @staticmethod
    def _expected_state() -> dict[str, object]:
        return json.loads(_GOLDEN_STATE_PATH.read_text(encoding="utf-8"))

    @staticmethod
    def _event(
        event_type: str,
        sequence_no: int,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return {
            "event_type": event_type,
            "sequence_no": sequence_no,
            "payload": payload,
        }


class TestGoldenFold(GoldenFoldMixin):
    def test_golden_events_fold_matches_expected_snapshot(self) -> None:
        golden = load_ledger_golden_events()
        state = SurfaceStoreProjection.fold_raw(golden["run_id"], golden["events"])

        assert state.model_dump(mode="json") == self._expected_state()

    def test_expected_state_round_trips_through_the_model(self) -> None:
        # The fixture is a valid ``SurfaceStoreState`` (guards against a hand
        # edit that drifts the fold's own contract).
        state = SurfaceStoreState.model_validate(self._expected_state())
        assert state.latest_sequence_no == 22
        assert [s.surface_id for s in state.surfaces] == [
            "surface_issue",
            "surface_receipt",
        ]


class TestFoldInvariants(GoldenFoldMixin):
    def test_repeat_surface_created_upserts_keeping_first_ledger_id(self) -> None:
        run_id = "a7f3c9d2e5b14f60"
        events = [
            self._event(
                "surface.created",
                3,
                {
                    "v": 1,
                    "surface_id": "s1",
                    "kind": "record",
                    "source": {"connector": "linear", "op": "get_issue"},
                    "title": "First title",
                    "payload_ref": "call:c1",
                },
            ),
            self._event(
                "surface.created",
                9,
                {
                    "v": 1,
                    "surface_id": "s1",
                    "kind": "record",
                    "source": {"connector": "linear", "op": "get_issue"},
                    "title": "Refreshed title",
                    "payload_ref": "call:c2",
                },
            ),
        ]

        state = SurfaceStoreProjection.fold_raw(run_id, events)

        assert len(state.surfaces) == 1
        snap = state.surfaces[0]
        assert snap.title == "Refreshed title"
        assert snap.payload_ref == "call:c2"
        assert snap.first_sequence_no == 3
        assert snap.last_sequence_no == 9
        # ledger_id anchors on the FIRST sequence.
        assert snap.ledger_id == "ra7f·003"

    def test_future_vocabulary_and_junk_types_are_skipped(self) -> None:
        run_id = "abc123def456"
        events = [
            self._event("gate.opened", 1, {"v": 1, "gate_id": "g1"}),
            self._event(
                "surface.created",
                2,
                {
                    "v": 1,
                    "surface_id": "s1",
                    "kind": "record",
                    "source": {"connector": "c", "op": "o"},
                    "title": "T",
                    "payload_ref": "call:c1",
                },
            ),
            self._event("decision.recorded", 3, {"v": 1}),
            self._event("totally.made.up", 4, {"anything": True}),
            self._event("usage.recorded", 5, {"v": 1, "purpose": "run"}),
        ]

        state = SurfaceStoreProjection.fold_raw(run_id, events)

        assert [s.surface_id for s in state.surfaces] == ["s1"]
        assert state.latest_sequence_no == 5  # watermark counts ALL events

    def test_view_derived_for_unknown_surface_is_ignored(self) -> None:
        run_id = "abc123def456"
        events = [
            self._event(
                "view.derived",
                1,
                {"v": 1, "surface_id": "ghost", "tier": "shaped", "basis": "registry"},
            ),
        ]

        state = SurfaceStoreProjection.fold_raw(run_id, events)

        assert state.surfaces == ()
        assert state.latest_sequence_no == 1

    def test_out_of_order_input_is_sorted_by_sequence_no(self) -> None:
        run_id = "abc123def456"
        events = [
            self._event(
                "view.derived",
                3,
                {
                    "v": 1,
                    "surface_id": "s1",
                    "tier": "shaped",
                    "basis": "generated",
                    "gen": {"model": "m2"},
                },
            ),
            self._event(
                "surface.created",
                1,
                {
                    "v": 1,
                    "surface_id": "s1",
                    "kind": "record",
                    "source": {"connector": "c", "op": "o"},
                    "title": "T",
                    "payload_ref": "call:c1",
                },
            ),
            self._event(
                "view.derived",
                2,
                {"v": 1, "surface_id": "s1", "tier": "generic", "basis": "schema"},
            ),
        ]

        state = SurfaceStoreProjection.fold_raw(run_id, events)

        assert len(state.surfaces) == 1
        # Sorted: created(1) then generic(2) then generated(3) — last view wins.
        assert state.surfaces[0].view is not None
        assert state.surfaces[0].view.tier == "shaped"
        assert state.surfaces[0].view.basis == "generated"
        assert state.surfaces[0].view.generator_model == "m2"
        assert state.surfaces[0].last_sequence_no == 3
