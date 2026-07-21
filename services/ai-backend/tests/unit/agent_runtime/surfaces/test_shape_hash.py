"""Unit tests for :func:`output_shape_hash` (generative-UI PRD-07).

The hash keys the spec cache on structure, so it must be stable across records
of the same shape, sensitive to structural change, and blind to values.
"""

from __future__ import annotations

from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash


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
