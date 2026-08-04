"""Unit tests for :class:`SurfaceProjector` (generative-UI PRD-02 + the floor PRD).

Covers the spec-acquisition ladder (builtin → store → **inferred**), the
envelope unwrapping that has to happen before any of it, the URI grammar + id
derivation, the rung reported to the Work Ledger, the refinement seam, and the
two short-circuits (non-mapping output, emission disabled).

The load-bearing change tested here is that a ladder *miss* is no longer a
failure state: a mapping-shaped output always ships a spec, and the async model
rung is now an upgrade invited on top of it rather than its only supplier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.projector import (
    InMemorySurfaceSpecStore,
    SurfaceProjector,
)
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceEnvelope,
    SurfaceFieldFormat,
    SurfaceSpecRung,
    validate_surface_spec,
)
from agent_runtime.capabilities.surfaces.store import SpecKey, StoredSpec
from agent_runtime.surfaces_v2.emitter import SpecRung


@dataclass
class _RecordingScheduler:
    """A :class:`SurfaceGenerationSchedulerPort` fake that records invitations.

    Records the whole call, not just ``(server, tool)``: the payload the model
    would author against has to be the unwrapped one, and only the recorded
    ``output`` can show that.
    """

    calls: list[dict[str, object]] = field(default_factory=list)

    def maybe_schedule(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        output: object,
        surface_uri: str,
    ) -> None:
        self.calls.append(
            {
                "server": server,
                "tool": tool,
                "tool_descriptor": tool_descriptor,
                "output": output,
                "surface_uri": surface_uri,
            }
        )


class SurfacePayloadMixin:
    """Connector-shaped payloads and the store/scheduler wiring shared below."""

    # The MCP artifact wrapper: what ``langchain-mcp-adapters`` hands the
    # projector for every server that returns ``structuredContent``.
    WRAPPER_KEY = "structured_content"

    @staticmethod
    def linear_issue_output() -> dict[str, object]:
        return {
            "issue": {
                "id": "issue-uuid-1",
                "identifier": "ENG-1421",
                "title": "Fix login redirect loop",
                "url": "https://linear.app/acme/issue/ENG-1421",
            }
        }

    @staticmethod
    def linear_list_output() -> dict[str, object]:
        """A ``linear.list_issues``-shaped payload: rows plus a headline object."""

        return {
            "team": {"name": "Core"},
            "issues": [
                {
                    "id": "1",
                    "identifier": "ENG-1",
                    "title": "Fix login redirect loop",
                    "state": {"name": "In Progress"},
                    "assignee": {"displayName": "Sarah Chen"},
                    "updatedAt": "2026-08-01T10:00:00Z",
                },
                {
                    "id": "2",
                    "identifier": "ENG-2",
                    "title": "Ship the floor",
                    "state": {"name": "Done"},
                    "assignee": {"displayName": "Marcus Webb"},
                    "updatedAt": "2026-08-02T10:00:00Z",
                },
            ],
        }

    @staticmethod
    def novel_output() -> dict[str, object]:
        """A payload no curated template binds — every shape rung must miss it.

        Needed because the shape rungs made ``linear_list_output`` a *match* for
        any tool name: a fixture used to prove "nothing above rung 0 answered"
        must be structurally unlike all twelve curated specs, not merely
        unlike their names.
        """

        return {
            "deployment": {
                "id": "dep-9",
                "environment": "production",
                "duration_ms": 4210,
            }
        }

    @classmethod
    def wrapped(cls, payload: object) -> dict[str, object]:
        return {cls.WRAPPER_KEY: payload}

    @staticmethod
    def store_with(spec_raw: dict[str, object]) -> InMemorySurfaceSpecStore:
        store = InMemorySurfaceSpecStore()
        store.put(validate_surface_spec(spec_raw))
        return store

    @staticmethod
    def recording() -> _RecordingScheduler:
        return _RecordingScheduler()


class TestSurfaceProjectorBuiltinRung(SurfacePayloadMixin):
    def test_builtin_spec_binds_record_archetype_and_uri(self) -> None:
        envelope = SurfaceProjector().resolve(
            "linear", "get_issue", self.linear_issue_output(), call_id="call_1"
        )

        assert envelope is not None
        assert envelope.archetype is SurfaceArchetype.RECORD
        # id precedence: ``id`` beats ``identifier`` — nested one wrapper deep.
        assert envelope.surface_uri == "record://linear/get_issue/issue-uuid-1"
        assert envelope.state.spec == builtin.lookup("linear", "get_issue")
        assert envelope.state.data == self.linear_issue_output()

    def test_seed_prefixed_server_name_resolves_same_builtin(self) -> None:
        envelope = SurfaceProjector().resolve(
            "seed:linear", "get_issue", self.linear_issue_output()
        )

        assert envelope is not None
        assert envelope.surface_uri == "record://linear/get_issue/issue-uuid-1"
        assert envelope.state.spec == builtin.lookup("linear", "get_issue")

    def test_list_shaped_builtin_binds_table(self) -> None:
        output = {"repository": {"full_name": "acme/web"}, "issues": [{"number": 1}]}
        envelope = SurfaceProjector().resolve("github", "list_issues", output)

        assert envelope is not None
        assert envelope.archetype is SurfaceArchetype.TABLE
        assert envelope.surface_uri.startswith("table://github/list_issues/")

    def test_a_curated_hit_reports_the_builtin_rung(self) -> None:
        envelope = SurfaceProjector().resolve(
            "linear", "get_issue", self.linear_issue_output()
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.BUILTIN


class TestSurfaceProjectorStoreRung(SurfacePayloadMixin):
    def test_store_resolves_when_builtin_misses(self) -> None:
        store = self.store_with(
            {
                "spec_version": 1,
                "archetype": "record",
                "source": {"server": "seed:customsvc", "tool": "get_thing"},
                "title_path": "thing.name",
            }
        )
        projector = SurfaceProjector(store=store)

        envelope = projector.resolve(
            "customsvc", "get_thing", {"thing": {"id": "t-7", "name": "Widget"}}
        )

        assert envelope is not None
        assert envelope.state.spec == store.get(server="customsvc", tool="get_thing")
        assert envelope.spec_rung is SurfaceSpecRung.STORE
        assert envelope.surface_uri == "record://customsvc/get_thing/t-7"

    def test_builtin_wins_over_store(self) -> None:
        # A store entry for a curated (server, tool) must not shadow the builtin.
        store = self.store_with(
            {
                "spec_version": 1,
                "archetype": "table",
                "source": {"server": "seed:linear", "tool": "get_issue"},
                "title_path": "issue.title",
            }
        )

        envelope = SurfaceProjector(store=store).resolve(
            "linear", "get_issue", self.linear_issue_output()
        )

        assert envelope is not None
        assert envelope.state.spec == builtin.lookup("linear", "get_issue")
        assert envelope.spec_rung is SurfaceSpecRung.BUILTIN
        assert envelope.archetype is SurfaceArchetype.RECORD


class TestSurfaceProjectorInferenceRung(SurfacePayloadMixin):
    """Rung 0 — the floor. A ladder miss is no longer a spec-less surface.

    Before this rung existed the projector shipped ``state.data`` alone and the
    client apologised for it. The whole point of the change is that there is
    nothing left to apologise for, so every assertion here is really the same
    one: a mapping went in, a bound spec came out.
    """

    def test_uncurated_mapping_still_ships_a_spec(self) -> None:
        output = {"widget": {"id": "w-9", "label": "Ready"}}
        envelope = SurfaceProjector().resolve("customsvc", "do_thing", output)

        assert envelope is not None
        assert envelope.state.spec is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
        assert envelope.state.data == output
        assert envelope.surface_uri == "record://customsvc/do_thing/w-9"

    def test_uncurated_collection_infers_a_bound_table(self) -> None:
        output = {"rows": [{"a": 1}, {"a": 2}]}
        envelope = SurfaceProjector().resolve("customsvc", "list_rows", output)

        assert envelope is not None
        assert envelope.state.spec is not None
        assert envelope.archetype is SurfaceArchetype.TABLE
        # The subject array is addressed, not merely detected: an ``items_path``
        # that does not resolve renders an empty table.
        assert envelope.state.spec.items_path == "rows"
        assert envelope.surface_uri.startswith("table://customsvc/list_rows/")

    def test_a_linear_shaped_payload_infers_typed_columns(self) -> None:
        # PRD AC5: the connector we have never seen renders like the one we
        # curated by hand — no model call, no store entry, no builtin.
        envelope = SurfaceProjector().resolve(
            "acme-tracker", "list_tickets", self.linear_list_output()
        )

        assert envelope is not None
        spec = envelope.state.spec
        assert spec is not None
        assert spec.archetype is SurfaceArchetype.TABLE
        assert spec.items_path == "issues"
        assert spec.columns is not None
        assert len(spec.columns) >= 3
        formats = {column.format for column in spec.columns}
        assert SurfaceFieldFormat.BADGE in formats  # state.name
        assert SurfaceFieldFormat.USER in formats  # assignee.displayName
        assert SurfaceFieldFormat.DATETIME in formats  # updatedAt
        # Nested values bind through to the displayable sub-key rather than
        # dumping a JSON blob into the cell.
        assert "assignee.displayName" in {column.path for column in spec.columns}

    def test_a_curated_spec_wins_over_inference(self) -> None:
        # Inference is the FLOOR, not a competitor: it must never overwrite a
        # spec a person wrote for this exact tool.
        envelope = SurfaceProjector().resolve(
            "linear", "list_issues", self.linear_list_output()
        )

        assert envelope is not None
        assert envelope.state.spec == builtin.lookup("linear", "list_issues")
        assert envelope.spec_rung is SurfaceSpecRung.BUILTIN

    def test_an_inferred_spec_names_the_calling_tool(self) -> None:
        envelope = SurfaceProjector().resolve(
            "customsvc", "do_thing", {"widget": {"id": "w-9"}}
        )

        assert envelope is not None
        assert envelope.state.spec is not None
        assert envelope.state.spec.source.server == "customsvc"
        assert envelope.state.spec.source.tool == "do_thing"

    def test_an_empty_mapping_still_ships_a_spec(self) -> None:
        # The floor may not have a failure mode; "nothing to render" is a
        # rendering, not an error.
        envelope = SurfaceProjector().resolve("customsvc", "do_thing", {})

        assert envelope is not None
        assert envelope.state.spec is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED


class TestSurfaceProjectorUnwrapping(SurfacePayloadMixin):
    """Wrapper envelopes are peeled before ANY rung reads the payload.

    A spec's dot-paths and the data they bind against must be the same value.
    Shipping ``{"structured_content": {...}}`` while the spec says
    ``items_path: "issues"`` is a spec that matched perfectly and rendered
    nothing — FINDINGS §3.4, and the reason a hand-authored Linear spec never
    reached a screen.
    """

    def test_structured_content_binds_as_if_the_wrapper_were_absent(self) -> None:
        bare = SurfaceProjector().resolve(
            "acme-tracker", "list_tickets", self.linear_list_output()
        )
        wrapped = SurfaceProjector().resolve(
            "acme-tracker", "list_tickets", self.wrapped(self.linear_list_output())
        )

        assert bare is not None and wrapped is not None
        assert wrapped.state.data == self.linear_list_output()
        assert wrapped.state.spec == bare.state.spec
        assert wrapped.surface_uri == bare.surface_uri

    def test_a_curated_spec_binds_through_the_wrapper(self) -> None:
        # PRD AC3's mechanism: the 12 curated specs address the payload, not the
        # transport's box around it.
        envelope = SurfaceProjector().resolve(
            "linear", "list_issues", self.wrapped(self.linear_list_output())
        )

        assert envelope is not None
        spec = envelope.state.spec
        assert spec == builtin.lookup("linear", "list_issues")
        assert spec is not None and spec.items_path is not None
        data = envelope.state.data
        assert isinstance(data, dict)
        # The curated ``items_path`` resolves against what we actually ship.
        assert isinstance(data.get(spec.items_path), list)

    def test_nested_wrappers_peel_to_the_payload(self) -> None:
        envelope = SurfaceProjector().resolve(
            "svc", "t", {"data": {"result": {"id": "deep", "title": "Buried"}}}
        )

        assert envelope is not None
        assert envelope.state.data == {"id": "deep", "title": "Buried"}
        # And the id segment now comes from the payload rather than a hash of
        # the box it arrived in.
        assert envelope.surface_uri == "record://svc/t/deep"

    def test_a_named_payload_key_is_not_a_wrapper(self) -> None:
        # ``issue`` is the subject, not an envelope: peeling it would strip a
        # segment every curated Linear path still spells out.
        envelope = SurfaceProjector().resolve(
            "linear", "get_issue", self.linear_issue_output()
        )

        assert envelope is not None
        assert envelope.state.data == self.linear_issue_output()

    def test_a_wrapper_around_an_array_is_left_alone(self) -> None:
        # Peeling to a bare list would leave a payload with no addressable
        # paths; keeping it is what lets ``items_path: "data"`` bind.
        output = {"data": [{"a": 1}, {"a": 2}]}
        envelope = SurfaceProjector().resolve("svc", "t", output)

        assert envelope is not None
        assert envelope.state.data == output
        assert envelope.state.spec is not None
        assert envelope.state.spec.items_path == "data"

    def test_a_multi_key_payload_is_never_peeled(self) -> None:
        output = {"data": {"id": "x"}, "cursor": "abc"}
        envelope = SurfaceProjector().resolve("svc", "t", output)

        assert envelope is not None
        assert envelope.state.data == output


class TestSurfaceProjectorRefinement(SurfacePayloadMixin):
    """The rung-3 seam. Rung 0 supplies a spec but must not silence the model.

    This is the regression the floor could easily have caused: the old
    invitation condition was "no spec", which rung 0 makes permanently false.
    Left unchanged, adding the floor would have deleted model refinement
    entirely — silently, with every test still green.
    """

    def test_an_inferred_spec_still_invites_refinement(self) -> None:
        scheduler = self.recording()
        projector = SurfaceProjector(scheduler=scheduler)

        envelope = projector.resolve("customsvc", "do_thing", {"widget": {"id": "w-1"}})

        assert envelope is not None
        assert envelope.state.spec is not None  # the floor answered …
        assert len(scheduler.calls) == 1  # … and the model was still invited
        assert scheduler.calls[0]["server"] == "customsvc"
        assert scheduler.calls[0]["tool"] == "do_thing"
        assert scheduler.calls[0]["surface_uri"] == envelope.surface_uri

    def test_a_curated_spec_settles_the_question(self) -> None:
        scheduler = self.recording()

        SurfaceProjector(scheduler=scheduler).resolve(
            "linear", "get_issue", self.linear_issue_output()
        )

        assert scheduler.calls == []

    def test_a_stored_spec_settles_the_question(self) -> None:
        scheduler = self.recording()
        store = self.store_with(
            {
                "spec_version": 1,
                "archetype": "record",
                "source": {"server": "seed:customsvc", "tool": "get_thing"},
                "title_path": "thing.name",
            }
        )

        SurfaceProjector(store=store, scheduler=scheduler).resolve(
            "customsvc", "get_thing", {"thing": {"id": "t-7", "name": "Widget"}}
        )

        assert scheduler.calls == []

    def test_the_scheduler_receives_the_unwrapped_payload(self) -> None:
        # A spec the model authors against the wrapper binds paths that miss
        # the data we ship — the same defect the unwrap exists to close.
        scheduler = self.recording()

        SurfaceProjector(scheduler=scheduler).resolve(
            "acme-tracker", "list_tickets", self.wrapped(self.novel_output())
        )

        assert len(scheduler.calls) == 1
        assert scheduler.calls[0]["output"] == self.novel_output()


class TestSurfaceProjectorIdDerivation:
    def test_top_level_id_field_used(self) -> None:
        envelope = SurfaceProjector().resolve("svc", "t", {"id": "abc", "x": 1})
        assert envelope is not None
        assert envelope.surface_uri.endswith("/abc")

    def test_id_field_precedence_over_identifier(self) -> None:
        envelope = SurfaceProjector().resolve(
            "svc", "t", {"identifier": "IDF-1", "key": "K1"}
        )
        # ``key`` precedes ``identifier`` in the probe order.
        assert envelope is not None
        assert envelope.surface_uri.endswith("/K1")

    def test_unsafe_id_is_sanitised(self) -> None:
        envelope = SurfaceProjector().resolve("svc", "t", {"id": "a/b c:d"})
        assert envelope is not None
        segment = envelope.surface_uri.rsplit("/", 1)[1]
        assert "/" not in segment
        assert " " not in segment
        assert segment == "a-b-c-d"

    def test_hash_fallback_uses_call_id_and_is_stable(self) -> None:
        projector = SurfaceProjector()
        output = {"no": {"identifier": "here"}, "also": {"nope": True}}
        a = projector.resolve("svc", "t", output, call_id="call_zzz")
        b = projector.resolve("svc", "t", output, call_id="call_zzz")
        assert a is not None and b is not None
        assert a.surface_uri == b.surface_uri
        # Different call ids → different fallback segment.
        c = projector.resolve("svc", "t", output, call_id="call_yyy")
        assert c is not None
        assert c.surface_uri != a.surface_uri

    def test_hash_fallback_uses_output_when_no_call_id(self) -> None:
        projector = SurfaceProjector()
        output = {"payload": {"nested": "value"}}
        a = projector.resolve("svc", "t", output)
        b = projector.resolve("svc", "t", output)
        assert a is not None and b is not None
        assert a.surface_uri == b.surface_uri


class TestSurfaceProjectorShortCircuits(SurfacePayloadMixin):
    def test_non_mapping_output_returns_none(self) -> None:
        projector = SurfaceProjector()
        assert projector.resolve("linear", "get_issue", "a string") is None
        assert projector.resolve("linear", "get_issue", None) is None
        assert projector.resolve("linear", "get_issue", [1, 2, 3]) is None

    def test_disabled_projector_returns_none(self) -> None:
        projector = SurfaceProjector(enabled=False)
        assert (
            projector.resolve("linear", "get_issue", self.linear_issue_output()) is None
        )

    def test_disabled_projector_never_infers_or_schedules(self) -> None:
        # ``enabled`` short-circuits BEFORE the ladder, floor included.
        scheduler = self.recording()
        projector = SurfaceProjector(enabled=False, scheduler=scheduler)

        assert projector.resolve("customsvc", "do_thing", {"id": "x"}) is None
        assert scheduler.calls == []


class TestSurfaceProjectorProvenance(SurfacePayloadMixin):
    """``state.source`` — the envelope's own ``{server, tool}``.

    Carried on every state, spec or not, and never read back out of ``data``:
    a payload that could name its own provenance could claim to be any tool.
    """

    def test_an_inferred_surface_names_the_tool_it_came_from(self) -> None:
        envelope = SurfaceProjector().resolve(
            "customsvc", "do_thing", {"widget": {"id": "w-9"}}
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
        assert envelope.state.source is not None
        assert envelope.state.source.server == "customsvc"
        assert envelope.state.source.tool == "do_thing"

    def test_hit_carries_the_same_provenance_as_its_spec(self) -> None:
        envelope = SurfaceProjector().resolve(
            "linear", "get_issue", self.linear_issue_output()
        )

        assert envelope is not None
        assert envelope.state.source is not None
        assert envelope.state.source.tool == "get_issue"

    @pytest.mark.parametrize(
        ("server", "tool"),
        [("", "do_thing"), ("customsvc", ""), ("   ", "   ")],
        ids=["blank-server", "blank-tool", "both-blank"],
    )
    def test_a_nameless_call_still_projects(self, server: str, tool: str) -> None:
        # ``SurfaceSource`` requires both members. This projector is called
        # outside any ``try`` (``SurfaceLedgerOperationOutcomePresenter``), so a
        # ValidationError here would turn a nameless tool into a failed tool
        # call. Absent provenance is the honest answer.
        envelope = SurfaceProjector().resolve(server, tool, {"id": "x"})

        assert envelope is not None
        assert envelope.state.source is None
        # And nothing empty rides the wire.
        assert (
            "source" not in envelope.model_dump(mode="json", exclude_none=True)["state"]
        )

    @pytest.mark.parametrize(
        ("server", "tool"),
        [("", "do_thing"), ("customsvc", ""), ("   ", "   ")],
        ids=["blank-server", "blank-tool", "both-blank"],
    )
    def test_a_nameless_call_still_gets_the_floor(self, server: str, tool: str) -> None:
        # The inferrer stamps its own placeholder provenance rather than
        # refusing to infer — ``SurfaceSpec.source`` is schema-required, and a
        # nameless tool must not cost the user the surface.
        envelope = SurfaceProjector().resolve(server, tool, {"id": "x"})

        assert envelope is not None
        assert envelope.state.spec is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED


class TestSurfaceSpecRungVocabulary(SurfacePayloadMixin):
    """``spec_rung`` is what makes the ledger's ``tier``/``basis`` honest.

    The projector is the only party that knows which rung answered; the emitter
    maps the value onto the pinned ``(tier, basis)`` pair. Two spellings of one
    vocabulary exist only because the pure-domain model may not import the
    ledger emitter — so they are pinned equal here.
    """

    def test_matches_the_ledger_emitters_spelling(self) -> None:
        wire = {
            value
            for name, value in vars(SpecRung).items()
            if not name.startswith("_") and isinstance(value, str)
        }

        assert {rung.value for rung in SurfaceSpecRung} == wire

    def test_rides_the_wire_as_its_string_value(self) -> None:
        envelope = SurfaceProjector().resolve("customsvc", "do_thing", {"id": "x"})

        assert envelope is not None
        dumped = envelope.model_dump(mode="json", exclude_none=True)
        assert dumped["spec_rung"] == SpecRung.INFERRED

    def test_an_envelope_without_a_rung_still_validates(self) -> None:
        # Optional forever: every surface emitted before this field existed
        # carries none, and replaying those events must keep working.
        envelope = SurfaceEnvelope.model_validate(
            {
                "surface_uri": "record://svc/t/1",
                "archetype": "record",
                "state": {"data": {"id": "1"}},
            }
        )

        assert envelope.spec_rung is None
        assert "spec_rung" not in envelope.model_dump(mode="json", exclude_none=True)


class TestSurfaceProjectorShapeMatchRung(SurfacePayloadMixin):
    """Rung 1: a curated spec reused for a tool nobody catalogued (PRD §3.4).

    The audit's finding, restated: Linear's real create tool is ``save_issue``
    and not the catalogued ``create_issue``, ``list_my_issues`` misses
    ``list_issues``, and a server the user added by URL is named after its host
    and misses every entry. All three are *name* failures over payloads the
    curated specs already describe.
    """

    def test_ac8_an_uncatalogued_tool_name_renders_the_curated_table(self) -> None:
        # AC8: `list_my_issues` is not a builtin key; its payload is exactly the
        # shape `linear.list_issues` was written for.
        envelope = SurfaceProjector().resolve(
            "linear", "list_my_issues", self.linear_list_output()
        )

        assert envelope is not None
        assert envelope.state.spec == builtin.lookup("linear", "list_issues")
        assert envelope.archetype is SurfaceArchetype.TABLE

    def test_ac9_a_shape_match_is_never_reported_as_an_exact_hit(self) -> None:
        # AC9: the spec is curated, this pairing is not. Provenance must say so.
        envelope = SurfaceProjector().resolve(
            "linear", "list_my_issues", self.linear_list_output()
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.SHAPE_MATCH
        assert envelope.spec_rung is not SurfaceSpecRung.BUILTIN

    def test_an_unknown_server_named_after_its_url_host_still_matches(self) -> None:
        # The manually-added-server case: neither name resolves, the shape does.
        envelope = SurfaceProjector().resolve(
            "linear-app-com", "issues_query", self.linear_list_output()
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.SHAPE_MATCH
        assert envelope.state.spec == builtin.lookup("linear", "list_issues")

    def test_an_exact_builtin_still_wins_over_a_shape_match(self) -> None:
        envelope = SurfaceProjector().resolve(
            "linear", "list_issues", self.linear_list_output()
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.BUILTIN

    def test_the_envelope_source_names_the_calling_tool_not_the_template(self) -> None:
        # The spec came from Linear's `list_issues`; the *call* did not. A shape
        # match must never re-label whose output the user is looking at.
        envelope = SurfaceProjector().resolve(
            "acme-tracker", "list_my_issues", self.linear_list_output()
        )

        assert envelope is not None
        assert envelope.state.source is not None
        assert envelope.state.source.server == "acme-tracker"
        assert envelope.state.source.tool == "list_my_issues"

    def test_ac10_a_superficially_similar_payload_does_not_match(self) -> None:
        # THE NEGATIVE TEST. Same top-level keys as `linear.list_issues`
        # (`team` + an `issues` array of objects), so every coarse heuristic —
        # "same container name", "a list of objects", "has a team header" —
        # says match. Structurally it is an audit log: not one column path
        # resolves, and rendering the curated table would draw five empty
        # columns under confident Linear headers.
        audit_log = {
            "team": {"name": "Core"},
            "issues": [
                {"event_id": "ev-1", "action": "deleted", "actor": {"email": "a@b.c"}}
            ],
        }

        envelope = SurfaceProjector().resolve("linear", "recent_activity", audit_log)

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
        assert envelope.state.spec != builtin.lookup("linear", "list_issues")

    def test_ac10_a_partial_overlap_below_the_threshold_falls_to_rung_zero(
        self,
    ) -> None:
        # Two of six column paths line up. Better than chance, nowhere near
        # enough — and rung 0 renders it correctly anyway.
        thin = {
            "team": {"name": "Core"},
            "issues": [{"title": "Fix login", "updatedAt": "2026-08-01T10:00:00Z"}],
        }

        envelope = SurfaceProjector().resolve("linear", "list_titles", thin)

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED

    def test_a_record_payload_without_the_list_anchor_does_not_match_a_table(
        self,
    ) -> None:
        # One row lifted out of the collection: the column paths are all there,
        # but flattened. `items_path` has nothing to iterate, so the curated
        # table would draw a header and no rows.
        flattened = {
            "team": {"name": "Core"},
            "identifier": "ENG-1",
            "title": "Fix login redirect loop",
            "state": {"name": "In Progress"},
            "assignee": {"displayName": "Sarah Chen"},
            "updatedAt": "2026-08-01T10:00:00Z",
        }

        envelope = SurfaceProjector().resolve("linear", "current_issue", flattened)

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED

    def test_a_shape_match_does_not_invite_refinement(self) -> None:
        # A curated spec is already better than anything the nano model would
        # author from the same payload; paying per novel tool name would spend
        # the model on aliases of a shape that is already solved.
        scheduler = self.recording()

        SurfaceProjector(scheduler=scheduler).resolve(
            "linear", "list_my_issues", self.linear_list_output()
        )

        assert scheduler.calls == []


class TestSurfaceProjectorLearnedCacheRung(SurfacePayloadMixin):
    """Rung 1a: the learned cache, keyed by payload shape (PRD §3.6, AC14)."""

    NOVEL_SPEC = {
        "spec_version": 1,
        "archetype": "record",
        "source": {"server": "seed:deployer", "tool": "get_deployment"},
        "title_path": "deployment.environment",
        "fields": [{"label": "Duration", "path": "deployment.duration_ms"}],
    }

    def _store_holding_the_novel_spec(self) -> InMemorySurfaceSpecStore:
        store = InMemorySurfaceSpecStore()
        key = SpecKey.build(
            server="deployer",
            tool="get_deployment",
            output_shape_hash=output_shape_hash(self.novel_output()),
            skill_version=1,
        )
        spec = validate_surface_spec(self.NOVEL_SPEC)
        store.put(
            key, StoredSpec.from_generation(key=key, spec=spec, generator_model="")
        )
        return store

    def test_ac14_a_second_tool_with_the_same_shape_reuses_the_learned_spec(
        self,
    ) -> None:
        # AC14: a different connector AND a different tool name, same shape.
        # The exact `(server, tool)` rung misses; the shape rung hits.
        store = self._store_holding_the_novel_spec()

        envelope = SurfaceProjector(store=store).resolve(
            "other-svc", "fetch_release", self.novel_output()
        )

        assert envelope is not None
        assert envelope.state.spec == validate_surface_spec(self.NOVEL_SPEC)
        assert envelope.spec_rung is SurfaceSpecRung.SHAPE_MATCH

    def test_ac14_the_second_encounter_costs_zero_model_calls(self) -> None:
        # AC14, the part that matters commercially: a learned shape must not
        # re-invite generation under a new name.
        scheduler = self.recording()
        store = self._store_holding_the_novel_spec()

        SurfaceProjector(store=store, scheduler=scheduler).resolve(
            "other-svc", "fetch_release", self.novel_output()
        )

        assert scheduler.calls == []

    def test_a_different_shape_still_reaches_rung_zero_and_invites_the_model(
        self,
    ) -> None:
        scheduler = self.recording()
        store = self._store_holding_the_novel_spec()

        envelope = SurfaceProjector(store=store, scheduler=scheduler).resolve(
            "other-svc", "fetch_release", {"build": {"id": "b-1", "ok": True}}
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
        assert len(scheduler.calls) == 1

    def test_the_shape_key_is_the_unwrapped_payload(self) -> None:
        # The scheduler hashes the peeled payload when it writes; reading the
        # wrapper would key the cache one way and read it another.
        store = self._store_holding_the_novel_spec()

        envelope = SurfaceProjector(store=store).resolve(
            "other-svc", "fetch_release", self.wrapped(self.novel_output())
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.SHAPE_MATCH

    def test_a_store_without_the_shape_capability_is_skipped_not_an_error(self) -> None:
        # The PRD-02 read seam is frozen: a store that predates the learned
        # cache must keep working, minus the rung.

        class _NameOnlyStore:
            def get(self, *, server: str, tool: str) -> None:
                return None

        envelope = SurfaceProjector(store=_NameOnlyStore()).resolve(
            "other-svc", "fetch_release", self.novel_output()
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
