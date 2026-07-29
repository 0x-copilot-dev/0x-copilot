"""W3 canaries — the graph/F6 seam must not become the edge it was built to avoid.

``capabilities.concurrency`` imports ``control_plane`` and ``execution``, so the
reverse edge is a real cycle rather than a matter of taste. W3 adds a second port
to that boundary (``RuntimeBatchAdmissionPort``) and one consumer on the hot path
(the tool middleware), and both are only safe while they point the same way the
first port does.

Pure AST, so nothing here imports the code under test and the rules hold even for
a module that would fail to import.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[3] / "src"
_CONCURRENCY_PREFIX = "agent_runtime.capabilities.concurrency"
_SEAM_MODULE = f"{_CONCURRENCY_PREFIX}.graph_admission"
_CONTRACTS = _SOURCE / "agent_runtime" / "execution" / "contracts.py"
_MIDDLEWARE = (
    _SOURCE
    / "agent_runtime"
    / "capabilities"
    / "middleware"
    / "runtime_tool_control.py"
)
_PACKAGE_INIT = (
    _SOURCE / "agent_runtime" / "capabilities" / "concurrency" / "__init__.py"
)
_COMPOSER = _SOURCE / "runtime_worker" / "batch_concurrency_composition.py"


def _module_level_imports(path: Path) -> list[str]:
    """Return absolute module names imported at module scope only.

    Deferred imports inside a function body are deliberately excluded: they are
    the mechanism a composition root uses to read its presence gate before it
    pays for a subsystem, so counting them would forbid the very pattern these
    rules exist to protect.
    """

    tree = ast.parse(path.read_text())
    imported: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.If):
            # ``if TYPE_CHECKING:`` blocks are module scope for our purposes:
            # they cost nothing at runtime but still express a dependency.
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import):
                    imported.extend(alias.name for alias in inner.names)
                elif (
                    isinstance(inner, ast.ImportFrom)
                    and inner.level == 0
                    and inner.module
                ):
                    imported.append(inner.module)
    return imported


def test_the_batch_admission_port_never_imports_the_concurrency_lane() -> None:
    """The new port points the same way ``ParallelAdmissionPort`` does."""

    offenders = [
        name
        for name in _module_level_imports(_CONTRACTS)
        if name.startswith(_CONCURRENCY_PREFIX)
    ]

    assert offenders == []


def test_the_graph_tool_seam_never_imports_the_concurrency_lane() -> None:
    """The middleware speaks the port, never the subsystem behind it.

    This is both a boundary rule and the feature-off parity rule: the middleware
    is constructed for every compiled graph in every deployment, so an import
    here would put F6 on every import graph unconditionally.
    """

    offenders = [
        name
        for name in _module_level_imports(_MIDDLEWARE)
        if name.startswith(_CONCURRENCY_PREFIX)
    ]

    assert offenders == []


def test_the_graph_seam_is_not_re_exported_from_the_package() -> None:
    """``graph_admission`` stays reachable only by module path.

    ``runtime_api.schemas.events`` imports this package's journal record, so the
    package ``__init__`` runs in every deployment. Re-exporting the graph seam
    from it would load the seam everywhere and silently undo the parity the
    composition gate buys.
    """

    imported = _module_level_imports(_PACKAGE_INIT)

    assert _SEAM_MODULE not in imported


def test_the_composition_root_defers_every_concurrency_import() -> None:
    """The presence gate is readable without paying for the subsystem."""

    runtime_imports = [
        name
        for name in _module_level_imports(_COMPOSER)
        if name.startswith(_CONCURRENCY_PREFIX)
    ]
    source = _COMPOSER.read_text()

    # Type-checking-only imports are allowed and expected; runtime ones are not.
    assert all(f"    from {name} import" in source for name in runtime_imports)
    tree = ast.parse(source)
    top_level = [
        name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        for name in [node.module]
        if name.startswith(_CONCURRENCY_PREFIX)
    ]
    assert top_level == []
