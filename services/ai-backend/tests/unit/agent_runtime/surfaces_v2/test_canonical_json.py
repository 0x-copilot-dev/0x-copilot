"""Cross-language canonical JSON and digest vectors."""

from __future__ import annotations

import math

import pytest

from copilot_service_contracts.work_ledger import load_ledger_contract_vectors

from agent_runtime.surfaces_v2.canonical_json import (
    CanonicalJsonError,
    canonical_json,
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_hex,
)


def test_canonical_json_vectors() -> None:
    vectors = load_ledger_contract_vectors()["canonical_json"]
    assert isinstance(vectors, list)
    for vector in vectors:
        assert isinstance(vector, dict)
        value = vector["value"]
        assert canonical_json(value) == vector["canonical"], vector["id"]
        assert canonical_json_sha256(value) == vector["sha256"], vector["id"]


def test_byte_digest_vectors_hash_bytes_as_bytes() -> None:
    vectors = load_ledger_contract_vectors()["byte_digests"]
    assert isinstance(vectors, list)
    for vector in vectors:
        assert isinstance(vector, dict)
        assert sha256_hex(str(vector["utf8"]).encode()) == vector["sha256"]


def test_shared_invalid_vectors_fail_before_hashing() -> None:
    vectors = load_ledger_contract_vectors()["invalid_canonical_json"]
    assert isinstance(vectors, list)
    observed: set[str] = set()
    for vector in vectors:
        assert isinstance(vector, dict)
        recipe = vector["recipe"]
        assert isinstance(recipe, dict)
        observed.add(str(vector["id"]))
        with pytest.raises(CanonicalJsonError):
            canonical_json_bytes(_materialize_invalid_recipe(recipe))
    assert observed == {
        "nan",
        "positive_infinity",
        "negative_infinity",
        "unsafe_integer",
        "unsupported_value",
        "non_string_key",
        "cycle",
        "unpaired_surrogate",
    }


@pytest.mark.parametrize("value", [(1, 2), object()])
def test_additional_unsupported_values_fail(value: object) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json(value)


def test_arrays_preserve_order_and_objects_sort_keys() -> None:
    left = {"b": 2, "a": [1, 2, 3]}
    reordered_keys = {"a": [1, 2, 3], "b": 2}
    reordered_array = {"a": [3, 2, 1], "b": 2}
    assert canonical_json(left) == canonical_json(reordered_keys)
    assert canonical_json(left) != canonical_json(reordered_array)


def test_sha256_hex_rejects_text_to_prevent_implicit_encoding() -> None:
    with pytest.raises(TypeError, match="bytes-like"):
        sha256_hex("abc")  # type: ignore[arg-type]


def _materialize_invalid_recipe(recipe: dict[str, object]) -> object:
    kind = recipe["kind"]
    if kind == "non_finite":
        return {
            "nan": math.nan,
            "positive": math.inf,
            "negative": -math.inf,
        }[str(recipe["variant"])]
    if kind == "unsafe_integer":
        return int(str(recipe["decimal"]))
    if kind == "unsupported_binary":
        return b"bytes"
    if kind == "non_string_key":
        return {1: "bad"}
    if kind == "cycle":
        value: list[object] = []
        value.append(value)
        return value
    if kind == "unpaired_surrogate":
        return chr(int(str(recipe["code_unit"]), 16))
    raise AssertionError(f"unknown invalid-vector recipe: {kind!r}")
