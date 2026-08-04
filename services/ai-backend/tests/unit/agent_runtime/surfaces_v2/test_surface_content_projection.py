"""Declared-reference content hydration — pure, total, and envelope-free."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.surfaces.projector import SurfaceProjector
from agent_runtime.surfaces_v2.content import SurfaceContentProjection
from agent_runtime.surfaces_v2.emitter import SpecRung, WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


@dataclass
class _Event:
    event_type: str
    payload: dict[str, object] = field(default_factory=dict)


def _surface(
    surface_id: str,
    call_id: str,
    source: object = None,
    spec: object = None,
) -> _Event:
    payload: dict[str, object] = {
        "surface_id": surface_id,
        "payload_ref": f"call:{call_id}",
    }
    if source is not None:
        payload["source"] = source
    if spec is not None:
        payload["spec"] = spec
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

    def test_the_spec_declared_on_the_surface_record_is_hydrated(self) -> None:
        # The builtin / store / inferred rungs resolve backend-side and ride
        # the record that declares the surface. Sourcing the spec from
        # generation alone left every one of them stranded.
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1", spec={"archetype": "table"}),
                _tool_result("call-1", {"rows": []}),
            ]
        )
        assert content == {"s1": {"data": {"rows": []}, "spec": {"archetype": "table"}}}

    def test_generated_refinement_replaces_the_declared_spec(self) -> None:
        # The model refines; it never authors from a blank page. It also
        # arrives later in the stream, so last-writer-wins is the ladder's own
        # order rather than a tiebreak.
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1", spec={"archetype": "table"}),
                _tool_result("call-1", {"rows": []}),
                _Event(
                    event_type="surface_spec_generated",
                    payload={
                        "surface_uri": "s1",
                        "spec": {"archetype": "table", "title_path": "team.name"},
                    },
                ),
            ]
        )
        assert content["s1"]["spec"] == {
            "archetype": "table",
            "title_path": "team.name",
        }

    def test_a_declared_spec_is_not_hydrated_for_an_undeclared_subject(self) -> None:
        # Same authorization posture the generated branch already has: the
        # caller names the subjects it may hydrate, and a spec does not widen
        # that set.
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1", spec={"archetype": "record"}),
                _surface("s2", "call-2", spec={"archetype": "table"}),
                _tool_result("call-1", 1),
                _tool_result("call-2", 2),
            ],
            surface_payload_refs={"s2": "call:call-2"},
        )
        assert content == {"s2": {"data": 2, "spec": {"archetype": "table"}}}

    @pytest.mark.parametrize(
        "spec", ["table", 7, ["archetype"]], ids=["string", "number", "list"]
    )
    def test_a_malformed_declared_spec_is_ignored(self, spec: object) -> None:
        content = SurfaceContentProjection.fold(
            [
                _surface("s1", "call-1", spec=spec),
                _tool_result("call-1", {"id": 1}),
            ]
        )
        assert content == {"s1": {"data": {"id": 1}}}

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


class ServedNameMixin:
    """Drives projector → emitter → fold, the chain that puts a name on screen.

    Deliberately over the real objects rather than hand-built payloads: the
    defect this pins was that two *separate* correct-looking functions produced
    two different names for one tool, so only running both and comparing the
    results proves anything.

    The names below are the mixed-case ones a connector would publish. What
    reaches this chain in production is whatever the caller has left — the MCP
    path slug-folds at ``McpToolCallRequest``, so it hands over ``get_issue``
    (see ``test_v1_free_ledger`` for what production actually serves). These
    inputs pin the property that is this layer's to keep: it serves the name it
    was handed, unaltered, and serves ONE of them.
    """

    SERVER = "Linear"
    TOOL = "getIssue"
    CALL_ID = "call-served"
    OUTPUT: Mapping[str, object] = {"id": "ENG-142", "title": "Fix reconnect"}

    def _v1_source(self) -> Mapping[str, object] | None:
        """``state.source`` as the v1 projector attaches it to the envelope."""

        envelope = SurfaceProjector().resolve(
            self.SERVER, self.TOOL, dict(self.OUTPUT), call_id=self.CALL_ID
        )
        assert envelope is not None
        return envelope.state.model_dump(mode="json", exclude_none=True).get("source")

    def _served_state(self) -> Mapping[str, object]:
        """``state`` as the v2 path serves it: emit → ledger → content fold."""

        envelope = SurfaceProjector().resolve(
            self.SERVER, self.TOOL, dict(self.OUTPUT), call_id=self.CALL_ID
        )
        assert envelope is not None
        recorded: list[_Event] = []

        async def _emit(
            event_type: str, payload: Mapping[str, object], _summary: object
        ) -> None:
            recorded.append(_Event(event_type=event_type, payload=dict(payload)))

        asyncio.run(
            WorkLedgerEmitter(emit=_emit).on_tool_result(
                server_name=self.SERVER,
                tool_name=self.TOOL,
                call_id=self.CALL_ID,
                output=dict(self.OUTPUT),
                surface=envelope.model_dump(mode="json", exclude_none=True),
                surface_uri=envelope.surface_uri,
                latency_ms=11,
            )
        )
        content = SurfaceContentProjection.fold(
            [_tool_result(self.CALL_ID, dict(self.OUTPUT)), *recorded],
            surface_payload_refs={envelope.surface_uri: f"call:{self.CALL_ID}"},
        )
        return content[envelope.surface_uri]


class TestServedToolName(ServedNameMixin):
    """One tool, one served name — whichever path produced the surface."""

    def test_the_surface_layer_does_not_respell_the_name_it_is_given(
        self,
    ) -> None:
        # ``state.source.tool`` is the string the tier-3 note reads out ("No
        # spec matched <tool>"), so a name may only ever be *carried* here. Put
        # through ``tool_slug`` this pair would serve ``getissue``, which names
        # no tool that exists anywhere.
        assert self._served_state()["source"] == {
            "server": self.SERVER,
            "tool": self.TOOL,
        }

    def test_both_paths_serve_the_same_name(self) -> None:
        # The v1 envelope and the v2 ledger→fold path are the two ways a client
        # can obtain this field. They disagreed before: the projector carried
        # the name, the emitter re-derived it through ``tool_slug``. They agree
        # now because there is one producer — the emitter restates the
        # envelope's ``state.source`` rather than computing a second answer.
        assert self._served_state()["source"] == self._v1_source()


class CuratedSpecMixin:
    """Drives projector → emitter → fold over a REAL curated builtin spec.

    Over the real objects, and over ``linear.list_issues`` specifically, because
    the defect was invisible to every hand-built payload: each layer did its own
    job correctly and the spec still never crossed the wire. Only running the
    whole chain and inspecting what the renderer is handed can tell the
    difference between "matched" and "delivered".
    """

    SERVER = "seed:linear"
    TOOL = "list_issues"
    CALL_ID = "call-curated"
    OUTPUT: Mapping[str, object] = {
        "team": {"name": "Engineering"},
        "issues": [
            {
                "identifier": "ENG-1421",
                "title": "Fix login redirect loop",
                "state": {"name": "In Progress"},
                "assignee": {"displayName": "Sarah Chen"},
                "updatedAt": "2026-07-20T10:00:00Z",
            }
        ],
    }

    def _drive(self) -> tuple[Mapping[str, object], list[_Event]]:
        """Return the served ``state`` plus the ledger events that produced it."""

        envelope = SurfaceProjector().resolve(
            self.SERVER, self.TOOL, dict(self.OUTPUT), call_id=self.CALL_ID
        )
        assert envelope is not None
        # Guards the fixture, not the code: if the curated spec ever stopped
        # matching, every assertion below would pass vacuously instead.
        assert envelope.state.spec is not None

        recorded: list[_Event] = []

        async def _emit(
            event_type: str, payload: Mapping[str, object], _summary: object
        ) -> None:
            recorded.append(_Event(event_type=event_type, payload=dict(payload)))

        asyncio.run(
            WorkLedgerEmitter(emit=_emit).on_tool_result(
                server_name=self.SERVER,
                tool_name=self.TOOL,
                call_id=self.CALL_ID,
                output=dict(self.OUTPUT),
                surface=envelope.model_dump(mode="json", exclude_none=True),
                surface_uri=envelope.surface_uri,
                latency_ms=7,
                spec_rung=SpecRung.BUILTIN,
            )
        )
        content = SurfaceContentProjection.fold(
            [_tool_result(self.CALL_ID, dict(self.OUTPUT)), *recorded],
            surface_payload_refs={envelope.surface_uri: f"call:{self.CALL_ID}"},
        )
        return content[envelope.surface_uri], recorded

    @staticmethod
    def _payload_of(
        recorded: list[_Event], event_type: LedgerEventType
    ) -> dict[str, object]:
        return next(
            event.payload for event in recorded if event.event_type == event_type.value
        )


class TestCuratedSpecReachesTheRenderer(CuratedSpecMixin):
    """A matched builtin spec must arrive at the fold, not just at the ledger."""

    def test_the_curated_table_is_served_with_its_columns(self) -> None:
        state, _ = self._drive()

        spec = state["spec"]
        assert spec["archetype"] == "table"
        assert spec["items_path"] == "issues"
        assert [column["label"] for column in spec["columns"]] == [
            "ID",
            "Title",
            "State",
            "Assignee",
            "Updated",
        ]

    def test_the_data_arrives_with_the_spec(self) -> None:
        # A spec without its payload renders an empty table — the same blank
        # screen by another route.
        state, _ = self._drive()

        assert state["data"] == self.OUTPUT

    def test_the_ledger_basis_describes_what_was_served(self) -> None:
        # The falsehood this PRD removes: ``basis: registry`` recorded over a
        # surface the client rendered spec-less. The two are asserted together
        # on purpose — either one alone is what we already had.
        state, recorded = self._drive()

        derived = self._payload_of(recorded, LedgerEventType.VIEW_DERIVED)
        assert (derived["tier"], derived["basis"]) == ("shaped", "registry")
        assert "spec" in state
