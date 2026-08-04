"""``WorkLedgerEmitter`` behaviour (PRD-A3 D3).

Drives the emitter through a recording :data:`EmitFn` (no runtime, no network):
a tool result emits the four events in order; a spec envelope yields a
shaped/registry view + spec-resolved title **and the spec itself on
``surface.created``**; a spec-less envelope yields a generic/schema view +
fallback title; a non-mapping (absent) surface yields classified + read only;
``payload_ref`` is always ``call:<call_id>``; ``class`` / ``basis`` carry the
real PRD-C1 classification (catalog read here, fail-closed write/default for an
unknown op, never spoofable via output); ``spec_rung`` decides the
``view.derived`` pair; a raising ``EmitFn`` is swallowed; ``active()`` is
``None`` when unbound; ``on_spec_generated`` emits the generated view.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agent_runtime.surfaces_v2.emitter import SpecRung, WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


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

    def test_specless_envelope_yields_generic_schema_view_and_fallback_title(
        self,
    ) -> None:
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
        derived = recorded[3]["payload"]
        assert derived["tier"] == "generic"
        assert derived["basis"] == "schema"

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
    """The resolved spec must ride ``surface.created`` (PRD floor §3.2b).

    The defect this pins was silent by construction: the ladder matched a
    curated spec, ``view.derived`` recorded the surface as ``shaped`` /
    ``registry``, and the spec was then dropped on the floor because no event
    carried it. The ledger described a screen nobody was looking at.
    """

    def test_created_carries_the_resolved_spec(self) -> None:
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        created = recorded[2]["payload"]
        assert created["spec"] == {
            "archetype": "record",
            "title_path": "issue.title",
        }

    def test_specless_envelope_omits_the_key_entirely(self) -> None:
        # Absent, not ``None``: a null spec would read as "we looked and there
        # is nothing", which is a different claim from "this record does not
        # speak to the question".
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert "spec" not in recorded[2]["payload"]

    def test_a_non_mapping_spec_is_not_delivered(self) -> None:
        # Total over an untrusted envelope, like every other field here.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()
        env["state"]["spec"] = "record"

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])

        assert "spec" not in recorded[2]["payload"]
        assert recorded[3]["payload"]["tier"] == "generic"

    def test_the_emitted_spec_is_a_copy_not_the_caller_s_mapping(self) -> None:
        # An event payload is a durable record of what was sent. Sharing the
        # caller's mapping would let a later mutation rewrite history.
        emitter, recorded = self._make_emitter()
        env = self._spec_envelope()

        self._run(emitter, surface=env, surface_uri=env["surface_uri"])
        env["state"]["spec"]["title_path"] = "rewritten.after.the.fact"

        assert recorded[2]["payload"]["spec"]["title_path"] == "issue.title"


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
        # the client renders raw — the precise falsehood this PRD removes.
        emitter, recorded = self._make_emitter()
        env = self._specless_envelope()

        self._run(
            emitter,
            surface=env,
            surface_uri=env["surface_uri"],
            spec_rung=SpecRung.INFERRED,
        )

        derived = recorded[3]["payload"]
        assert (derived["tier"], derived["basis"]) == ("generic", "schema")


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
