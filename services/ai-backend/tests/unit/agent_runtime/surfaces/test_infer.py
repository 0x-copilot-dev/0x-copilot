"""Unit tests for rung 0 — deterministic SurfaceSpec inference.

Drives the two public classmethods only (``EnvelopeUnwrapper.unwrap`` and
``SurfaceSpecInferrer.infer``); every heuristic is asserted through the spec it
produces, never by reaching into a private helper. That is deliberate: the
module's contract is "a mapping in, a renderable spec out", and a test that
pinned an internal ranking function would go green while the spec it feeds
stopped validating.

Two acceptance criteria drive the shape of this file (PRD ``generative-ui-floor``
§3.3): **AC5** — a Linear-shaped payload with no builtin spec available infers a
table with at least three correctly typed columns; **AC6** — inference never
raises and never returns ``None`` for a mapping, across scalars, nulls, empty and
heterogeneous arrays, deep nesting, non-string keys and megabyte payloads.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_runtime.capabilities.surfaces.infer import (
    INFERRED_SOURCE,
    EnvelopeUnwrapper,
    SurfaceSpecInferrer,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceFieldFormat,
    SurfaceSpec,
    SurfaceSource,
    validate_surface_spec,
)


class PayloadsMixin:
    """Connector-shaped payloads the inference rules are asserted against."""

    @staticmethod
    def linear_issue_row(index: int) -> dict[str, object]:
        """One Linear issue as the real connector shapes it (nested state/assignee)."""

        return {
            "id": f"9c1f7e6a-0000-4a1b-9f00-{index:012d}",
            "identifier": f"ENG-{1400 + index}",
            "title": f"Investigate the flaky login redirect {index}",
            "state": {"name": "In Progress" if index % 2 else "Todo"},
            "assignee": {
                "displayName": "Ada Lovelace",
                "avatarUrl": "https://example.invalid/a.png",
            },
            "priority": index % 4,
            "updatedAt": f"2026-08-{(index % 28) + 1:02d}T09:12:00Z",
            "url": f"https://linear.app/acme/issue/ENG-{1400 + index}",
        }

    @classmethod
    def linear_list_issues(cls, *, rows: int = 3) -> dict[str, object]:
        """The MCP-delivered payload: a Linear list wrapped in the artifact key.

        Wrapped in ``structured_content`` on purpose — that is exactly what
        ``langchain-mcp-adapters`` hands the projector, and the wrapper is what
        made ``items_path: "issues"`` miss (FINDINGS §3.4).
        """

        return {
            "structured_content": {
                "team": {"id": "team-1", "name": "Core Engineering"},
                "issues": [cls.linear_issue_row(index) for index in range(rows)],
            }
        }

    @classmethod
    def linear_get_issue(cls) -> dict[str, object]:
        """A single-object payload — the record case, wrapped in its own noun."""

        return {"issue": cls.linear_issue_row(1)}


class HostilePayloadsMixin:
    """The AC6 table: inputs chosen to break a naive walk, not to be realistic."""

    @staticmethod
    def megabyte_payload() -> dict[str, object]:
        rows = [
            {
                "id": f"row-{index}",
                "title": f"Row number {index} with some padding text to add weight",
                "state": {"name": "open"},
                "updatedAt": "2026-08-04T00:00:00Z",
                "blob": "x" * 120,
            }
            for index in range(4000)
        ]
        return {"data": {"records": rows}}

    @staticmethod
    def deeply_nested(depth: int) -> dict[str, object]:
        node: dict[str, object] = {"leaf": 1}
        for _ in range(depth):
            node = {"child": node}
        return node

    @staticmethod
    def wrapper_chain(depth: int) -> dict[str, object]:
        node: dict[str, object] = {"name": "bottom"}
        for _ in range(depth):
            node = {"data": node}
        return node

    @classmethod
    def mappings(cls) -> tuple[tuple[str, object], ...]:
        """Every hostile *mapping*: each must yield a valid spec, never ``None``."""

        return (
            ("empty", {}),
            ("all_null", {"a": None, "b": None}),
            ("empty_array", {"items": []}),
            ("array_of_scalars", {"items": [1, 2, 3]}),
            ("array_of_nulls", {"items": [None, None]}),
            ("heterogeneous_array", {"items": [{"a": 1}, 2, "x", None, [3]]}),
            ("array_of_arrays", {"items": [[1, 2], [3, 4]]}),
            ("nested_empty_mappings", {"a": {}, "b": {"c": {}}}),
            # ``3`` rather than ``1`` on purpose: ``True`` hashes equal to ``1``,
            # so the original literal collapsed to two entries and quietly
            # stopped covering the bool-key case it was written for.
            ("non_string_keys", {3: "a", 2.5: "b", True: "c"}),
            ("unaddressable_keys", {"": 1, "  ": 2, "2fa": 3, "with space": 4}),
            ("colliding_labels", {"display_name": "a", "displayName": "b"}),
            ("scalar_wrapper", {"result": "ok"}),
            ("null_wrapper", {"data": None}),
            ("wrapper_over_array", {"data": [{"a": 1}, {"a": 2}]}),
            ("mixed_value_kinds", {"a": {1, 2}, "b": (3, 4), "c": b"bytes"}),
            ("deep_nesting", cls.deeply_nested(200)),
            ("deep_wrapper_chain", cls.wrapper_chain(64)),
            ("megabyte", cls.megabyte_payload()),
        )

    @classmethod
    def non_mappings(cls) -> tuple[tuple[str, object], ...]:
        """The only inputs allowed to infer ``None`` — nothing here is a surface."""

        return (
            ("none", None),
            ("string", "just text"),
            ("integer", 7),
            ("float", 1.5),
            ("bool", True),
            ("bytes", b"raw"),
            ("scalar_list", [1, 2, 3]),
            ("mapping_list", [{"a": 1}]),
            ("empty_list", []),
            ("object", object()),
        )


class SpecAssertionsMixin:
    """Assertions that re-run the real gate rather than trusting the model."""

    @staticmethod
    def revalidate(spec: SurfaceSpec) -> SurfaceSpec:
        """Round-trip the spec through the schema gate a builtin spec faces.

        ``infer`` already validates on the way out, so re-validating the dumped
        JSON is what proves the spec is wire-safe: every path is dot-path legal,
        every label is inside the 40-char bound, every format is in the enum.
        """

        dumped = spec.model_dump(mode="json", exclude_none=True)
        return validate_surface_spec(dumped)

    @staticmethod
    def slot_formats(spec: SurfaceSpec) -> dict[str, str]:
        """``{path: format}`` across whichever slot array the archetype uses."""

        slots = list(spec.columns or ()) + list(spec.fields or ())
        return {
            slot.path: (slot.format or SurfaceFieldFormat.TEXT).value for slot in slots
        }

    @staticmethod
    def slot_labels(spec: SurfaceSpec) -> list[str]:
        slots = list(spec.columns or ()) + list(spec.fields or ())
        return [slot.label for slot in slots]


class TestEnvelopeUnwrapper(PayloadsMixin, HostilePayloadsMixin):
    def test_peels_a_single_key_data_envelope(self) -> None:
        assert EnvelopeUnwrapper.unwrap({"data": {"id": 1}}) == {"id": 1}

    def test_peels_the_mcp_structured_content_wrapper(self) -> None:
        # FINDINGS §3.4: this wrapper is why ``items_path: "issues"`` missed on
        # every MCP server that returns ``structuredContent``.
        unwrapped = EnvelopeUnwrapper.unwrap(self.linear_list_issues())

        assert isinstance(unwrapped, dict)
        assert set(unwrapped) == {"team", "issues"}

    def test_peels_the_camel_case_spelling_too(self) -> None:
        assert EnvelopeUnwrapper.unwrap({"structuredContent": {"a": 1}}) == {"a": 1}

    def test_peels_recursively_through_stacked_wrappers(self) -> None:
        stacked = {"response": {"payload": {"result": {"id": "x"}}}}

        assert EnvelopeUnwrapper.unwrap(stacked) == {"id": "x"}

    def test_does_not_peel_a_multi_key_payload(self) -> None:
        # ``data`` is a wrapper name, but a sibling key means this is content.
        payload = {"data": {"id": 1}, "meta": {"page": 2}}

        assert EnvelopeUnwrapper.unwrap(payload) == payload

    def test_does_not_peel_a_key_outside_the_wrapper_vocabulary(self) -> None:
        payload = {"issue": {"id": 1}}

        assert EnvelopeUnwrapper.unwrap(payload) == payload

    def test_null_siblings_do_not_block_a_peel(self) -> None:
        assert EnvelopeUnwrapper.unwrap({"data": {"id": 1}, "error": None}) == {"id": 1}

    def test_never_peels_down_to_a_scalar(self) -> None:
        # Peeling to ``"ok"`` would leave a payload with no addressable paths.
        payload = {"result": "ok"}

        assert EnvelopeUnwrapper.unwrap(payload) == payload

    def test_never_peels_down_to_an_array(self) -> None:
        # Left wrapped so ``items_path: "data"`` can bind the array.
        payload = {"data": [{"a": 1}]}

        assert EnvelopeUnwrapper.unwrap(payload) == payload

    def test_depth_capped_chain_terminates_and_stays_a_mapping(self) -> None:
        unwrapped = EnvelopeUnwrapper.unwrap(self.wrapper_chain(64))

        assert isinstance(unwrapped, dict)

    def test_non_mapping_input_is_returned_unchanged(self) -> None:
        assert EnvelopeUnwrapper.unwrap("text") == "text"
        assert EnvelopeUnwrapper.unwrap(None) is None
        assert EnvelopeUnwrapper.unwrap([1, 2]) == [1, 2]

    @pytest.mark.parametrize("label,payload", HostilePayloadsMixin.mappings())
    def test_unwrap_never_raises_on_hostile_input(
        self, label: str, payload: object
    ) -> None:
        EnvelopeUnwrapper.unwrap(payload)


class TestInferTableSubject(PayloadsMixin, SpecAssertionsMixin):
    """AC5 — a Linear payload with no builtin spec infers a usable table."""

    def test_linear_payload_infers_a_table_bound_to_the_issue_array(self) -> None:
        spec = SurfaceSpecInferrer.infer(self.linear_list_issues())

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.TABLE
        # ``issues``, not ``structured_content.issues`` — the caller ships the
        # unwrapped payload as ``state.data``, so paths bind against it.
        assert spec.items_path == "issues"
        self.revalidate(spec)

    def test_linear_payload_yields_at_least_three_correctly_typed_columns(self) -> None:
        spec = SurfaceSpecInferrer.infer(self.linear_list_issues())

        assert spec is not None
        formats = self.slot_formats(spec)
        assert formats["state.name"] == SurfaceFieldFormat.BADGE.value
        assert formats["assignee.displayName"] == SurfaceFieldFormat.USER.value
        assert formats["updatedAt"] == SurfaceFieldFormat.DATETIME.value

    def test_linear_payload_titles_from_the_container_outside_the_rows(self) -> None:
        # A table's headline lives outside its rows; the hand-authored Linear
        # spec says ``team.name`` and inference reaches the same path.
        spec = SurfaceSpecInferrer.infer(self.linear_list_issues())

        assert spec is not None
        assert spec.title_path == "team.name"

    def test_columns_are_capped_at_six(self) -> None:
        wide = {"rows": [{f"field_{index}": index for index in range(40)}]}
        spec = SurfaceSpecInferrer.infer(wide)

        assert spec is not None
        assert spec.columns is not None
        assert len(spec.columns) == 6

    def test_numeric_columns_are_end_aligned(self) -> None:
        spec = SurfaceSpecInferrer.infer({"rows": [{"count": 4}, {"count": 9}]})

        assert spec is not None
        assert spec.columns is not None
        assert spec.columns[0].format is SurfaceFieldFormat.NUMBER
        assert spec.columns[0].align is not None

    def test_the_larger_array_of_mappings_wins_the_subject(self) -> None:
        payload = {
            "labels": [{"name": "bug"}, {"name": "ui"}],
            "issues": [{"title": f"t{index}"} for index in range(20)],
        }
        spec = SurfaceSpecInferrer.infer(payload)

        assert spec is not None
        assert spec.items_path == "issues"

    def test_an_array_of_scalars_is_not_a_subject(self) -> None:
        # A table over ``["a", "b"]`` has no columns; the record fallback is the
        # better render, so mapping-ness gates the subject.
        spec = SurfaceSpecInferrer.infer({"tags": ["a", "b", "c"]})

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.RECORD

    def test_a_nested_array_binds_its_full_dot_path(self) -> None:
        payload = {"page": {"sections": [{"heading": "One"}, {"heading": "Two"}]}}
        spec = SurfaceSpecInferrer.infer(payload)

        assert spec is not None
        assert spec.items_path == "page.sections"
        self.revalidate(spec)

    def test_an_unpeelable_array_wrapper_still_binds(self) -> None:
        # ``{"data": [...]}`` is deliberately left wrapped by the unwrapper so
        # ``items_path`` has something to name.
        spec = SurfaceSpecInferrer.infer({"data": [{"title": "a"}, {"title": "b"}]})

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.TABLE
        assert spec.items_path == "data"

    def test_a_table_is_never_emitted_without_columns(self) -> None:
        # ``TableRenderer`` draws a column-less spec as "No columns configured."
        # — a fresh apology, and worse than a record because the table also
        # promises rows. Every emitted table must carry at least one column.
        spec = SurfaceSpecInferrer.infer({"rows": [{"tags": ["a"]}, {"tags": ["b"]}]})

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.RECORD
        assert spec.items_path is None


class TestInferRecordSubject(PayloadsMixin, SpecAssertionsMixin):
    def test_single_object_payload_infers_a_prefixed_record(self) -> None:
        spec = SurfaceSpecInferrer.infer(self.linear_get_issue())

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.RECORD
        assert spec.title_path == "issue.title"
        # Fields descend past the noun wrapper but keep its prefix, exactly as
        # the hand-authored ``linear.get_issue`` spec writes them.
        assert self.slot_formats(spec)["issue.state.name"] == (
            SurfaceFieldFormat.BADGE.value
        )
        self.revalidate(spec)

    def test_the_title_is_not_repeated_as_a_field(self) -> None:
        spec = SurfaceSpecInferrer.infer(self.linear_get_issue())

        assert spec is not None
        assert spec.title_path not in self.slot_formats(spec)

    def test_a_flat_mapping_binds_fields_at_the_root(self) -> None:
        spec = SurfaceSpecInferrer.infer(
            {"name": "Widget", "status": "active", "count": 3}
        )

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.RECORD
        assert spec.title_path == "name"
        assert set(self.slot_formats(spec)) == {"status", "count"}

    def test_title_probe_order_prefers_title_over_name(self) -> None:
        spec = SurfaceSpecInferrer.infer({"name": "second", "title": "first"})

        assert spec is not None
        assert spec.title_path == "title"

    def test_title_falls_back_to_an_unresolved_path_when_nothing_is_titleish(
        self,
    ) -> None:
        # ``title_path`` is schema-required, so "unset" is not available. An
        # unresolved path renders an empty headline — the honest equivalent.
        spec = SurfaceSpecInferrer.infer({"count": 1, "total": 2})

        assert spec is not None
        assert spec.title_path == "title"
        self.revalidate(spec)

    def test_record_fields_are_capped_at_six(self) -> None:
        spec = SurfaceSpecInferrer.infer({f"field_{i}": i for i in range(40)})

        assert spec is not None
        assert spec.fields is not None
        assert len(spec.fields) == 6


class TestInferSlotRanking(PayloadsMixin, SpecAssertionsMixin):
    def test_a_sparse_key_loses_to_a_dense_one(self) -> None:
        rows: list[dict[str, object]] = [{"alpha": index} for index in range(10)]
        rows[0]["beta"] = 1
        spec = SurfaceSpecInferrer.infer({"rows": rows})

        assert spec is not None
        paths = list(self.slot_formats(spec))
        assert paths.index("alpha") < paths.index("beta")

    def test_a_key_that_is_null_on_every_row_is_dropped(self) -> None:
        rows = [{"alpha": 1, "beta": None}, {"alpha": 2, "beta": None}]
        spec = SurfaceSpecInferrer.infer({"rows": rows})

        assert spec is not None
        assert "beta" not in self.slot_formats(spec)

    def test_a_named_key_outranks_an_unrecognised_one(self) -> None:
        rows = [
            {"zzz": "a", "identifier": "ENG-1"},
            {"zzz": "b", "identifier": "ENG-2"},
        ]
        spec = SurfaceSpecInferrer.infer({"rows": rows})

        assert spec is not None
        paths = list(self.slot_formats(spec))
        assert paths.index("identifier") < paths.index("zzz")

    def test_an_iso_8601_string_is_typed_datetime(self) -> None:
        spec = SurfaceSpecInferrer.infer({"stamp": "2026-08-04T09:12:00Z"})

        assert spec is not None
        assert self.slot_formats(spec)["stamp"] == SurfaceFieldFormat.DATETIME.value

    def test_a_prose_string_is_not_badged(self) -> None:
        payload = {"note": "A long sentence that is plainly not a status token"}
        spec = SurfaceSpecInferrer.infer(payload)

        assert spec is not None
        assert self.slot_formats(spec)["note"] == SurfaceFieldFormat.TEXT.value

    def test_a_short_token_string_is_badged(self) -> None:
        spec = SurfaceSpecInferrer.infer({"note": "in progress"})

        assert spec is not None
        assert self.slot_formats(spec)["note"] == SurfaceFieldFormat.BADGE.value

    def test_a_person_shaped_mapping_binds_a_user_column(self) -> None:
        rows = [{"reviewer": {"displayName": "Ada", "avatarUrl": "https://x/y"}}]
        spec = SurfaceSpecInferrer.infer({"rows": rows})

        assert spec is not None
        assert self.slot_formats(spec)["reviewer.displayName"] == (
            SurfaceFieldFormat.USER.value
        )

    def test_a_state_mapping_binds_a_badge_not_a_user(self) -> None:
        # ``state`` carries ``name``; typing every mapping-with-a-name as a user
        # would badge-less the single most common connector column there is.
        spec = SurfaceSpecInferrer.infer({"rows": [{"state": {"name": "Todo"}}]})

        assert spec is not None
        assert self.slot_formats(spec)["state.name"] == SurfaceFieldFormat.BADGE.value

    def test_a_named_display_key_is_preferred_inside_a_nested_mapping(self) -> None:
        rows = [{"project": {"id": "p-1", "name": "Core"}}]
        spec = SurfaceSpecInferrer.infer({"rows": rows})

        assert spec is not None
        assert "project.name" in self.slot_formats(spec)

    def test_an_unnamed_nested_mapping_still_binds_a_scalar(self) -> None:
        # No key here is in any display vocabulary. Dropping the column would
        # leave a table with none at all, which renders as an apology.
        spec = SurfaceSpecInferrer.infer({"rows": [{"blob": {"x": 1, "y": 2}}]})

        assert spec is not None
        assert "blob.x" in self.slot_formats(spec)

    def test_a_nested_mapping_of_only_objects_binds_nothing(self) -> None:
        # There is no scalar anywhere inside to put in a cell.
        spec = SurfaceSpecInferrer.infer({"rows": [{"blob": {"x": {"deep": 1}}}]})

        assert spec is not None
        assert not self.slot_formats(spec)

    def test_an_array_valued_key_is_not_bound_as_a_slot(self) -> None:
        rows = [{"title": "a", "labels": ["x", "y"]}, {"title": "b", "labels": []}]
        spec = SurfaceSpecInferrer.infer({"rows": rows})

        assert spec is not None
        assert "labels" not in self.slot_formats(spec)

    def test_keys_that_are_not_dot_path_legal_are_skipped(self) -> None:
        spec = SurfaceSpecInferrer.infer({"ok_key": 1, "with space": 2, "2fa": 3})

        assert spec is not None
        assert set(self.slot_formats(spec)) == {"ok_key"}
        self.revalidate(spec)


class TestInferLabels(PayloadsMixin, SpecAssertionsMixin):
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("display_name", "Display Name"),
            ("displayName", "Display Name"),
            ("updated_at", "Updated At"),
            ("updatedAt", "Updated At"),
            ("id", "ID"),
            ("html_url", "HTML URL"),
            ("userCount", "User Count"),
            ("state", "State"),
        ],
    )
    def test_labels_are_title_cased_from_the_key(self, key: str, expected: str) -> None:
        spec = SurfaceSpecInferrer.infer({"anchor": "x", key: "value"})

        assert spec is not None
        assert expected in self.slot_labels(spec)

    def test_a_nested_binding_is_labelled_by_its_container(self) -> None:
        # ``assignee.displayName`` reads as "Assignee", not "Display Name".
        spec = SurfaceSpecInferrer.infer({"rows": [{"assignee": {"displayName": "A"}}]})

        assert spec is not None
        assert self.slot_labels(spec) == ["Assignee"]

    def test_colliding_labels_are_disambiguated(self) -> None:
        # ``display_name`` and ``displayName`` Title-Case identically.
        spec = SurfaceSpecInferrer.infer({"display_name": "a", "displayName": "b"})

        assert spec is not None
        labels = self.slot_labels(spec)
        assert len(labels) == len(set(labels))

    def test_labels_stay_inside_the_schema_length_bound(self) -> None:
        spec = SurfaceSpecInferrer.infer({"a" * 200: "value"})

        assert spec is not None
        for label in self.slot_labels(spec):
            assert 1 <= len(label) <= 40
        self.revalidate(spec)


class TestInferProvenance(PayloadsMixin, SpecAssertionsMixin):
    def test_the_pinned_call_form_stamps_the_inferred_source(self) -> None:
        spec = SurfaceSpecInferrer.infer({"title": "x"})

        assert spec is not None
        assert spec.source == INFERRED_SOURCE

    def test_a_caller_supplied_source_is_carried_onto_the_spec(self) -> None:
        source = SurfaceSource(server="linear", tool="list_my_issues")
        spec = SurfaceSpecInferrer.infer(self.linear_list_issues(), source=source)

        assert spec is not None
        assert spec.source == source

    def test_inference_is_deterministic(self) -> None:
        # A learned cache keyed by shape (rung 3) is only sound if the same
        # payload infers the same spec every time.
        payload = self.linear_list_issues()

        first = SurfaceSpecInferrer.infer(payload)
        second = SurfaceSpecInferrer.infer(payload)

        assert first == second


class TestInferTotality(HostilePayloadsMixin, SpecAssertionsMixin):
    """AC6 — the floor has no failure mode."""

    @pytest.mark.parametrize("label,payload", HostilePayloadsMixin.mappings())
    def test_every_mapping_yields_a_valid_spec(
        self, label: str, payload: object
    ) -> None:
        spec = SurfaceSpecInferrer.infer(payload)

        assert spec is not None, f"{label}: a mapping must never infer None"
        assert spec.spec_version == 1
        assert spec.title_path
        self.revalidate(spec)

    @pytest.mark.parametrize("label,payload", HostilePayloadsMixin.non_mappings())
    def test_non_mappings_infer_none(self, label: str, payload: object) -> None:
        assert SurfaceSpecInferrer.infer(payload) is None, label

    def test_the_megabyte_case_is_genuinely_a_megabyte(self) -> None:
        # Guards the guard: a shrunk fixture would leave AC6's size case untested.
        payload = self.megabyte_payload()

        assert len(json.dumps(payload)) > 1_000_000

    def test_a_megabyte_payload_still_infers_a_bound_table(self) -> None:
        spec = SurfaceSpecInferrer.infer(self.megabyte_payload())

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.TABLE
        assert spec.items_path == "records"
        assert spec.columns

    def test_deep_nesting_neither_raises_nor_recurses_away(self) -> None:
        spec = SurfaceSpecInferrer.infer(self.deeply_nested(2000))

        assert spec is not None
        self.revalidate(spec)

    def test_a_mapping_with_only_unaddressable_keys_still_renders(self) -> None:
        # No key can become a dot-path, so the spec carries no slots at all —
        # still a valid spec, still a surface, never an error.
        spec = SurfaceSpecInferrer.infer({1: "a", (2, 3): "b"})

        assert spec is not None
        assert not spec.columns
        assert not spec.fields
        self.revalidate(spec)

    def test_a_mapping_subclass_that_breaks_mid_walk_still_yields_a_spec(self) -> None:
        """The last-resort guard, driven rather than assumed.

        Every branch above is written to be total, but the module promises more
        than "we believe it is": an exotic mapping must still produce a surface.
        The payload is chosen so an ordinary walk would produce fields — their
        absence is what proves the guard, not the walk, answered.
        """

        class ExplodingMapping(dict[str, Any]):
            def __iter__(self) -> Any:
                raise RuntimeError("payload iteration exploded")

        payload = {"title": "x", "status": "open", "count": 2}
        assert SurfaceSpecInferrer.infer(dict(payload)) is not None
        assert SurfaceSpecInferrer.infer(dict(payload)).fields  # type: ignore[union-attr]

        spec = SurfaceSpecInferrer.infer(ExplodingMapping(payload))

        assert spec is not None
        assert spec.archetype is SurfaceArchetype.RECORD
        assert spec.fields is None
        assert spec.title_path == "title"
        self.revalidate(spec)
