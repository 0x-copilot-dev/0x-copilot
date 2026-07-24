"""Declared-reference content hydration — pure, total, and envelope-free."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.surfaces_v2.content import SurfaceContentProjection


@dataclass
class _Event:
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)


def _surface(surface_id: str, call_id: str) -> _Event:
    return _Event(
        event_type="surface.created",
        payload={"surface_id": surface_id, "payload_ref": f"call:{call_id}"},
    )


def _tool_result(call_id: str, output: object) -> _Event:
    return _Event(
        event_type="tool_result",
        payload={"call_id": call_id, "output": output},
    )


class TestSurfaceContentProjection:
    def test_resolves_only_the_declared_tool_result_reference(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _tool_result("other", {"ignored": True}),
                _surface("s1", "call-1"),
                _tool_result("call-1", {"id": 7}),
            ]
        )
        assert content == {"s1": {"data": {"id": 7}}}

    def test_explicit_snapshot_ref_limits_hydration_to_authorized_subjects(
        self,
    ) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1"),
                _surface("s2", "call-2"),
                _tool_result("call-1", 1),
                _tool_result("call-2", 2),
            ],
            surface_payload_refs={"s2": "call:call-2"},
        )
        assert content == {"s2": {"data": 2}}

    def test_spec_merges_by_declared_surface_identity(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1"),
                _tool_result("call-1", {"id": 1}),
                _Event(
                    event_type="surface_spec_generated",
                    payload={"surface_id": "s1", "spec": {"archetype": "record"}},
                ),
            ]
        )
        assert content == {"s1": {"data": {"id": 1}, "spec": {"archetype": "record"}}}

    def test_legacy_presentation_envelope_is_ignored(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1"),
                _Event(
                    event_type="tool_result",
                    payload={
                        "surface": {
                            "surface_uri": "s1",
                            "state": {"data": {"forged": True}},
                        },
                    },
                ),
            ]
        )
        assert content == {}

    def test_missing_or_malformed_refs_are_honestly_unhydrated(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _Event(
                    event_type="surface.created",
                    payload={"surface_id": "s1", "payload_ref": "blob:elsewhere"},
                ),
                _Event(event_type="tool_result", payload={"call_id": "call-1"}),
                _Event(
                    event_type="surface_spec_generated",
                    payload={"surface_id": "unknown", "spec": {}},
                ),
            ]
        )
        assert content == {}

    def test_fold_is_deterministic_and_does_not_mutate_input(self) -> None:
        events = [_surface("s1", "call-1"), _tool_result("call-1", {"id": 1})]
        first = SurfaceContentProjection.fold(events)
        second = SurfaceContentProjection.fold(events)
        assert first == second == {"s1": {"data": {"id": 1}}}
        assert events[1].payload == {"call_id": "call-1", "output": {"id": 1}}
