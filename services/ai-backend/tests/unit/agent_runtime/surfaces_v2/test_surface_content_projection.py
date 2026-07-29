"""Declared-reference content hydration — pure, total, and envelope-free."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent_runtime.surfaces_v2.content import SurfaceContentProjection


@dataclass
class _Event:
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)


def _surface(surface_id: str, call_id: str, source: object = None) -> _Event:
    payload: dict[str, object] = {
        "surface_id": surface_id,
        "payload_ref": f"call:{call_id}",
    }
    if source is not None:
        payload["source"] = source
    return _Event(event_type="surface.created", payload=payload)


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

    def test_production_surface_uri_keys_generated_spec(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1"),
                _tool_result("call-1", {"id": 1}),
                _Event(
                    event_type="surface_spec_generated",
                    payload={"surface_uri": "s1", "spec": {"archetype": "record"}},
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


class TestSurfaceProvenance:
    """``state.source`` — the only thing that can name the tool on a surface
    that has no spec to name it (PRD-02 requirement 1)."""

    def test_declared_source_is_restated_in_renderer_vocabulary(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface(
                    "s1",
                    "call-1",
                    source={"connector": "pagerduty", "op": "pagerduty.incident.read"},
                ),
                _tool_result("call-1", {"id": 7}),
            ]
        )

        # The ledger says {connector, op}; the renderer contract says
        # {server, tool}. The translation lives here, not in the client.
        assert content == {
            "s1": {
                "data": {"id": 7},
                "source": {"server": "pagerduty", "tool": "pagerduty.incident.read"},
            }
        }

    def test_source_is_not_invented_for_an_undeclared_subject(self) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1", source={"connector": "linear", "op": "read"}),
                _surface("s2", "call-2", source={"connector": "github", "op": "read"}),
                _tool_result("call-1", 1),
                _tool_result("call-2", 2),
            ],
            surface_payload_refs={"s2": "call:call-2"},
        )

        assert content == {
            "s2": {"data": 2, "source": {"server": "github", "tool": "read"}}
        }

    def test_a_payload_cannot_name_its_own_tool(self) -> None:
        # Provenance is read from the ledger record the runtime wrote, never
        # from the tool's own output — otherwise any webhook body with a
        # ``source`` key could claim to be any tool.
        forged = {"source": {"server": "stripe", "tool": "stripe.charge.create"}}
        content = SurfaceContentProjection.fold(
            [_surface("s1", "call-1"), _tool_result("call-1", forged)]
        )

        # The forged key stays where it belongs: inside ``data``, which the
        # generic view paints as an ordinary field, not as provenance.
        assert content == {"s1": {"data": forged}}

    @pytest.mark.parametrize(
        "source",
        [
            None,
            "pagerduty",
            {"connector": "pagerduty"},
            {"op": "pagerduty.incident.read"},
            {"connector": "", "op": "read"},
            {"connector": "pagerduty", "op": "   "},
            {"connector": 7, "op": 9},
        ],
        ids=[
            "absent",
            "not-an-object",
            "no-op",
            "no-connector",
            "blank-connector",
            "blank-op",
            "non-strings",
        ],
    )
    def test_a_half_named_source_is_no_source(self, source: object) -> None:
        # "unknown tool" already has an honest sentence on the surface. A
        # half-resolved name would put a blank or a stray value in a register
        # that reads as "this is what the system knows".
        content = SurfaceContentProjection.fold(
            [_surface("s1", "call-1", source=source), _tool_result("call-1", {"id": 1})]
        )

        assert content == {"s1": {"data": {"id": 1}}}

    def test_source_alone_never_hydrates_an_empty_surface(self) -> None:
        # No resolved content ⇒ the surface stays unhydrated and the client
        # shows its honest skeleton. A ``{source}``-only state would read as
        # "hydrated, and the tool returned nothing".
        content = SurfaceContentProjection.fold(
            [_surface("s1", "call-1", source={"connector": "linear", "op": "read"})]
        )

        assert content == {}
