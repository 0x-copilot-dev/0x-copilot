"""F-013 canaries for the MCP/operation presentation ownership boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest
from agent_runtime.capabilities.operations.presentation_boundary import (
    PresentationBoundaryViolation,
    assert_transport_adapter_presentation_boundary,
    presentation_boundary_violations,
)
from agent_runtime.capabilities.mcp.execution_services import (
    McpOperationExecutionServices,
)
from agent_runtime.capabilities.operations.context import (
    BoundOperationExecutionContext,
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot

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
        # Builtins and reflection aliases are generic capability recovery
        # tools, so adapters reject them irrespective of a target spelling.
        (
            "import builtins as runtime\n"
            "def bypass():\n"
            "    return runtime.__import__('agent_runtime.surfaces_v2.emitter')\n"
        ),
        (
            "from builtins import getattr as inspect\n"
            "def bypass(context):\n"
            "    return inspect(context, 'outcome_presenter')\n"
        ),
        # Owners protect themselves with a source capability marker; the
        # adapter guard does not grow a module-name denylist.
        (
            "from agent_runtime.capabilities.operations.presentation "
            "import SurfaceLedgerOperationOutcomePresenter as Wrapper\n"
        ),
        (
            "from agent_runtime.capabilities.operations.context "
            "import _GatewayPresentationContext\n"
        ),
        (
            "import agent_runtime.capabilities.operations.context as context\n"
            "def bypass():\n"
            "    return context._GatewayPresentationContext.require()\n"
        ),
        # The full gateway composition is source-owned by the gateway and
        # cannot be imported back into the transport adapter.
        (
            "from agent_runtime.capabilities.mcp.gateway_context "
            "import McpOperationGatewayContext\n"
        ),
        (
            "def bypass():\n"
            "    return __builtins__['__import__'](\n"
            "        'agent_runtime.surfaces_v2.emitter'\n"
            "    )\n"
        ),
    ),
)
def test_presentation_boundary_rejects_import_bypasses(source: str) -> None:
    """Planted ordinary, alias, local, and dynamic imports all fail closed."""

    assert presentation_boundary_violations(source)


def test_presentation_boundary_runtime_guard_fails_closed(tmp_path: Path) -> None:
    """The same scanner protects runtime construction, not only CI tests."""

    planted = tmp_path / "provider_adapter.py"
    planted.write_text(
        "import builtins as runtime\n"
        "emitter = runtime.__import__('agent_runtime.surfaces_v2.emitter')\n",
        encoding="utf-8",
    )

    with pytest.raises(PresentationBoundaryViolation):
        assert_transport_adapter_presentation_boundary(planted)


def test_adapter_visible_context_and_dependencies_have_no_presentation_path() -> None:
    """The decisive boundary is object-graph removal, not an import filter."""

    execution_fields = {field.name for field in fields(BoundOperationExecutionContext)}
    adapter_fields = {field.name for field in fields(McpOperationExecutionServices)}

    forbidden = {
        "artifact_service",
        "gateway",
        "ledger_emitter",
        "outcome_presenter",
        "projector",
        "scheduler",
    }
    assert execution_fields.isdisjoint(forbidden)
    assert adapter_fields.isdisjoint(forbidden)
    adapter_source = _MCP_ADAPTER.read_text(encoding="utf-8")
    assert "McpOperationGatewayContext" not in adapter_source
    assert "McpOperationGatewayServices" not in adapter_source
    assert "OperationPresentationOutcome" not in adapter_source


def test_operation_context_require_cannot_traverse_to_presentation() -> None:
    """Binding a presenter does not make it reachable through ``require()``."""

    ledger = object()
    artifacts = object()
    presenter = object()
    token = OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id="org_boundary",
            user_id="user_boundary",
            conversation_id="conv_boundary",
            run_id="run_boundary",
        ),
        policy_snapshot=ToolUsePolicySnapshot.from_response(),
        ledger_emitter=ledger,  # type: ignore[arg-type]
        artifact_service=artifacts,  # type: ignore[arg-type]
        outcome_presenter=presenter,  # type: ignore[arg-type]
        mode=OperationGatewayMode.OFF,
    )
    try:
        execution = OperationContext.require()
    finally:
        OperationContext.unbind(token)

    assert not hasattr(execution, "ledger_emitter")
    assert not hasattr(execution, "outcome_presenter")
    assert not hasattr(execution, "artifact_service")
    assert ledger not in vars(execution).values()
    assert artifacts not in vars(execution).values()
    assert presenter not in vars(execution).values()


def test_operation_gateway_constructs_and_presents_the_generic_outcome() -> None:
    """The generic gateway, not a transport adapter, owns presentation."""

    tree = ast.parse(_GATEWAY.read_text(encoding="utf-8"), filename=str(_GATEWAY))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "present"
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OperationPresentationOutcome"
        for node in ast.walk(tree)
    )
