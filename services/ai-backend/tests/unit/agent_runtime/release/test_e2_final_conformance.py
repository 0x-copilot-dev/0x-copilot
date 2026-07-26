"""E2 D9 unified release report and adversarial canaries."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.effects.conformance import (
    canonical_effect_result_producer_present,
    effect_applied_producer_violations,
    workspace_executor_constructor_violations,
)
from agent_runtime.observability.llm_seam_conformance import llm_seam_violations
from agent_runtime.release.e2_final_conformance import (
    ConformanceStatus,
    E2FinalConformancePaths,
    E2FinalConformanceRunner,
)


def test_current_report_has_all_twelve_numbered_conditions_and_is_ready() -> None:
    report = E2FinalConformanceRunner().run()

    assert [item.number for item in report.conditions] == list(range(1, 13))
    assert all(item.evidence for item in report.conditions)
    assert report.ready is True
    assert all(item.status is ConformanceStatus.PASS for item in report.conditions)


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


def test_descriptor_executor_gap_is_blocked_not_passed(tmp_path: Path) -> None:
    paths = E2FinalConformancePaths.current()
    missing_builtin_factory = tmp_path / "mcp_operation_storage.py"
    missing_builtin_factory.write_text(
        "factories = {EffectExecutorKind.MCP: lambda scope: object()}\n",
        encoding="utf-8",
    )
    condition = E2FinalConformanceRunner(
        paths=E2FinalConformancePaths(
            repo_root=paths.repo_root,
            source_root=paths.source_root,
            descriptor_factory=missing_builtin_factory,
            surface_schema=paths.surface_schema,
            renderer_spec_types=paths.renderer_spec_types,
        )
    )._effect_descriptor_execution_path()

    assert condition.status is ConformanceStatus.BLOCKED
    assert "uncomposed executor kinds" in condition.evidence[0]
