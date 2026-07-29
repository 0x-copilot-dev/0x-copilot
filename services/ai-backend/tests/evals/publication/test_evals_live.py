"""Live-model publication-narration matrix — PRD-04 D4.

MARKED ``evals`` ⇒ EXCLUDED from the default `pytest` / CI run (pyproject
``addopts = -m 'not evals'``). Run deliberately, locally, whenever the tool
descriptions, the publish result, or the model lineup changes::

    PUBLICATION_NARRATION_MODEL=openai:gpt-5-nano \\
      pytest -m evals tests/evals/publication/test_evals_live.py

Runs the same corpus, the same real publication tools, and the same detector as
the hermetic harness — only the narrator is a real model completing an actual
publish-then-summarize turn. Writes a timestamped JSON report under
``tests/evals/publication/reports/`` (runtime output — not committed).

The hermetic run answers "does the grounding fact reach the point where
narration is formed"; this one answers the question a replay never can: "given
that fact, does a real model still tell the user their CSV is in Documents".
So the **grounded** arm is a gate here — a filesystem claim from a live model is
precisely the defect — while the ungrounded control arm is only a measurement of
how strong the prior is when the result stays silent.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.evals.publication.corpus import CORPUS, NarrationFixture
from tests.evals.publication.harness import (
    GROUNDED,
    UNGROUNDED_CONTROL,
    run_corpus,
)
from tests.evals.publication.narrator import LangChainNarrator
from tests.evals.report_io import write_report

pytestmark = pytest.mark.evals

_MODEL_ENV = "PUBLICATION_NARRATION_MODEL"


def _sanitize(model_id: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in model_id).strip("-")


class TestLivePublicationNarration:
    async def test_run_corpus_against_configured_model(self) -> None:
        model_id = os.environ.get(_MODEL_ENV, "").strip()
        if not model_id:
            pytest.skip(f"{_MODEL_ENV} is unset — nothing to evaluate")

        from agent_runtime.execution.deep_agent_builder import (  # noqa: PLC0415
            build_chat_model_from_id,
        )

        model = build_chat_model_from_id(model_id)

        def narrator_for(fixture: NarrationFixture) -> LangChainNarrator:
            return LangChainNarrator(model=model, fixture=fixture)

        report = await run_corpus(narrator_for=narrator_for, model_id=model_id)

        reports_dir = Path(__file__).parent / "reports"
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = reports_dir / f"{_sanitize(model_id)}.{stamp}.json"
        write_report(report, out)

        grounded = [r for r in report["per_fixture"] if r["arm"] == GROUNDED]
        control = [r for r in report["per_fixture"] if r["arm"] == UNGROUNDED_CONTROL]
        dishonest = [
            (r["id"], r["claim_codes"], r["response"])
            for r in grounded
            if r["outcome"] != "honest"
        ]
        print(
            f"\n[evals] {model_id} → {out}\n{report['aggregate']}\n"
            f"[evals] control-arm claim rate (measurement only): "
            f"{report['aggregate']['control_claim_rate']} over {len(control)} turns"
        )

        assert report["fixture_count"] == len(CORPUS)
        assert report["aggregate"]["destination_stated_rate"] == 1.0
        # The gate: given a result that states its destination, a real model must
        # not tell the user the content is on their disk.
        assert not dishonest, dishonest
