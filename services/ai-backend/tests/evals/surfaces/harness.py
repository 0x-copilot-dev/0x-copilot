"""Eval runner for the spec-authoring skill (generative-UI PRD-11).

Drives the real generation pipeline (``SurfaceSpecGenerator`` → validate → lint)
over the corpus with an injected completion, scores every fixture with the
deterministic scorers, and assembles a stable JSON report
``{model, skill_version, counts, aggregate, per_fixture}``. The same runner backs
both the hermetic replay run (CI) and the live-model matrix (``-m evals``); only
the injected completion differs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    SpecAuthoringSkill,
    SpecCompletionPort,
    SurfaceSpecGenerator,
)

from tests.evals.surfaces.corpus import CORPUS, EvalFixture
from tests.evals.surfaces.scorers import aggregate, score_fixture

CompletionFor = Callable[[EvalFixture], SpecCompletionPort]


def _descriptor(fixture: EvalFixture) -> GenToolDescriptor:
    td = fixture.tool_descriptor
    return GenToolDescriptor(
        name=str(td.get("name", "")),
        description=str(td.get("description", "")),
        input_schema=td.get("input_schema", {}) or {},
        output_shape=td.get("output_shape", {}) or {},
    )


async def run_corpus(
    *,
    completion_for: CompletionFor,
    model_id: str,
    fixtures: list[EvalFixture] | None = None,
) -> dict[str, Any]:
    """Run every fixture through generation, score it, and build the report."""

    corpus = fixtures if fixtures is not None else CORPUS
    skill_version = SpecAuthoringSkill.load().skill_version
    records: list[dict[str, Any]] = []
    for fixture in corpus:
        generator = SurfaceSpecGenerator(completion=completion_for(fixture))
        result = await generator.generate(
            server=fixture.server,
            tool_descriptor=_descriptor(fixture),
            sample_output=fixture.sample_output,
        )
        records.append(score_fixture(fixture, result, fixture.sample_output))

    records.sort(key=lambda r: r["id"])
    return {
        "model": model_id,
        "skill_version": skill_version,
        "fixture_count": len(records),
        "spec_count": sum(1 for r in records if r["outcome"] == "spec"),
        "rejected_count": sum(1 for r in records if r["outcome"] == "rejected"),
        "aggregate": aggregate(records),
        "per_fixture": records,
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    """Write the report as pretty, sorted JSON (stable across runs)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


__all__ = ["CompletionFor", "run_corpus", "write_report"]
