"""Adversarial tests for the F1-F12 contract-authority map guard."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest


_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE / "check_f_series_contract_authority_map.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_f_series_contract_authority_map",
        _MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _load_module()


@pytest.fixture()
def valid_map(checker) -> dict[str, Any]:
    return checker.load_map()


def _redigest(checker, document: dict[str, Any]) -> None:
    document["integrity"]["digest"] = checker.contract_map_digest(document)


def _assert_error(errors: tuple[str, ...], fragment: str) -> None:
    assert any(fragment in error for error in errors), errors


def test_repository_contract_map_is_valid(checker, valid_map: dict[str, Any]) -> None:
    assert checker.validate_map(valid_map) == ()


def test_missing_f_series_record_coverage_fails_even_with_fresh_digest(
    checker,
    valid_map: dict[str, Any],
) -> None:
    document = deepcopy(valid_map)
    f12_records = next(
        group
        for group in document["contract_groups"]
        if group["scope"] == "F12" and group["kind"] == "record"
    )
    f12_records["names"].remove("AnswerVerificationReport")
    _redigest(checker, document)

    errors = checker.validate_map(document)

    _assert_error(errors, "F12 missing record coverage: AnswerVerificationReport")


def test_duplicate_primary_owner_fails_even_when_group_ids_differ(
    checker,
    valid_map: dict[str, Any],
) -> None:
    document = deepcopy(valid_map)
    duplicate = deepcopy(document["contract_groups"][0])
    duplicate["group_id"] = "f1.record.desktop.duplicate"
    duplicate["names"] = ["EvaluationCase"]
    duplicate["primary_authority"] = "desktop"
    duplicate["supporting_authorities"] = []
    duplicate["consumer_authorities"] = []
    document["contract_groups"].append(duplicate)
    _redigest(checker, document)

    errors = checker.validate_map(document)

    _assert_error(errors, "duplicate primary ownership for record 'EvaluationCase'")


@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_error"),
    [
        (
            "primary_authority",
            "backend-facade",
            "primary_authority is invalid",
        ),
        ("kind", "message", "kind is invalid"),
    ],
)
def test_invalid_authority_or_kind_fails(
    checker,
    valid_map: dict[str, Any],
    field: str,
    invalid_value: str,
    expected_error: str,
) -> None:
    document = deepcopy(valid_map)
    document["contract_groups"][0][field] = invalid_value
    _redigest(checker, document)

    errors = checker.validate_map(document)

    _assert_error(errors, expected_error)


def test_bad_source_path_fails_even_with_fresh_digest(
    checker,
    valid_map: dict[str, Any],
) -> None:
    document = deepcopy(valid_map)
    document["source_documents"]["F1"]["path"] = "../outside.md"
    _redigest(checker, document)

    errors = checker.validate_map(document)

    _assert_error(errors, "source_documents.F1.path must be")
    _assert_error(errors, "must not be absolute, traverse parents")


def test_source_prd_digest_drift_fails(
    checker,
    valid_map: dict[str, Any],
) -> None:
    document = deepcopy(valid_map)
    document["source_documents"]["F1"]["sha256"] = "0" * 64
    _redigest(checker, document)

    errors = checker.validate_map(document)

    _assert_error(errors, "source_documents.F1.sha256 mismatch")


def test_integration_digest_excludes_only_execution_checklist(checker) -> None:
    before = b"""# PRD
## 1.1 Ordered execution checklist
- [ ] pending
## 2. Problem statement
Contract A
"""
    checklist_changed = before.replace(b"- [ ] pending", b"- [x] complete")
    contract_changed = before.replace(b"Contract A", b"Contract B")

    assert checker.source_document_digest(
        "integration", before
    ) == checker.source_document_digest("integration", checklist_changed)
    assert checker.source_document_digest(
        "integration", before
    ) != checker.source_document_digest("integration", contract_changed)


def test_map_tampering_without_redigesting_fails(
    checker,
    valid_map: dict[str, Any],
) -> None:
    document = deepcopy(valid_map)
    document["contract_groups"][0]["primary_authority"] = "backend"

    errors = checker.validate_map(document)

    _assert_error(errors, "integrity.digest mismatch (map tampering or stale digest)")
