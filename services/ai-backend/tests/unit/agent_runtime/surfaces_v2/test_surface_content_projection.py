"""``SurfaceContentProjection`` — the B2 content-hydration fold (pure).

The Python twin of chat-surface's ``applySurfaceEvent``: resolve each v2
surface's materialized ``{spec?, data}`` from the run's v1 surface-envelope
events, keyed by ``surface_uri`` (== ``surface.created.surface_id``). Total +
deterministic: unrelated/malformed events are skipped, a late ``spec`` merges
without clobbering newer ``data``, and a surface with no content event is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.surfaces_v2.content import SurfaceContentProjection


@dataclass
class _Event:
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)


def _envelope_event(uri: str, *, spec: object = None, data: object = None) -> _Event:
    state: dict[str, object] = {}
    if spec is not None:
        state["spec"] = spec
    if data is not None:
        state["data"] = data
    return _Event(
        event_type="tool_result",
        payload={
            "surface": {"surface_uri": uri, "archetype": "record", "state": state}
        },
    )


class TestSurfaceContentProjection:
    def test_resolves_state_from_tool_result_envelope(self) -> None:
        content = SurfaceContentProjection.fold(
            [_envelope_event("s1", spec={"archetype": "record"}, data={"id": 7})]
        )
        assert content == {"s1": {"spec": {"archetype": "record"}, "data": {"id": 7}}}

    def test_legacy_flat_surface_uri_and_state(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _Event(
                    event_type="draft_updated",
                    payload={"surface_uri": "s2", "state": {"data": {"n": 1}}},
                )
            ]
        )
        assert content == {"s2": {"data": {"n": 1}}}

    def test_later_event_wins_per_key_but_keeps_others(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _envelope_event("s1", data={"id": 1}),
                _envelope_event("s1", data={"id": 2}),
            ]
        )
        assert content == {"s1": {"data": {"id": 2}}}

    def test_late_spec_merges_without_clobbering_data(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _envelope_event("s1", data={"id": 1}),
                _Event(
                    event_type="surface_spec_generated",
                    payload={
                        "surface": {"surface_uri": "s1"},
                        "spec": {"archetype": "record"},
                    },
                ),
            ]
        )
        assert content == {"s1": {"data": {"id": 1}, "spec": {"archetype": "record"}}}

    def test_surface_with_no_content_event_is_absent(self) -> None:
        # A ledger-only surface (surface.created but no tool_result envelope yet)
        # produces no content entry — honest "not hydrated", never fabricated.
        content = SurfaceContentProjection.fold(
            [
                _Event(
                    event_type="surface.created",
                    payload={"surface_id": "s1", "kind": "record"},
                )
            ]
        )
        assert content == {}

    def test_malformed_and_unrelated_events_are_skipped(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _Event(event_type="tool_result", payload={"surface": "not-a-mapping"}),
                _Event(event_type="tool_result", payload={"surface": {"state": {}}}),
                _Event(event_type="model_delta", payload={"text": "hi"}),
                _Event(event_type="surface_spec_generated", payload={"spec": "x"}),
            ]
        )
        assert content == {}

    def test_fold_is_deterministic_and_does_not_mutate_input(self) -> None:
        events = [_envelope_event("s1", data={"id": 1})]
        first = SurfaceContentProjection.fold(events)
        second = SurfaceContentProjection.fold(events)
        assert first == second
        # The source event payload is untouched.
        assert events[0].payload["surface"]["state"] == {"data": {"id": 1}}
