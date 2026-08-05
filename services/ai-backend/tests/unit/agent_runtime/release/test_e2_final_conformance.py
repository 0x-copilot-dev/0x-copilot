"""E2 D9 unified release report and adversarial canaries."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json
from pathlib import Path

import pytest

from copilot_service_contracts.work_ledger import load_legacy_v2_replay_corpus

from agent_runtime.effects.conformance import (
    canonical_effect_result_producer_present,
    effect_applied_producer_violations,
    workspace_executor_constructor_violations,
)
from agent_runtime.observability.llm_seam_conformance import llm_seam_violations
from agent_runtime.release import e2_final_conformance
from agent_runtime.release.e2_final_conformance import (
    ConformanceStatus,
    E2FinalConformancePaths,
    E2FinalConformanceRunner,
    _model_facing_mcp_dispatch_violations,
)


def test_current_report_passes_every_condition() -> None:
    """Presence is not a status.

    Asserting the twelve conditions are *there* and pinning the status of only
    one leaves the other eleven free to flip to FAIL with this suite green —
    which is exactly what a release gate must never allow, least of all in the
    round that rewrote condition 7.
    """

    report = E2FinalConformanceRunner().run()

    assert [item.number for item in report.conditions] == list(range(1, 13))
    assert all(item.evidence for item in report.conditions)
    assert {item.status for item in report.conditions} == {ConformanceStatus.PASS}
    assert report.ready is True


def _with_corpus(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict], None],
) -> None:
    """Run the gate against a deliberately damaged copy of the frozen corpus."""

    corpus = deepcopy(load_legacy_v2_replay_corpus())
    mutate(corpus)
    monkeypatch.setattr(
        e2_final_conformance, "load_legacy_v2_replay_corpus", lambda: corpus
    )


def test_a_historic_surface_that_hydrates_differently_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The condition's second fold is pinned, not merely folded twice.

    Carrying state on ``surface.created`` removed the ``payload_ref → call_id →
    tool_result.output`` re-join, and with it whatever a pre-carry record on a
    user's disk used to hydrate to. Comparing ``SurfaceContentProjection.fold``
    only to itself would call that a pass. The frozen ``content`` vector is what
    makes a change in what an old surface shows fail here.
    """

    _with_corpus(
        monkeypatch,
        lambda corpus: corpus["cases"][0]["expected"].update(
            {
                "content": {
                    "connector:linear:issue:ENG-142": {"data": {"invented": True}}
                }
            }
        ),
    )

    condition = E2FinalConformanceRunner()._historic_replay()

    assert condition.status is ConformanceStatus.FAIL
    assert "hydrated content drifted" in condition.evidence[0]


def test_a_case_with_no_frozen_content_pin_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unpinned case must not read as a covered one."""

    _with_corpus(
        monkeypatch, lambda corpus: corpus["cases"][0]["expected"].pop("content")
    )

    condition = E2FinalConformanceRunner()._historic_replay()

    assert condition.status is ConformanceStatus.FAIL
    assert "lacks content" in condition.evidence[0]


def test_the_golden_export_content_pin_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export hydrates to nothing today, and the vector has to say so."""

    assert load_legacy_v2_replay_corpus()["golden_export_content"] == {}
    _with_corpus(monkeypatch, lambda corpus: corpus.pop("golden_export_content"))

    condition = E2FinalConformanceRunner()._historic_replay()

    assert condition.status is ConformanceStatus.FAIL
    assert "golden export lacks a frozen content pin" in condition.evidence[0]


def test_report_json_is_complete_and_has_no_implicit_passes() -> None:
    report = E2FinalConformanceRunner().run().as_json()

    assert report["contract"] == "e2-final-conformance-v1"
    assert report["ready"] is True
    conditions = report["conditions"]
    assert isinstance(conditions, list)
    assert {item["status"] for item in conditions} <= {"pass", "fail", "blocked"}
    assert len(conditions) == 12


def test_rogue_terminal_effect_producer_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "runtime_api/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("event_type = 'effect.applied'\n", encoding="utf-8")

    assert effect_applied_producer_violations(tmp_path) == ("runtime_api/rogue.py:1",)


def test_missing_canonical_effect_producer_is_detected(tmp_path: Path) -> None:
    assert canonical_effect_result_producer_present(tmp_path) is False


def test_rogue_workspace_constructor_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "runtime_api/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("WorkspaceEffectExecutor(scope=scope)\n", encoding="utf-8")

    assert workspace_executor_constructor_violations(tmp_path) == (
        "runtime_api/rogue.py:1",
    )


def test_direct_model_construction_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "rogue.py"
    rogue.write_text(
        "from langchain.chat_models import init_chat_model\n"
        "model = init_chat_model('test')\n",
        encoding="utf-8",
    )

    assert llm_seam_violations(tmp_path) == (
        "rogue.py: imports init_chat_model",
        "rogue.py: references init_chat_model()",
    )


def test_rogue_model_facing_mcp_client_dispatch_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime/capabilities/mcp/middleware/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "client = provider.create_client(card)\n"
        "await client.call_tool(tool_name='write', arguments={})\n",
        encoding="utf-8",
    )

    assert _model_facing_mcp_dispatch_violations(tmp_path) == (
        "agent_runtime/capabilities/mcp/middleware/rogue.py:1: create_client",
        "agent_runtime/capabilities/mcp/middleware/rogue.py:2: call_tool",
    )


def test_missing_fixed_surface_schema_is_fail_closed(tmp_path: Path) -> None:
    paths = E2FinalConformancePaths.current()
    broken_schema = tmp_path / "surface_spec.schema.json"
    broken_schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    runner = E2FinalConformanceRunner(
        paths=E2FinalConformancePaths(
            repo_root=paths.repo_root,
            source_root=paths.source_root,
            descriptor_factory=paths.descriptor_factory,
            surface_schema=broken_schema,
            renderer_spec_types=paths.renderer_spec_types,
        )
    )

    assert runner._fixed_ui_schema().status is ConformanceStatus.FAIL


def test_boundary_and_dark_scanner_failures_are_not_waived() -> None:
    runner = E2FinalConformanceRunner(
        dark_capability_check=lambda: ("RUNTIME_ENABLE_ROGUE",),
        service_boundary_check=lambda: ("ai-backend:rogue.py:1:backend_app",),
    )

    assert runner._dark_capabilities().status is ConformanceStatus.FAIL
    assert runner._service_boundaries().status is ConformanceStatus.FAIL


def test_descriptor_stager_mapping_gap_is_blocked_not_passed(monkeypatch) -> None:
    from agent_runtime.effects import composition

    monkeypatch.setattr(composition, "EFFECT_DESCRIPTOR_STAGE_MAPPINGS", ())

    condition = E2FinalConformanceRunner()._effect_descriptor_execution_path()

    assert condition.status is ConformanceStatus.BLOCKED
    assert "missing stage mapping" in condition.evidence[0]
