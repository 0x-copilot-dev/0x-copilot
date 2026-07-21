"""Deterministic scorers for the surface-spec eval harness (PRD-11).

Pure functions — no model, no I/O, no randomness (never LLM-judges, per the
PRD guardrail), so the harness is reproducible and its report is a stable golden.
Scores a produced spec against its fixture:

* **schema-valid rate** — the produced spec re-validates against the contract.
* **path-resolution rate** — every ``*_path`` resolves against the real sample.
* **archetype-choice accuracy** — produced archetype equals the golden.
* **label-quality lint** — labels obey the SKILL rules (length, ≤3 words,
  sentence case, no snake_case).
* **field-count sanity** — the slot count sits in the healthy 4–8 band.

Plus **rejection accuracy** over the adversarial fixtures whose recorded output
should be rejected by the injection lint, and an overall expected-match rate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    SurfaceSpecLinter,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpec,
    validate_surface_spec,
)

from tests.evals.surfaces.corpus import EvalFixture

_LABEL_MAX = 40
_LABEL_MAX_WORDS = 3
_FIELD_COUNT_MIN = 4
_FIELD_COUNT_MAX = 8


def label_quality_ok(label: str) -> bool:
    """Return whether a label obeys the SKILL label rules (deterministic).

    Sentence case, ≤3 words, 1–40 chars, no snake_case, not ALL CAPS. Mirrors the
    doctrine's "Assignee / Updated / Due date — not assignee_display_name, not
    ASSIGNEE" guidance.
    """

    if not (1 <= len(label) <= _LABEL_MAX):
        return False
    if "_" in label:
        return False
    words = label.split()
    if len(words) > _LABEL_MAX_WORDS:
        return False
    if label.isupper():
        return False
    first = label[0]
    return first.isupper() or not first.isalpha()


def _slot_count(spec: SurfaceSpec) -> int:
    if spec.fields:
        return len(spec.fields)
    if spec.columns:
        return len(spec.columns)
    return 0


def _all_labels(spec: SurfaceSpec) -> list[str]:
    labels = [slot.label for slot in (*(spec.fields or ()), *(spec.columns or ()))]
    if spec.link is not None:
        labels.append(spec.link.label)
    return labels


def score_fixture(
    fixture: EvalFixture, result: SurfaceSpec | GenFailure, sample: object
) -> dict[str, Any]:
    """Build the per-fixture score record (stable, JSON-serialisable)."""

    record: dict[str, Any] = {
        "id": fixture.id,
        "server": fixture.server,
        "tool": str(fixture.tool_descriptor.get("name", "")),
        "expected": fixture.expected,
        "golden_archetype": fixture.golden_archetype,
    }
    if isinstance(result, GenFailure):
        record["outcome"] = "rejected"
        record["reject_reason"] = result.reason
        record["expected_match"] = fixture.expected == "rejected"
        return record

    record["outcome"] = "spec"
    record["archetype"] = result.archetype.value
    record["slot_count"] = _slot_count(result)
    record["schema_valid"] = _schema_valid(result)
    record["paths_resolve"] = SurfaceSpecLinter.lint(result, sample).ok
    record["archetype_ok"] = result.archetype.value == fixture.golden_archetype
    record["labels_ok"] = all(label_quality_ok(label) for label in _all_labels(result))
    record["field_count_sane"] = (
        _FIELD_COUNT_MIN <= _slot_count(result) <= _FIELD_COUNT_MAX
    )
    record["expected_match"] = fixture.expected == "spec"
    return record


def _schema_valid(spec: SurfaceSpec) -> bool:
    try:
        validate_surface_spec(spec.model_dump(mode="json", exclude_none=True))
        return True
    except Exception:
        return False


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def aggregate(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Reduce per-fixture records to the aggregate rates (deterministic)."""

    specs = [r for r in records if r["outcome"] == "spec"]
    rejected_expected = [r for r in records if r["expected"] == "rejected"]
    n_specs = len(specs)
    return {
        "schema_valid_rate": _rate(sum(1 for r in specs if r["schema_valid"]), n_specs),
        "path_resolution_rate": _rate(
            sum(1 for r in specs if r["paths_resolve"]), n_specs
        ),
        "archetype_accuracy": _rate(
            sum(1 for r in specs if r["archetype_ok"]), n_specs
        ),
        "label_quality_rate": _rate(sum(1 for r in specs if r["labels_ok"]), n_specs),
        "field_count_sane_rate": _rate(
            sum(1 for r in specs if r["field_count_sane"]), n_specs
        ),
        "rejection_accuracy": _rate(
            sum(1 for r in rejected_expected if r["outcome"] == "rejected"),
            len(rejected_expected),
        ),
        "expected_match_rate": _rate(
            sum(1 for r in records if r["expected_match"]), len(records)
        ),
    }


__all__ = ["aggregate", "label_quality_ok", "score_fixture"]
