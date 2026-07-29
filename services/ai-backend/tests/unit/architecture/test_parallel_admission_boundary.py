"""SMELL-01 canaries: F6 widens the run gate without inverting the layering.

``capabilities.concurrency`` already imports ``control_plane``. So the obvious
implementation of "let the admission ask F6" — importing the coordinator from
``control_plane.context`` — is a genuine import cycle, not a style preference.
The port in ``control_plane.parallel_admission`` exists to keep the dependency
pointing the way it already points, and these tests are what keep it that way
once the module stops being new.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CONTROL_PLANE = _ROOT / "src/agent_runtime/control_plane"
_CONCURRENCY = _ROOT / "src/agent_runtime/capabilities/concurrency"
_PORT = _CONTROL_PLANE / "parallel_admission.py"
_MIDDLEWARE = (
    _ROOT / "src/agent_runtime/capabilities/middleware/runtime_tool_control.py"
)

_FORBIDDEN_PREFIX = "agent_runtime.capabilities.concurrency"


def _imported_modules(path: Path) -> set[str]:
    """Return every module name ``path`` imports, absolute form only."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _package_imports(package: Path) -> dict[Path, set[str]]:
    return {path: _imported_modules(path) for path in sorted(package.glob("*.py"))}


def test_the_reverse_edge_this_boundary_protects_actually_exists() -> None:
    """Document *why* the forbidden direction is a cycle rather than a taste.

    If this ever fails, ``capabilities.concurrency`` stopped depending on
    ``control_plane`` and the port's rationale needs rereading — not deleting.
    """

    importers = {
        path.name
        for path, modules in _package_imports(_CONCURRENCY).items()
        if any(module.startswith("agent_runtime.control_plane") for module in modules)
    }

    assert importers, "capabilities.concurrency no longer imports control_plane"


def test_control_plane_never_imports_the_concurrency_lane() -> None:
    """The cycle-forming direction, forbidden across the whole package."""

    offenders = {
        path.name: sorted(
            module for module in modules if module.startswith(_FORBIDDEN_PREFIX)
        )
        for path, modules in _package_imports(_CONTROL_PLANE).items()
        if any(module.startswith(_FORBIDDEN_PREFIX) for module in modules)
    }

    assert offenders == {}


def test_the_admission_port_depends_on_nothing_in_the_runtime() -> None:
    """The seam F6 implements must be free of every runtime layer.

    A port that imported ``capabilities`` — even a part that is not
    ``concurrency`` today — would be one refactor away from reintroducing the
    cycle it exists to prevent.
    """

    runtime_imports = sorted(
        module
        for module in _imported_modules(_PORT)
        if module.startswith("agent_runtime")
    )

    assert runtime_imports == []


def test_the_middleware_still_imports_the_gate_from_the_control_plane() -> None:
    """The seam sits where §8 put it: capabilities depends on control_plane."""

    modules = _imported_modules(_MIDDLEWARE)

    assert "agent_runtime.control_plane.context" in modules
    assert not any(module.startswith(_FORBIDDEN_PREFIX) for module in modules)


def test_the_graph_tool_seam_is_the_only_taker_of_the_run_permit() -> None:
    """No second admission path may exist beside ``wrap``/``awrap_tool_call``.

    Step 2 closed the bypass by making one middleware the sole gate. Widening
    what that gate *decides* must not add a second place that decides at all, so
    the permit's takers are pinned to the two graph tool seams.
    """

    source = _ROOT / "src/agent_runtime"
    takers: set[str] = set()
    for path in source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "async_permit(" in text or "sync_permit(" in text:
            takers.add(str(path.relative_to(source)))

    assert takers == {
        "control_plane/context.py",
        "capabilities/middleware/runtime_tool_control.py",
    }
