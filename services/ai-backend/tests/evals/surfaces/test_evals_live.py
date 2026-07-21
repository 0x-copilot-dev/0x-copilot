"""Live-model eval matrix for the spec-authoring skill (generative-UI PRD-11).

MARKED ``evals`` ⇒ EXCLUDED from the default `pytest` / CI run (pyproject
``addopts = -m 'not evals'``). Run deliberately, locally, to refresh the
model-routing data whenever the skill or the model lineup changes::

    SURFACE_SPEC_MODEL=openai:gpt-5-nano \\
      pytest -m evals tests/evals/surfaces/test_evals_live.py

Runs the same corpus + scorers as the hermetic harness, but against a real model
via the production ``LangChainSpecCompletion``. Writes a timestamped JSON report
under ``tests/evals/surfaces/reports/`` (runtime output — not committed). Never
runs in CI; there are no live-model calls in the default suite.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.evals.surfaces.corpus import CORPUS, EvalFixture
from tests.evals.surfaces.harness import run_corpus, write_report

pytestmark = pytest.mark.evals


def _sanitize(model_id: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in model_id).strip("-")


class TestLiveEvalMatrix:
    async def test_run_corpus_against_configured_model(self) -> None:
        model_id = os.environ.get("SURFACE_SPEC_MODEL", "").strip()
        if not model_id:
            pytest.skip("SURFACE_SPEC_MODEL is unset — nothing to evaluate")

        from agent_runtime.capabilities.surfaces.generator import (  # noqa: PLC0415
            LangChainSpecCompletion,
        )
        from agent_runtime.execution.deep_agent_builder import (  # noqa: PLC0415
            build_chat_model_from_id,
        )

        model = build_chat_model_from_id(model_id)
        completion = LangChainSpecCompletion(model=model, model_id=model_id)

        def completion_for(_fixture: EvalFixture) -> LangChainSpecCompletion:
            return completion

        report = await run_corpus(completion_for=completion_for, model_id=model_id)

        reports_dir = Path(__file__).parent / "reports"
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = reports_dir / f"{_sanitize(model_id)}.{stamp}.json"
        write_report(report, out)

        # The run is the deliverable; assert only that it covered the corpus and
        # produced a well-formed report (scores are data, not a pass/fail gate).
        assert report["fixture_count"] == len(CORPUS)
        assert set(report["aggregate"]) >= {
            "schema_valid_rate",
            "path_resolution_rate",
            "archetype_accuracy",
        }
        print(f"\n[evals] {model_id} → {out}\n{report['aggregate']}")
