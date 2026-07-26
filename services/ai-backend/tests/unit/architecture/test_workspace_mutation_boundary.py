"""Architecture gates for the C1 workspace mutation authority boundary.

The Deep Agents workspace backend is model-visible.  It may reach the raw
overlay engine only through ``workspace.effects`` (the universal operation
gateway adapter); a direct or helper-mediated import would make staging
optional again.  These tests parse imports everywhere in a module, including
function-local imports, so moving an import into a helper cannot evade the
gate.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

from agent_runtime.capabilities.workspace import __all__ as workspace_exports
from agent_runtime.capabilities.workspace.merged_backend import MergedWorkspaceBackend
from agent_runtime.capabilities.workspace.overlay import __all__ as overlay_exports


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"
_RAW_ENGINE = "agent_runtime.capabilities.workspace.overlay"
_ENFORCED_GATEWAY = "agent_runtime.capabilities.workspace.effects"
_MODEL_VISIBLE_ROOTS = (
    "agent_runtime.capabilities.workspace.deep_backend",
    "agent_runtime.capabilities.workspace.merged_backend",
    "runtime_worker.handlers.run",
)


def _module_path(source_root: Path, module: str) -> Path | None:
    relative = Path(*module.split("."))
    candidate = source_root / relative.with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = source_root / relative / "__init__.py"
    return package if package.is_file() else None


def _current_module(source_root: Path, path: Path) -> str:
    relative = path.relative_to(source_root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from_module(current: str, module: str | None, level: int) -> str | None:
    if level == 0:
        return module
    package = current.rsplit(".", 1)[0]
    parent_parts = package.split(".")
    if level > len(parent_parts):
        return None
    prefix = ".".join(parent_parts[: 1 - level])
    return ".".join(part for part in (prefix, module) if part)


def _imports(source_root: Path, module: str) -> tuple[str, ...]:
    path = _module_path(source_root, module)
    if path is None:
        return ()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dependencies.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from_module(
                _current_module(source_root, path), node.module, node.level
            )
            if base is None:
                continue
            dependencies.add(base)
            # ``from package import submodule`` is a module edge when the
            # imported name resolves to a source file/package.
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                if _module_path(source_root, candidate) is not None:
                    dependencies.add(candidate)
    return tuple(sorted(dependencies))


def _unmediated_raw_engine_paths(source_root: Path) -> tuple[tuple[str, ...], ...]:
    """Return model-root → raw-engine import paths that miss the effect gateway."""

    violations: list[tuple[str, ...]] = []
    for root in _MODEL_VISIBLE_ROOTS:
        pending: deque[tuple[str, tuple[str, ...], bool]] = deque(
            [(root, (root,), root == _ENFORCED_GATEWAY)]
        )
        visited: set[tuple[str, bool]] = set()
        while pending:
            module, path, crossed_gateway = pending.popleft()
            state = (module, crossed_gateway)
            if state in visited:
                continue
            visited.add(state)
            for dependency in _imports(source_root, module):
                crossed = crossed_gateway or dependency == _ENFORCED_GATEWAY
                next_path = (*path, dependency)
                if dependency == _RAW_ENGINE:
                    if not crossed:
                        violations.append(next_path)
                    continue
                if _module_path(source_root, dependency) is not None:
                    pending.append((dependency, next_path, crossed))
    return tuple(sorted(set(violations)))


def test_model_visible_workspace_graph_cannot_reach_raw_overlay_unmediated() -> None:
    assert _unmediated_raw_engine_paths(_SOURCE_ROOT) == ()


def test_graph_guard_rejects_direct_and_function_local_indirect_raw_imports(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    deep = source / "agent_runtime/capabilities/workspace/deep_backend.py"
    helper = source / "agent_runtime/capabilities/workspace/helper.py"
    overlay = source / "agent_runtime/capabilities/workspace/overlay.py"
    run = source / "runtime_worker/handlers/run.py"
    for path in (deep, helper, overlay, run):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    deep.write_text(
        "from agent_runtime.capabilities.workspace.overlay import _WorkspaceOverlayMutationEngine\n",
        encoding="utf-8",
    )
    assert _unmediated_raw_engine_paths(source) == (
        (
            "agent_runtime.capabilities.workspace.deep_backend",
            "agent_runtime.capabilities.workspace.overlay",
        ),
    )

    deep.write_text(
        "from agent_runtime.capabilities.workspace import helper\n",
        encoding="utf-8",
    )
    helper.write_text(
        "def model_reachable_helper():\n"
        "    from agent_runtime.capabilities.workspace.overlay import _WorkspaceOverlayMutationEngine\n"
        "    return _WorkspaceOverlayMutationEngine\n",
        encoding="utf-8",
    )

    assert _unmediated_raw_engine_paths(source) == (
        (
            "agent_runtime.capabilities.workspace.deep_backend",
            "agent_runtime.capabilities.workspace.helper",
            "agent_runtime.capabilities.workspace.overlay",
        ),
    )


def test_raw_overlay_engine_is_not_part_of_the_public_or_model_read_api() -> None:
    assert workspace_exports == ("MergedWorkspaceBackend",)
    assert overlay_exports == ()
    for mutator in ("awrite", "aedit", "adelete", "amove", "amkdir"):
        assert not hasattr(MergedWorkspaceBackend, mutator)
