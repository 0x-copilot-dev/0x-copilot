"""Hermetic (replay) eval-harness tests (generative-UI PRD-11, AC1).

These are ORDINARY unit tests — no ``evals`` marker — so they run in default CI
with no live model. They exercise the scorers + injection lint end-to-end via the
recorded-output replay, and pin the committed baseline as a golden regression:
change a scorer, the corpus, or the skill_version and this fails until the
baseline is refreshed (regenerate with tools/scratch and re-commit).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.capabilities.surfaces.generator import SpecLintCode

from tests.evals.surfaces.corpus import (
    ADVERSARIAL_FIXTURES,
    REAL_FIXTURES,
    EvalFixture,
)
from tests.evals.surfaces.harness import run_corpus
from tests.evals.surfaces.replay import ReplayCompletion
from tests.evals.surfaces.scorers import label_quality_ok

_BASELINE = Path(__file__).parent / "baselines" / "baseline_replay.json"


def _completion_for(fixture: EvalFixture) -> ReplayCompletion:
    return ReplayCompletion(fixture.recorded_output)


async def _run() -> dict[str, object]:
    return await run_corpus(completion_for=_completion_for, model_id="replay")


class TestCorpusShape:
    def test_corpus_meets_prd_minimums(self) -> None:
        assert len(REAL_FIXTURES) >= 20
        assert len(ADVERSARIAL_FIXTURES) >= 5

    def test_adversarial_shapes_are_covered(self) -> None:
        ids = {f.id for f in ADVERSARIAL_FIXTURES}
        # injection-in-values, 40-key flat, deep nesting, empty arrays, unicode.
        assert any("injection" in i for i in ids)
        assert any("forty_keys" in i for i in ids)
        assert any("deep_nesting" in i for i in ids)
        assert any("empty_arrays" in i for i in ids)
        assert any("unicode" in i for i in ids)


class TestScorersHermetic:
    async def test_goldens_score_clean(self) -> None:
        report = await _run()
        agg = report["aggregate"]
        # Goldens are valid, resolve, and pick the right archetype by construction.
        assert agg["schema_valid_rate"] == 1.0
        assert agg["path_resolution_rate"] == 1.0
        assert agg["archetype_accuracy"] == 1.0
        assert agg["label_quality_rate"] == 1.0
        # The corpus deliberately mixes rich and sparse specs, so field-count
        # sanity is a genuine (sub-1.0) rate — a measurement, not a pass gate.
        assert 0.0 < agg["field_count_sane_rate"] < 1.0

    async def test_adversarial_reject_fixtures_are_rejected(self) -> None:
        report = await _run()
        by_id = {r["id"]: r for r in report["per_fixture"]}
        assert by_id["adv.injection_values_tainted"]["outcome"] == "rejected"
        assert by_id["adv.forty_keys_dumped"]["outcome"] == "rejected"
        # And rejection accuracy over all expected-rejected fixtures is perfect.
        assert report["aggregate"]["rejection_accuracy"] == 1.0

    async def test_expected_match_is_total(self) -> None:
        report = await _run()
        assert report["aggregate"]["expected_match_rate"] == 1.0
        assert (
            report["spec_count"] + report["rejected_count"] == report["fixture_count"]
        )


class TestBaselineRegression:
    async def test_report_matches_committed_baseline(self) -> None:
        baseline = json.loads(_BASELINE.read_text(encoding="utf-8"))
        report = await _run()
        assert report == baseline, (
            "Hermetic eval report drifted from the committed baseline. If this is "
            "an intended scorer/corpus/skill change, regenerate "
            "baselines/baseline_replay.json and re-commit."
        )


class TestLabelQualityScorer:
    def test_accepts_sentence_case_short_labels(self) -> None:
        assert label_quality_ok("Assignee")
        assert label_quality_ok("Due date")
        assert label_quality_ok("Open in Linear")

    def test_rejects_snake_case_all_caps_and_long(self) -> None:
        assert not label_quality_ok("assignee_display_name")
        assert not label_quality_ok("ASSIGNEE")
        assert not label_quality_ok("A label with far too many words here")
        assert not label_quality_ok("x" * 41)


class TestLintCodesAreStable:
    def test_reason_codes_exist(self) -> None:
        # Guards the vocabulary the metering + adversarial tests depend on.
        assert SpecLintCode.URL_PATH_UNSAFE == "url_path_unsafe"
        assert SpecLintCode.FIELD_COUNT_EXCEEDED == "field_count_exceeded"
