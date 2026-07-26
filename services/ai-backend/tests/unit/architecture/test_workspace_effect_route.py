"""Canaries for C3's sole workspace-effect construction boundary."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.effects.conformance import (
    canonical_workspace_executor_constructor_present,
    workspace_executor_constructor_violations,
)


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"
_WORKSPACE_ROOT = _SERVICE_ROOT.parents[1]
_DIRECT_WORKSPACE_SOURCES = (
    Path("agent_runtime/capabilities/desktop/broker_client.py"),
    Path("agent_runtime/capabilities/desktop/workspace_backend.py"),
    Path("agent_runtime/execution/factory.py"),
)


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


def test_retirement_leaves_no_direct_v1_workspace_mutation_dispatch() -> None:
    """D7: only C2's prepared authority may mutate a host workspace."""

    source = "\n".join(
        (_SOURCE_ROOT / path).read_text(encoding="utf-8")
        for path in _DIRECT_WORKSPACE_SOURCES
    )
    source += "\n" + (
        _WORKSPACE_ROOT / "apps/desktop/main/capabilities/broker.ts"
    ).read_text(encoding="utf-8")
    for retired in (
        '"/v1/fs/write"',
        '"/v1/fs/edit"',
        '"/v1/fs/mkdir"',
        '"/v1/fs/delete"',
        '"/v1/fs/move"',
        "self._client.write(",
        "self._client.edit(",
    ):
        assert retired not in source
    backend = (
        _SOURCE_ROOT / "agent_runtime/capabilities/desktop/workspace_backend.py"
    ).read_text(encoding="utf-8")
    assert "OperationShadowProbe.invoke_legacy" not in backend
    factory = (_SOURCE_ROOT / "agent_runtime/execution/factory.py").read_text(
        encoding="utf-8"
    )
    assert '"/workspace/**"' not in factory
