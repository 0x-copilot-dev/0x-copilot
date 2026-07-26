"""F-013 canaries for the one approved MCP effect dispatch path."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_STAGE_HANDLER = _ROOT / "src/runtime_worker/handlers/stage_commit.py"
_STAGE_ADAPTER = _ROOT / "src/runtime_worker/staged_write_effect_dispatch.py"
_DISPATCH = _ROOT / "src/agent_runtime/effects/dispatch.py"
_MCP_EXECUTOR = _ROOT / "src/runtime_worker/mcp_effect_executor.py"
_LEGACY_ENGINE = _ROOT / "src/agent_runtime/surfaces_v2/commit_engine.py"


def _calls(path: Path, attribute: str) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    ]


def test_staged_writes_cannot_dispatch_a_connector_from_the_handler_or_adapter() -> (
    None
):
    """The legacy fold path has no independent ``connector.execute`` escape hatch."""

    assert _calls(_STAGE_HANDLER, "execute") == []
    assert _calls(_STAGE_ADAPTER, "execute") == []
    assert _calls(_LEGACY_ENGINE, "execute") == []


def test_shared_dispatcher_owns_the_only_executor_apply_call() -> None:
    """Both generic operations and staged writes converge before transport apply."""

    apply_calls = _calls(_DISPATCH, "apply")
    assert len(apply_calls) == 1

    connector_calls = _calls(_MCP_EXECUTOR, "execute")
    assert len(connector_calls) == 1

    stage_source = _STAGE_ADAPTER.read_text(encoding="utf-8")
    assert "EffectDispatchCoordinator" in stage_source
    assert "McpEffectExecutor" in stage_source
