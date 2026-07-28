"""Step 0 content-free active-path baseline conformance."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime.harness_quality import (
    BASELINE_JOURNEY_IDS,
    GoldenTraceCatalog,
)


_FIXTURE = Path(__file__).parent / "fixtures" / "active_path_baseline_traces.v1.json"


def _catalog() -> GoldenTraceCatalog:
    return GoldenTraceCatalog.model_validate_json(_FIXTURE.read_text())


def test_active_path_golden_catalog_is_complete_and_digest_bound() -> None:
    catalog = _catalog()

    assert {trace.journey_id for trace in catalog.traces} == BASELINE_JOURNEY_IDS
    assert all(
        trace.ordered_steps[-1].event_type == trace.expected_last_event_type.value
        for trace in catalog.traces
    )


def test_golden_traces_materialize_as_f1_trajectory_manifests() -> None:
    catalog = _catalog()
    manifests = catalog.manifests()

    assert tuple(manifest.case_id for manifest in manifests) == tuple(
        trace.journey_id for trace in catalog.traces
    )
    assert all(manifest.run_id is None for manifest in manifests)
    assert all(manifest.evidence_refs == () for manifest in manifests)
    assert all(manifest.usage_summary == {} for manifest in manifests)
    assert tuple(manifest.manifest_digest for manifest in catalog.manifests()) == tuple(
        manifest.manifest_digest for manifest in manifests
    )


@pytest.mark.parametrize(
    ("journey_id", "required_events"),
    [
        ("approval_resume", {"approval_requested", "approval_resolved"}),
        ("cancel", {"run_cancelling", "run_cancelled"}),
        ("large_tool_result", {"tool_call_started", "tool_result"}),
        (
            "local_subagent",
            {"subagent_fleet_started", "subagent_started", "subagent_completed"},
        ),
        ("local_tool_use", {"tool_call_started", "tool_result"}),
        ("mcp_auth", {"mcp_auth_required"}),
        ("mcp_read", {"operation.requested", "operation.completed"}),
        ("mcp_write_staging", {"operation.classified", "effect.staged"}),
        ("ordinary_chat", {"model_call_started", "final_response"}),
        ("provider_error", {"error", "run_failed"}),
        ("timeout", {"error", "run_failed"}),
        ("workspace_draft", {"draft_updated", "tool_result"}),
    ],
)
def test_each_baseline_journey_retains_its_defining_milestones(
    journey_id: str,
    required_events: set[str],
) -> None:
    trace = next(trace for trace in _catalog().traces if trace.journey_id == journey_id)

    assert required_events <= {step.event_type for step in trace.ordered_steps}


def test_catalog_tampering_fails_closed() -> None:
    raw = json.loads(_FIXTURE.read_text())
    raw["traces"][0]["ordered_steps"][0][3] = "0" * 64

    with pytest.raises(ValidationError, match="trace_digest"):
        GoldenTraceCatalog.model_validate(raw)


def test_catalog_pins_the_installed_harness_versions() -> None:
    catalog = _catalog()

    assert catalog.harness_revisions == {
        "deepagents": version("deepagents"),
        "langchain": version("langchain"),
        "langgraph": version("langgraph"),
    }
