"""Repository guards for C3's sole workspace-effect construction boundary."""

from __future__ import annotations

import ast
from pathlib import Path


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"
_CANONICAL_CONSTRUCTOR = Path("runtime_worker/workspace_effect_storage.py")


def _constructor_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkspaceEffectExecutor"
    )


def _rogue_constructors(source_root: Path) -> list[str]:
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        if relative == _CANONICAL_CONSTRUCTOR:
            continue
        violations.extend(
            f"{relative.as_posix()}:{line}"
            for line in _constructor_calls(path.read_text(encoding="utf-8"))
        )
    return sorted(violations)


def test_workspace_executor_is_constructed_only_by_worker_registry_adapter() -> None:
    assert _constructor_calls(
        (_SOURCE_ROOT / _CANONICAL_CONSTRUCTOR).read_text(encoding="utf-8")
    )
    assert _rogue_constructors(_SOURCE_ROOT) == []


def test_guard_rejects_a_planted_direct_workspace_executor(tmp_path: Path) -> None:
    rogue = tmp_path / "runtime_api" / "workspace_apply.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "executor = WorkspaceEffectExecutor(scope=scope)\n",
        encoding="utf-8",
    )

    assert _rogue_constructors(tmp_path) == ["runtime_api/workspace_apply.py:1"]
