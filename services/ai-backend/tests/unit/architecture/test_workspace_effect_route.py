"""Canaries for C3's sole workspace-effect construction boundary."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.effects.conformance import (
    canonical_workspace_executor_constructor_present,
    workspace_executor_constructor_violations,
)


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"


def test_workspace_executor_is_constructed_only_by_worker_registry_adapter() -> None:
    assert canonical_workspace_executor_constructor_present(_SOURCE_ROOT)
    assert workspace_executor_constructor_violations(_SOURCE_ROOT) == ()


def test_guard_rejects_a_planted_direct_workspace_executor(tmp_path: Path) -> None:
    rogue = tmp_path / "runtime_api" / "workspace_apply.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "executor = WorkspaceEffectExecutor(scope=scope)\n",
        encoding="utf-8",
    )

    assert workspace_executor_constructor_violations(tmp_path) == (
        "runtime_api/workspace_apply.py:1",
    )
