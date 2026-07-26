"""F-013 canaries for the MCP/operation presentation ownership boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agent_runtime.capabilities.operations.presentation_boundary import (
    PresentationBoundaryViolation,
    assert_transport_adapter_presentation_boundary,
    presentation_boundary_violations,
)

_ROOT = Path(__file__).resolve().parents[3]
_MCP_ADAPTER = _ROOT / "src/agent_runtime/capabilities/mcp/operation_adapter.py"
_GATEWAY = _ROOT / "src/agent_runtime/capabilities/operations/gateway.py"


def test_mcp_operation_adapter_is_presentation_neutral_at_runtime() -> None:
    """The source policy runs before an MCP adapter receives client authority."""

    assert_transport_adapter_presentation_boundary(_MCP_ADAPTER)


@pytest.mark.parametrize(
    "source",
    (
        # Ordinary module import and module alias.
        "import agent_runtime.surfaces_v2.emitter\n",
        "import agent_runtime.surfaces_v2.emitter as ledger\n",
        # Direct symbol import and symbol alias.
        ("from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter as Ledger\n"),
        # Lexical depth does not weaken the policy.
        (
            "def bypass():\n"
            "    from agent_runtime.capabilities.surfaces.projector "
            "import SurfaceProjector\n"
        ),
        # Importing a presentation child through its parent package is still
        # a direct recovery of the presentation implementation.
        "from agent_runtime.surfaces_v2 import emitter as ledger\n",
        # Relative imports have no stable target in a standalone source scan,
        # so the transport adapter permits only auditable absolute imports.
        "from ...surfaces_v2 import emitter\n",
        # Dynamic imports are prohibited entirely in provider adapters, closing
        # aliases and computed-target forms rather than merely matching a name.
        (
            "import importlib as loader\n"
            "def bypass():\n"
            "    return loader.import_module('agent_runtime.surfaces_v2.emitter')\n"
        ),
        (
            "from importlib import import_module as load\n"
            "def bypass():\n"
            "    return load('agent_runtime.capabilities.surfaces.generator')\n"
        ),
        ("def bypass():\n    return __import__('agent_runtime.surfaces_v2.emitter')\n"),
    ),
)
def test_presentation_boundary_rejects_import_bypasses(source: str) -> None:
    """Planted ordinary, alias, local, and dynamic imports all fail closed."""

    assert presentation_boundary_violations(source)


def test_presentation_boundary_runtime_guard_fails_closed(tmp_path: Path) -> None:
    """The same scanner protects runtime construction, not only CI tests."""

    planted = tmp_path / "provider_adapter.py"
    planted.write_text(
        "import importlib as loader\n"
        "emitter = loader.import_module('agent_runtime.surfaces_v2.emitter')\n",
        encoding="utf-8",
    )

    with pytest.raises(PresentationBoundaryViolation):
        assert_transport_adapter_presentation_boundary(planted)


def test_operation_gateway_is_the_only_outcome_presentation_reachability_seam() -> None:
    """The generic gateway, not a transport adapter, calls ``present``."""

    tree = ast.parse(_GATEWAY.read_text(encoding="utf-8"), filename=str(_GATEWAY))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "present"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "outcome_presenter"
        for node in ast.walk(tree)
    )
