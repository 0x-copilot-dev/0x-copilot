"""F-013 reachability canaries for the MCP/operation presentation boundary."""

from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_MCP_ADAPTER = _ROOT / "src/agent_runtime/capabilities/mcp/operation_adapter.py"
_GATEWAY = _ROOT / "src/agent_runtime/capabilities/operations/gateway.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_mcp_operation_adapter_has_no_direct_ledger_or_surface_presentation_imports() -> (
    None
):
    """A provider adapter cannot regain UI/ledger ownership by a local import."""

    tree = _tree(_MCP_ADAPTER)
    banned_symbols = {
        "WorkLedgerEmitter",
        "SurfaceProjector",
        "SurfaceGenerationScheduler",
    }
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    presentation_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not imported & banned_symbols
    assert not presentation_modules & {
        "agent_runtime.surfaces_v2.emitter",
        "agent_runtime.capabilities.surfaces.generator",
        "agent_runtime.capabilities.surfaces.projector",
    }
    assert "on_tool_result" not in attributes


def test_operation_gateway_is_the_only_outcome_presentation_reachability_seam() -> None:
    """The generic gateway, not a transport adapter, calls ``present``."""

    tree = _tree(_GATEWAY)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "present" in calls
