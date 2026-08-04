"""Unit tests for :func:`output_shape_hash` (generative-UI PRD-07).

The hash keys the spec cache on structure, so it must be stable across records
of the same shape, sensitive to structural change, and blind to values.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.surfaces.shape_hash import (
    SHAPE_MATCH_MIN_COVERAGE,
    SHAPE_MATCH_MIN_TEMPLATE_PATHS,
    ShapeSkeleton,
    output_shape_hash,
)


class TestOutputShapeHash:
    def test_same_shape_different_values_hash_equal(self) -> None:
        a = {"issue": {"id": "ENG-1", "title": "one", "count": 3}}
        b = {"issue": {"id": "ENG-999", "title": "another entirely", "count": 41}}
        assert output_shape_hash(a) == output_shape_hash(b)

    def test_key_order_is_irrelevant(self) -> None:
        a = {"a": 1, "b": "x"}
        b = {"b": "y", "a": 2}
        assert output_shape_hash(a) == output_shape_hash(b)

    def test_added_key_changes_hash(self) -> None:
        a = {"issue": {"id": "1", "title": "t"}}
        b = {"issue": {"id": "1", "title": "t", "assignee": "x"}}
        assert output_shape_hash(a) != output_shape_hash(b)

    def test_value_type_change_changes_hash(self) -> None:
        a = {"count": 1}
        b = {"count": "1"}
        assert output_shape_hash(a) != output_shape_hash(b)

    def test_array_uses_first_element_shape(self) -> None:
        a = {"rows": [{"x": 1}]}
        b = {"rows": [{"x": 9}, {"x": 8}, {"x": 7}]}
        # Same element shape, different length/values ⇒ same hash.
        assert output_shape_hash(a) == output_shape_hash(b)

    def test_empty_vs_nonempty_array_differ(self) -> None:
        assert output_shape_hash({"rows": []}) != output_shape_hash(
            {"rows": [{"x": 1}]}
        )

    def test_string_is_not_walked_as_sequence(self) -> None:
        # A long string must not blow up depth or leak content into the hash.
        assert output_shape_hash({"s": "a" * 5000}) == output_shape_hash({"s": "b"})

    def test_deeply_nested_is_depth_capped(self) -> None:
        node: dict[str, object] = {"leaf": 1}
        for _ in range(40):
            node = {"child": node}
        # Must terminate (no recursion error) and produce a stable digest.
        assert isinstance(output_shape_hash(node), str)

    def test_bool_and_int_distinguished(self) -> None:
        assert output_shape_hash({"v": True}) != output_shape_hash({"v": 1})


class SkeletonFixtureMixin:
    """Payloads shared by the skeleton + similarity tests."""

    @staticmethod
    def linear_list() -> dict[str, object]:
        return {
            "team": {"name": "Core"},
            "issues": [
                {
                    "identifier": "ENG-1",
                    "title": "Fix login",
                    "state": {"name": "In Progress"},
                    "assignee": {"displayName": "Sarah Chen"},
                    "updatedAt": "2026-08-01T10:00:00Z",
                }
            ],
        }

    # What ``linear.list_issues`` binds, expanded into the payload's path space.
    LINEAR_LIST_TEMPLATE = frozenset(
        {
            "team.name",
            "issues[].identifier",
            "issues[].title",
            "issues[].state.name",
            "issues[].assignee.displayName",
            "issues[].updatedAt",
        }
    )


class TestShapeSkeleton(SkeletonFixtureMixin):
    def test_paths_use_the_specs_dot_grammar_plus_a_collection_marker(self) -> None:
        skeleton = ShapeSkeleton.of(self.linear_list())

        assert "team.name" in skeleton.paths
        assert "issues[]" in skeleton.paths
        assert "issues[].state.name" in skeleton.paths

    def test_values_never_enter_a_path(self) -> None:
        a = ShapeSkeleton.of({"issue": {"title": "one"}})
        b = ShapeSkeleton.of({"issue": {"title": "something else entirely"}})

        assert a.paths == b.paths

    def test_types_never_enter_a_path(self) -> None:
        # Unlike the hash: a spec declares no types, so scoring a spec against a
        # typed skeleton would score every template at zero.
        a = ShapeSkeleton.of({"count": 1})
        b = ShapeSkeleton.of({"count": "1"})

        assert a.paths == b.paths
        assert output_shape_hash({"count": 1}) != output_shape_hash({"count": "1"})

    def test_an_empty_collection_still_records_its_marker(self) -> None:
        # `items_path` pointing at an empty result set matched; it just has no
        # rows to draw. That must not read as a structural miss.
        assert ShapeSkeleton.of({"issues": []}).has("issues[]")

    def test_a_string_is_not_walked_as_a_sequence(self) -> None:
        assert ShapeSkeleton.of({"s": "abc"}).paths == frozenset({"s"})

    def test_deep_nesting_terminates(self) -> None:
        node: dict[str, object] = {"leaf": 1}
        for _ in range(60):
            node = {"child": node}

        assert isinstance(ShapeSkeleton.of(node).paths, frozenset)

    def test_a_wide_payload_is_path_capped(self) -> None:
        wide = {f"k{index}": index for index in range(5_000)}

        assert len(ShapeSkeleton.of(wide).paths) <= 512

    def test_total_over_a_non_mapping(self) -> None:
        assert ShapeSkeleton.of("just a string").paths == frozenset()


class TestShapeSkeletonCoverage(SkeletonFixtureMixin):
    def test_a_payload_supplying_every_bound_path_scores_one(self) -> None:
        skeleton = ShapeSkeleton.of(self.linear_list())

        assert skeleton.coverage_of(self.LINEAR_LIST_TEMPLATE) == 1.0

    def test_one_missing_path_costs_exactly_one_paths_worth(self) -> None:
        payload = self.linear_list()
        del payload["team"]

        coverage = ShapeSkeleton.of(payload).coverage_of(self.LINEAR_LIST_TEMPLATE)

        assert coverage == pytest.approx(5 / 6)
        assert coverage >= SHAPE_MATCH_MIN_COVERAGE

    def test_the_threshold_rejects_a_half_matching_payload(self) -> None:
        thin = {"team": {"name": "Core"}, "issues": [{"title": "Fix login"}]}

        coverage = ShapeSkeleton.of(thin).coverage_of(self.LINEAR_LIST_TEMPLATE)

        assert coverage < SHAPE_MATCH_MIN_COVERAGE

    def test_extra_payload_keys_do_not_dilute_the_score(self) -> None:
        # The asymmetry is the point: a curated spec binds a deliberate SUBSET
        # of a payload's keys, so a symmetric metric (Jaccard) would score a
        # textbook-correct match down into the coincidence band.
        payload = self.linear_list()
        payload["pagination"] = {"cursor": "abc", "has_more": True}
        payload["issues"][0]["description"] = "..."  # type: ignore[index]

        assert ShapeSkeleton.of(payload).coverage_of(self.LINEAR_LIST_TEMPLATE) == 1.0

    def test_an_empty_template_scores_zero_not_one(self) -> None:
        # A template that demands nothing has not matched anything; it has
        # merely failed to disagree.
        assert ShapeSkeleton.of(self.linear_list()).coverage_of(frozenset()) == 0.0

    def test_the_threshold_is_a_named_tunable_constant(self) -> None:
        # AC10: a false match is worse than rung 0, so the bias is high and the
        # dial is a module constant rather than a literal at a call site.
        assert 0.0 < SHAPE_MATCH_MIN_COVERAGE <= 1.0
        assert SHAPE_MATCH_MIN_COVERAGE >= 0.75
        assert SHAPE_MATCH_MIN_TEMPLATE_PATHS >= 3
