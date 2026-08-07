"""``WorkLedgerEmitter`` behaviour (PRD-A3 D3).

Drives the emitter through a recording :data:`EmitFn` (no runtime, no network):
a tool result emits the four events in order; a spec envelope yields a
shaped/registry view + spec-resolved title **and the spec itself on
``surface.created``**; a spec-less envelope is delivered with a fallback title
and **no** ``view.derived`` (nothing shaped it, and no ``ViewBasis`` member can
say so); a non-mapping (absent) surface yields classified + read only;
``payload_ref`` is always ``call:<call_id>``; ``class`` / ``basis`` carry the
real PRD-C1 classification (catalog read here, fail-closed write/default for an
unknown op, never spoofable via output); ``spec_rung`` decides the
``view.derived`` pair; a raising ``EmitFn`` is swallowed; ``active()`` is
``None`` when unbound; ``on_spec_generated`` emits the generated view.

Two ledger-honesty concerns are pinned here as well, because both are about
what a row *says about itself* rather than about any one producer:

* the writer stamp ``w`` — written by this emitter, applied to anything that
  arrives unsigned at the transport allow-list, and rejected at the wire when it
  names a writer this build has never heard of (:class:`TestWriterStamp`);
* the workspace gate pair — ``gate.resolved.v2`` had five readers and no
  producer at all, so a gate that could open could never close
  (:class:`TestWorkspaceGateVocabularyIsHonest`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from copilot_service_contracts.work_ledger import load_work_ledger_contract

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationGatewayMode,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.capabilities.workspace.effects import WorkspaceGrantGate
from agent_runtime.surfaces_v2.emitter import SpecRung, WorkLedgerEmitter
from agent_runtime.surfaces_v2.entities import OperationRequest
from agent_runtime.surfaces_v2.ledger_models import (
    CURRENT_LEDGER_WRITER,
    GateKind,
    LedgerEventType,
    LedgerWriter,
    Producer,
    UnknownLedgerWriterError,
    WorkLedgerVocabulary,
)
from runtime_api.schemas import RuntimeApiEventType
from runtime_api.schemas.events import RuntimeEventPresentationProjector


class RecordingEmitMixin:
    """A recording :data:`EmitFn` + envelope builders for emitter tests."""

    def _make_emitter(self) -> tuple[WorkLedgerEmitter, list[dict[str, object]]]:
        recorded: list[dict[str, object]] = []

        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            recorded.append(
                {
                    "event_type": event_type_value,
                    "payload": dict(payload),
                    "summary": summary,
                }
            )

        return WorkLedgerEmitter(emit=_emit), recorded

    @staticmethod
    def _spec_envelope() -> dict[str, object]:
        return {
            "surface_uri": "record://linear/get_issue/issue-1",
            "archetype": "record",
            "state": {
                "spec": {"archetype": "record", "title_path": "issue.title"},
                "data": {"issue": {"title": "ENG-142 Fix streaming reconnect"}},
            },
        }

    @staticmethod
    def _specless_envelope() -> dict[str, object]:
        return {
            "surface_uri": "table://customsvc/list_rows/w-9",
            "archetype": "board",
            "state": {"data": {"rows": [1, 2, 3]}},
        }

    def _run(
        self,
        emitter: WorkLedgerEmitter,
        *,
        surface: object,
        surface_uri: object,
        latency_ms: int | None = 42,
        server: str = "seed:linear",
        tool: str = "Get_Issue",
        call_id: str = "call_01",
        output: object = None,
        spec_rung: str | None = None,
    ) -> None:
        asyncio.run(
            emitter.on_tool_result(
                server_name=server,
                tool_name=tool,
                call_id=call_id,
                output=output if output is not None else {"k": "v"},
                surface=surface,
                surface_uri=surface_uri,
                latency_ms=latency_ms,
                spec_rung=spec_rung,
            )
        )


class TestOnToolResult(RecordingEmitMixin):
    def test_spec_envelope_emits_four_events_in_order(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
            LedgerEventType.VIEW_DERIVED.value,
        ]

    def test_action_classified_carries_real_catalog_basis(self) -> None:
        # PRD-C1 — the classifier now fills class/basis truthfully. The default
        # ``seed:linear`` / ``Get_Issue`` call is a curated catalog READ, so the
        # emitted pair is ``class=read`` / ``basis=catalog`` (was the A3 stub
        # ``unknown`` / ``default``).
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        classified = recorded[0]["payload"]
        assert classified["class"] == "read"
        assert classified["basis"] == "catalog"
        assert classified["connector"] == "linear"  # server_slug strips "seed:"
        assert classified["op"] == "get_issue"  # tool_slug lowercases
        assert classified["v"] == 1

    def test_action_classified_unknown_op_is_write_default(self) -> None:
        # PRD-C1 fail-closed: an op absent from every catalog classifies WRITE
        # with basis=default — no annotations registry bound here.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="seed:linear",
            tool="frobnicate_widget",
        )

        classified = recorded[0]["payload"]
        assert classified["class"] == "write"
        assert classified["basis"] == "default"
        assert classified["op"] == "frobnicate_widget"

    def test_read_executed_payload_ref_is_call_scheme(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter, surface=env, surface_uri=env["surface_uri"], call_id="call_XYZ"
        )

        read = recorded[1]["payload"]
        assert read["payload_ref"] == "call:call_XYZ"
        assert read["latency_ms"] == 42
        assert recorded[1]["summary"] == "auto-ran (read)"

    def test_read_executed_omits_latency_when_unavailable(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"], latency_ms=None)

        assert "latency_ms" not in recorded[1]["payload"]

    def test_spec_envelope_yields_shaped_registry_view_and_title(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        # The rung is stated, because that is what a curated hit means and it is
        # the only thing that now earns ``basis: registry``. This call used to
        # omit it and pass anyway, on a fallback that assumed any delivered spec
        # came from the registry — so it asserted the right answer for the wrong
        # reason, and would not have noticed the presenter failing to thread the
        # rung through at all.
        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=SpecRung.BUILTIN,
        )

        created = recorded[2]["payload"]
        assert created["surface_id"] == "record://linear/get_issue/issue-1"
        assert created["kind"] == "record"
        # The surface's provenance is the DISPLAY register: the names the call
        # was made with, passed through unaltered — not the lookup slugs
        # ``action.classified`` carries. The v2 content fold restates this pair
        # verbatim as the renderer's ``state.source``, which the tier-3 note
        # prints. (An MCP caller hands these already slug-folded; this asserts
        # the surface layer does not fold them a second time.)
        assert created["source"] == {"connector": "seed:linear", "op": "Get_Issue"}
        assert created["title"] == "ENG-142 Fix streaming reconnect"
        assert created["payload_ref"] == "call:call_01"
        derived = recorded[3]["payload"]
        assert derived["tier"] == "shaped"
        assert derived["basis"] == "registry"

    def test_classification_keeps_the_lookup_slugs(self) -> None:
        # The two registers do not collapse into one. ``surface.created.source``
        # names the tool; ``action.classified`` / ``read.executed`` identify the
        # CALL and stay on the normalised pair the catalogs key on.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert recorded[0]["payload"]["connector"] == "linear"
        assert recorded[0]["payload"]["op"] == "get_issue"
        assert recorded[1]["payload"]["connector"] == "linear"
        assert recorded[1]["payload"]["op"] == "get_issue"
        assert recorded[2]["payload"]["source"] != {
            "connector": recorded[0]["payload"]["connector"],
            "op": recorded[0]["payload"]["op"],
        }

    def test_surface_source_is_restated_from_the_envelope_not_recomputed(
        self,
    ) -> None:
        # The structural pin behind "one served name". Two computations that
        # agree today can drift tomorrow; this asserts the emitter does not
        # compute at all. The envelope's ``state.source`` deliberately differs
        # from the names this call was made with, and the envelope wins — that
        # is only possible if the value is READ rather than derived.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()
        env["state"]["source"] = {"server": "Linear", "tool": "getIssue"}

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="somewhere-else",
            tool="some_other_tool",
        )

        created = recorded[2]["payload"]
        assert created["source"] == {"connector": "Linear", "op": "getIssue"}
        assert created["title"] == "Linear · getIssue"

    def test_surface_source_falls_back_when_the_envelope_names_nothing(
        self,
    ) -> None:
        # Total over an untrusted envelope: a half-named source is no name, and
        # taking it would pair a real connector with a slugged op. The call's
        # own names are the honest fallback.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()
        env["state"]["source"] = {"server": "Linear"}

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="Linear",
            tool="getIssue",
        )

        assert recorded[2]["payload"]["source"] == {
            "connector": "Linear",
            "op": "getIssue",
        }

    def test_fallback_title_names_the_tool_not_its_slug(self) -> None:
        # The no-spec tab label is read by a person, so the emitter must serve
        # the name it was given: ``getIssue`` put through ``tool_slug`` becomes
        # ``getissue``, which names no tool anyone has seen. Whether a given
        # caller still holds the connector's spelling by this point is that
        # caller's business — this pins that the emitter does not destroy one.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="Linear",
            tool="getIssue",
        )

        assert recorded[2]["payload"]["title"] == "Linear · getIssue"

    def test_specless_envelope_is_delivered_with_no_view_derived_at_all(
        self,
    ) -> None:
        # The surface still ships; the DERIVATION does not, because there was
        # none. ``basis: schema`` used to be written here and it asserted that
        # deterministic inference succeeded above a surface with no spec —
        # exactly the over-claim the selected/generated split exists to stop.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="customsvc",
            tool="list_rows",
        )

        created = recorded[2]["payload"]
        # board archetype maps to table kind (D1).
        assert created["kind"] == "table"
        # No spec ⇒ "<connector> · <op>" fallback title.
        assert created["title"] == "customsvc · list_rows"
        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
        ]

    def test_no_basis_is_minted_for_a_surface_nothing_shaped(self) -> None:
        # The four ``ViewBasis`` members all name a shaping that HAPPENED, so
        # none of them can carry "nothing was derived". The absent event is the
        # answer; a fifth member would change a pinned cross-language contract
        # in order to record a non-event.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert all(
            row["event_type"] != LedgerEventType.VIEW_DERIVED.value for row in recorded
        )

    def test_absent_surface_emits_classified_and_read_only(self) -> None:
        emitter, recorded = self._make_emitter()

        self._run(emitter, surface=None, surface_uri=None, server="linear")

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
        ]

    def test_output_cannot_spoof_read_classification(self) -> None:
        # PRD-C1 adversarial: the classification is a function of the curated
        # catalog + registered annotations ONLY — never of the tool OUTPUT. An
        # unknown op whose output claims ``class: read`` still classifies WRITE /
        # default (fail-closed), because the output is never a classification
        # input.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            server="seed:linear",
            tool="frobnicate_widget",
            output={"class": "read", "action_class": "read", "basis": "catalog"},
        )

        classified = next(
            row["payload"]
            for row in recorded
            if row["event_type"] == LedgerEventType.ACTION_CLASSIFIED.value
        )
        assert classified["class"] == "write"
        assert classified["basis"] == "default"

    def test_emit_exception_is_swallowed(self) -> None:
        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("emit exploded")

        emitter = WorkLedgerEmitter(emit=_boom)
        env = self._spec_envelope()

        # Must not raise — a ledger emit never fails a tool call.
        self._run(emitter, surface=env, surface_uri=env["surface_uri"])


class TestSpecDelivery(RecordingEmitMixin):
    """The resolved renderer state must ride ``surface.created`` (floor PRD §3.2b).

    The defect this pins was silent by construction: the ladder matched a
    curated spec, ``view.derived`` recorded the surface as ``shaped`` /
    ``registry``, and the spec was then dropped on the floor because no event
    carried it. The ledger described a screen nobody was looking at.

    ``data`` is asserted alongside ``spec`` throughout, never on its own. A spec
    delivered without the payload it was resolved against renders a correctly
    shaped table over zero rows — the same blank screen by another route, and
    the state the pipeline was actually in after the spec half was fixed.
    """

    def test_created_carries_the_resolved_state(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        created = recorded[2]["payload"]
        assert created["state"] == {
            "spec": {"archetype": "record", "title_path": "issue.title"},
            "source": {"server": "seed:linear", "tool": "Get_Issue"},
            "data": {"issue": {"title": "ENG-142 Fix streaming reconnect"}},
        }

    def test_specless_envelope_still_delivers_its_payload(self) -> None:
        # The floor: no spec is not no surface. The data and the tool's name
        # ride regardless, which is what lets the generic view render a real
        # body and name what produced it.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        state = recorded[2]["payload"]["state"]
        assert "spec" not in state
        assert state["data"] == {"rows": [1, 2, 3]}
        assert state["source"] == {"server": "seed:linear", "tool": "Get_Issue"}

    def test_a_non_mapping_spec_is_not_delivered(self) -> None:
        # Total over an untrusted envelope, like every other field here. The
        # payload still ships: a malformed spec costs the surface its shaping,
        # never its body.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()
        env["state"]["spec"] = "record"

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert "spec" not in recorded[2]["payload"]["state"]
        assert recorded[2]["payload"]["state"]["data"] == {
            "issue": {"title": "ENG-142 Fix streaming reconnect"}
        }
        # A spec that did not survive delivery leaves nothing to derive from,
        # so no ``view.derived`` is written over it either.
        assert all(
            row["event_type"] != LedgerEventType.VIEW_DERIVED.value for row in recorded
        )

    def test_the_emitted_spec_is_a_copy_not_the_caller_s_mapping(self) -> None:
        # An event payload is a durable record of what was sent. Sharing the
        # caller's mapping would let a later mutation rewrite history.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])
        env["state"]["spec"]["title_path"] = "rewritten.after.the.fact"

        assert recorded[2]["payload"]["state"]["spec"]["title_path"] == "issue.title"

    def test_an_envelope_with_no_state_omits_the_key_entirely(self) -> None:
        # Absent, not ``None`` or ``{}``: a present-but-empty state would read
        # as "hydrated, and the tool returned nothing", which is a different
        # claim from "this record does not speak to the question". The fold
        # keys its honest-skeleton decision on exactly that distinction.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()
        env["state"] = "not-a-mapping"

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert "state" not in recorded[2]["payload"]

    def test_a_null_payload_is_not_reported_as_an_empty_body(self) -> None:
        # ``SurfaceState.data`` is required, so its absence from the dump means
        # the projector excluded a ``None``. Writing ``data: null`` anyway would
        # tell the fold this surface is hydrated and empty.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()
        del env["state"]["data"]

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert "data" not in recorded[2]["payload"]["state"]


class TestViewDerivationRung(RecordingEmitMixin):
    """``spec_rung`` decides ``view.derived``'s ``(tier, basis)`` pair.

    No new ledger value: an inferred spec is ``shaped`` derived from structure,
    which is what the existing ``schema`` basis already means.
    """

    @pytest.mark.parametrize(
        ("rung", "expected"),
        [
            (SpecRung.BUILTIN, ("shaped", "registry")),
            (SpecRung.STORE, ("shaped", "registry")),
            (SpecRung.SHAPE_MATCH, ("shaped", "schema")),
            (SpecRung.INFERRED, ("shaped", "schema")),
            (SpecRung.GENERATED, ("shaped", "generated")),
        ],
        ids=["builtin", "store", "shape_match", "inferred", "generated"],
    )
    def test_each_rung_maps_to_its_pinned_pair(
        self, rung: str, expected: tuple[str, str]
    ) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"], spec_rung=rung)

        derived = recorded[3]["payload"]
        assert (derived["tier"], derived["basis"]) == expected

    @pytest.mark.parametrize(
        "rung",
        [None, "", "rung-4", 7],
        ids=["none", "blank", "unknown", "not-a-string"],
    )
    def test_an_unstated_rung_understates_rather_than_overstates(
        self, rung: object
    ) -> None:
        # This asserted ``registry`` until the inference floor shipped, on the
        # reasoning that builtin and store were the only rungs able to produce a
        # spec. That stopped being true, and an unwired caller then stamped
        # ``basis: registry`` on specs derived from the payload's own structure —
        # the failure the old comment here had explicitly predicted.
        #
        # Both readings are wrong when the producer says nothing; they are not
        # equally wrong. ``view.derived`` is a durable compliance record, so
        # understating provenance is recoverable, while claiming a curated
        # registry entry asserts human authorship that never happened. It fails
        # toward the least-claiming basis.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=rung,  # type: ignore[arg-type]
        )

        derived = recorded[3]["payload"]
        assert (derived["tier"], derived["basis"]) == ("shaped", "schema")

    def test_a_rung_cannot_claim_a_spec_the_envelope_does_not_carry(self) -> None:
        # ``view.derived`` describes what was DELIVERED. A caller naming a rung
        # over an empty state would put "shaped" on the ledger above a surface
        # the client renders raw — the precise falsehood this PRD removes. The
        # emitter does not soften the claim to ``generic``/``schema`` either:
        # the delivered state was shaped by nothing, so nothing is recorded.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=SpecRung.INFERRED,
        )

        assert all(
            row["event_type"] != LedgerEventType.VIEW_DERIVED.value for row in recorded
        )


class TestOnSpecGenerated(RecordingEmitMixin):
    def test_emits_generated_view(self) -> None:
        emitter, recorded = self._make_emitter()

        asyncio.run(
            emitter.on_spec_generated(
                payload={
                    "surface_uri": "record://linear/get_issue/issue-1",
                    "generator_model": "gpt-5.4-mini",
                }
            )
        )

        assert len(recorded) == 1
        assert recorded[0]["event_type"] == LedgerEventType.VIEW_DERIVED.value
        payload = recorded[0]["payload"]
        assert payload == {
            "v": 1,
            "w": LedgerWriter.RUNTIME_V2_1.value,
            "surface_id": "record://linear/get_issue/issue-1",
            "tier": "shaped",
            "basis": "generated",
            "gen": {"model": "gpt-5.4-mini"},
        }

    def test_missing_surface_uri_emits_nothing(self) -> None:
        emitter, recorded = self._make_emitter()

        asyncio.run(emitter.on_spec_generated(payload={"generator_model": "m"}))

        assert recorded == []

    def test_spec_generated_emit_exception_swallowed(self) -> None:
        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("nope")

        emitter = WorkLedgerEmitter(emit=_boom)
        asyncio.run(
            emitter.on_spec_generated(
                payload={"surface_uri": "record://x/y/1", "generator_model": "m"}
            )
        )


class TestWriterStamp(RecordingEmitMixin):
    """Every row this emitter writes is signed, and the transport keeps it.

    The absence of a writer stamp is the root cause of the defect this whole
    pass exists to fix. With no record of WHO wrote a row, "is this record
    historic?" can only be answered by guessing from the shape of the strings
    inside the payload — which is what the deleted ``isLegacySurfaceCreated``
    did, and it answered "historic" for every surface the live pipeline
    produces.

    Both ends are asserted here on purpose. The transport allow-list has
    already silently deleted an emitted key once (``surface.created.state``,
    which cost a release), and a stamp that the emitter writes and the wire
    drops is worse than no stamp: it reads as present in every backend test.

    What makes the stamp *complete* is not this emitter — it signs 4 of the 34
    event types — but the append funnel, which signs anything reaching it
    unsigned. Coverage of all 34 is pinned in
    ``tests/unit/runtime_api/test_ledger_writer_stamp.py``; this class pins the
    emitter's own half plus the transport rules the stamp obeys.
    """

    _LEDGER_EVENTS = (
        RuntimeApiEventType.ACTION_CLASSIFIED,
        RuntimeApiEventType.READ_EXECUTED,
        RuntimeApiEventType.SURFACE_CREATED,
        RuntimeApiEventType.VIEW_DERIVED,
    )

    def _emitted(self) -> list[dict[str, object]]:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()
        self._run(emitter, surface=env, surface_uri=env["surface_uri"])
        return recorded

    def test_every_emitted_row_is_signed(self) -> None:
        recorded = self._emitted()

        assert len(recorded) == 4
        for row in recorded:
            payload = row["payload"]
            assert isinstance(payload, dict)
            assert payload["w"] == LedgerWriter.RUNTIME_V2_1.value, row["event_type"]

    def test_the_transport_allow_list_carries_the_stamp(self) -> None:
        # The projection is a REBUILD, not a filter: a key it does not name is
        # deleted with no error at any layer. Drive the real projector with the
        # real emitted payloads rather than a hand-written stand-in.
        recorded = self._emitted()
        by_type = {row["event_type"]: row["payload"] for row in recorded}

        for wire in self._LEDGER_EVENTS:
            projected = RuntimeEventPresentationProjector.payload_for_event(
                event_type=wire,
                payload=dict(by_type[wire.value]),
            )
            assert projected["w"] == LedgerWriter.RUNTIME_V2_1.value, wire.value

    def test_a_row_from_a_producer_that_does_not_sign_is_signed_anyway(
        self,
    ) -> None:
        # The floor. This emitter signs its own four rows, but 28 of the 34
        # ledger event types come from producers that do not — and a stamp only
        # some producers apply is worse than none, because "no ``w``" then means
        # "historic OR forgotten" and the first reader keyed on it classifies
        # live rows as historic. The append funnel closes that.
        unsigned = {
            "v": 1,
            "surface_id": "record://linear/get_issue/1",
            "kind": "record",
            "source": {"connector": "linear", "op": "get_issue"},
            "title": "ENG-1",
            "payload_ref": "call:c1",
        }

        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload=dict(unsigned),
        )

        assert projected == {**unsigned, "w": CURRENT_LEDGER_WRITER.value}

    def test_an_unknown_writer_fails_the_append_rather_than_ghosting_a_row(
        self,
    ) -> None:
        # Three ways to handle a stamp this build cannot read, two of them
        # silent: drop it (the client renders a foreign row as though we wrote
        # it), blank the payload (a ``surface.created`` with no ``surface_id``,
        # which the client fold skips without a word), or refuse. Refuse.
        forged = {
            "v": 1,
            "w": "runtime.v9.7",
            "surface_id": "record://linear/get_issue/1",
            "kind": "record",
            "source": {"connector": "linear", "op": "get_issue"},
            "title": "ENG-1",
            "payload_ref": "call:c1",
        }

        with pytest.raises(UnknownLedgerWriterError) as raised:
            RuntimeEventPresentationProjector.payload_for_event(
                event_type=RuntimeApiEventType.SURFACE_CREATED,
                payload=dict(forged),
            )

        # The safe public message names the rejected stamp and nothing else.
        assert str(raised.value) == "unknown ledger writer: 'runtime.v9.7'"

    def test_a_non_ledger_event_may_spell_w_however_it_likes(self) -> None:
        # ``w`` is one letter. A tool payload is free to mean width by it, and
        # the ledger rule must not reach a row that never spoke the vocabulary.
        payload = {"w": 640, "h": 480}

        projected = RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload=dict(payload),
        )

        assert projected == payload

    def test_the_projection_does_not_mutate_its_argument(self) -> None:
        # ``_project_payload`` returns its argument for event types with no
        # branch of their own; the stamp seam must copy before touching it.
        payload = {"v": 1, "w": LedgerWriter.RUNTIME_V2_1.value, "gate_id": "g1"}
        original = dict(payload)

        RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.GATE_RESOLVED_V2,
            payload=payload,
        )

        assert payload == original

    def test_the_writer_vocabulary_matches_the_contract(self) -> None:
        # The JSON is the SSOT for the ledger vocabulary; ``LedgerWriter`` is
        # its typed mirror, pinned here the way ``ENUM_TYPES`` is pinned in
        # ``test_ledger_contract_parity``.
        writers = load_work_ledger_contract()["writers"]
        assert isinstance(writers, dict)

        assert list(writers["known"]) == [member.value for member in LedgerWriter]
        assert writers["current"] == LedgerWriter.RUNTIME_V2_1.value
        assert writers["current"] in writers["known"]

    def test_every_payload_model_accepts_the_stamp(self) -> None:
        # The stamp lives on ``LedgerPayload``, so it is available on every row
        # rather than on the four this emitter happens to write.
        for model in WorkLedgerVocabulary.PAYLOAD_MODELS.values():
            assert "w" in model.model_fields, model.__name__
            assert model.model_fields["w"].default is None, model.__name__


class TestWorkspaceGateVocabularyIsHonest:
    """A gate that can open must be able to close.

    ``gate.resolved.v2`` was declared in the vocabulary, mirrored on the wire,
    and branched on by five read models — ``PendingWorkV2``, the receipt fold,
    the receipt export, the pending-work query service and the canvas lifecycle
    — while nothing in the tree had ever constructed one. Its twin
    ``gate.opened.v2`` had a producer, so the ledger could record a workspace
    gate opening and could never record it closing, and every reader had to
    treat that gate as outstanding forever.

    The lane is dark (``WORKSPACE_EFFECT_MODE`` defaults off). These tests make
    the vocabulary honest; they do not turn the lane on.
    """

    OPERATION_ID = "op_11111111-1111-4111-8111-111111111111"

    @staticmethod
    def _request(operation_id: str) -> OperationRequest:
        return OperationRequest(
            operation_id=operation_id,
            run_id="run-1",
            producer=Producer.MODEL,
            capability="workspace",
            op="write",
            canonical_args_ref=f"operation://{operation_id}/args",
            args_digest="0" * 64,
            requested_at="2026-01-01T00:00:00+00:00",
        )

    class _RecordingEmitter:
        """Records ``(event_type, payload)`` for the gateway presentation seam."""

        def __init__(self) -> None:
            self.events: list[tuple[LedgerEventType, dict[str, object]]] = []

        async def emit(
            self,
            event_type: LedgerEventType,
            payload: Mapping[str, object],
            summary: str | None = None,
        ) -> None:
            del summary
            self.events.append((event_type, dict(payload)))

    def _blocked(self) -> list[tuple[LedgerEventType, dict[str, object]]]:
        emitter = self._RecordingEmitter()
        token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id="org-1",
                user_id="user-1",
                conversation_id="conv-1",
                run_id="run-1",
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(user={}),
            ledger_emitter=emitter,
            artifact_service=None,
            mode=OperationGatewayMode.ENFORCE,
        )
        try:
            resolution = asyncio.run(
                WorkspaceGrantGate._blocked(
                    request=self._request(self.OPERATION_ID),
                    kind=GateKind.GRANT,
                    reason="workspace_grant_missing_or_revoked",
                    summary="Workspace access is required; no host change was made.",
                )
            )
        finally:
            OperationContext.unbind(token)  # type: ignore[arg-type]

        # The denial is the decision; the events are only evidence of it.
        assert resolution.allowed is False
        return emitter.events

    def test_a_denied_gate_records_both_halves_in_order(self) -> None:
        events = self._blocked()

        assert [event_type for event_type, _ in events] == [
            LedgerEventType.GATE_OPENED_V2,
            LedgerEventType.GATE_RESOLVED_V2,
        ]
        gate_ids = {payload["gate_id"] for _, payload in events}
        assert gate_ids == {f"workspace:{self.OPERATION_ID}"}

    def test_the_resolution_is_a_policy_denial_not_a_user_one(self) -> None:
        # Nobody was asked: the grant was absent, revoked or too narrow and the
        # runtime decided on the spot. Recording ``user`` would claim a person
        # made this call.
        _, resolved = self._blocked()[1]

        assert resolved["decision"] == "denied"
        assert resolved["actor"] == "policy"

    def test_both_halves_validate_against_the_ledger_vocabulary(self) -> None:
        # The emitted payloads are the contract's, not a shape invented at the
        # call site — the failure mode a hand-built dict hides until replay.
        for event_type, payload in self._blocked():
            WorkLedgerVocabulary.validate_payload(event_type.value, payload)

    def test_both_halves_are_signed(self) -> None:
        for _, payload in self._blocked():
            assert payload["w"] == LedgerWriter.RUNTIME_V2_1.value


class TestBinding(RecordingEmitMixin):
    def test_active_is_none_when_unbound(self) -> None:
        assert WorkLedgerEmitter.active() is None

    def test_bind_and_unbind(self) -> None:
        emitter, _ = self._make_emitter()
        token = WorkLedgerEmitter.bind_for_run(emitter)
        try:
            assert WorkLedgerEmitter.active() is emitter
        finally:
            WorkLedgerEmitter.unbind(token)
        assert WorkLedgerEmitter.active() is None


class TestShapeMatchProvenance(RecordingEmitMixin):
    """AC9: a shape match must not be recorded as a curated-registry hit.

    Both readings are defensible — the spec IS a curated registry entry, but
    the connector it was reused for was never curated — which is exactly why
    the choice is pinned by a test rather than left to whoever edits the map
    next.
    """

    def test_a_shape_match_does_not_claim_the_registry_basis(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=SpecRung.SHAPE_MATCH,
        )

        derived = recorded[3]["payload"]
        assert derived["basis"] != "registry"
        assert derived["basis"] == "schema"

    def test_it_mints_no_new_ledger_value(self) -> None:
        # The (tier, basis) vocabulary is pinned cross-language; a new rung is
        # a new *reason*, never a new wire value.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=SpecRung.SHAPE_MATCH,
        )

        derived = recorded[3]["payload"]
        assert derived["tier"] in {"raw", "generic", "shaped"}
        assert derived["basis"] in {"schema", "registry", "generated", "default"}

    def test_it_still_reads_as_shaped(self) -> None:
        # Understating the *basis* must not understate the tier: a shape match
        # ships a real spec, so the surface genuinely is shaped.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=SpecRung.SHAPE_MATCH,
        )

        assert recorded[3]["payload"]["tier"] == "shaped"
