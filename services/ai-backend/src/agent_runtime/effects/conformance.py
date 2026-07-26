"""Read-only architecture probes shared by effect release gates.

The probes deliberately inspect Python ASTs rather than importing application
composition.  They are safe to run in CI/release tooling and keep the two
effect-boundary assertions in one place: a terminal receipt has one producer,
and a workspace executor has one construction boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path


CANONICAL_EFFECT_RESULT_PRODUCER = Path("agent_runtime/effects/coordinator.py")
CANONICAL_WORKSPACE_EXECUTOR_CONSTRUCTOR = Path(
    "runtime_worker/workspace_effect_storage.py"
)
# Keep the detector's own source from becoming an inline-event producer.  The
# AST guard deliberately sees direct string literals, so build its comparison
# token at runtime.
_EFFECT_APPLIED_LITERAL = ".".join(("effect", "applied"))


def effect_applied_producer_violations(source_root: Path) -> tuple[str, ...]:
    """Return non-canonical terminal-effect producer sites."""

    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        if relative == CANONICAL_EFFECT_RESULT_PRODUCER:
            continue
        for line in _effect_applied_markers(path.read_text(encoding="utf-8")):
            violations.append(f"{relative.as_posix()}:{line}")
    return tuple(sorted(violations))


def canonical_effect_result_producer_present(source_root: Path) -> bool:
    """Return whether the canonical coordinator still constructs a receipt."""

    path = source_root / CANONICAL_EFFECT_RESULT_PRODUCER
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    has_recorder = any(
        isinstance(node, ast.ClassDef) and node.name == "EffectResultRecorder"
        for node in ast.walk(tree)
    )
    has_terminal_constant = any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_EVENT_APPLIED"
            for target in node.targets
        )
        and any(_is_effect_applied_enum_expression(value) for value in (node.value,))
        for node in ast.walk(tree)
    )
    return has_recorder and has_terminal_constant


def workspace_executor_constructor_violations(source_root: Path) -> tuple[str, ...]:
    """Return workspace-executor construction outside the worker adapter."""

    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        if relative == CANONICAL_WORKSPACE_EXECUTOR_CONSTRUCTOR:
            continue
        for line in _workspace_executor_constructor_calls(
            path.read_text(encoding="utf-8")
        ):
            violations.append(f"{relative.as_posix()}:{line}")
    return tuple(sorted(violations))


def canonical_workspace_executor_constructor_present(source_root: Path) -> bool:
    """Return whether the closed worker adapter owns the constructor."""

    path = source_root / CANONICAL_WORKSPACE_EXECUTOR_CONSTRUCTOR
    return path.is_file() and bool(
        _workspace_executor_constructor_calls(path.read_text(encoding="utf-8"))
    )


def _effect_applied_markers(source: str) -> tuple[int, ...]:
    visitor = _EffectAppliedMarkerVisitor()
    visitor.visit(ast.parse(source))
    return tuple(sorted(visitor.lines))


def _workspace_executor_constructor_calls(source: str) -> tuple[int, ...]:
    tree = ast.parse(source)
    return tuple(
        sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "WorkspaceEffectExecutor"
        )
    )


def _is_effect_applied_enum_expression(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "value"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "EFFECT_APPLIED"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "LedgerEventType"
    )


class _EffectAppliedMarkerVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._class_stack: list[str] = []
        self.lines: set[int] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if (
            node.value == _EFFECT_APPLIED_LITERAL
            and "LedgerEventType" not in self._class_stack
        ):
            self.lines.add(node.lineno)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "RuntimeApiEventType"
            and any(_is_effect_applied_enum_expression(arg) for arg in node.args)
        ):
            self.lines.add(node.lineno)
        self.generic_visit(node)


__all__ = (
    "CANONICAL_EFFECT_RESULT_PRODUCER",
    "CANONICAL_WORKSPACE_EXECUTOR_CONSTRUCTOR",
    "canonical_effect_result_producer_present",
    "canonical_workspace_executor_constructor_present",
    "effect_applied_producer_violations",
    "workspace_executor_constructor_violations",
)
