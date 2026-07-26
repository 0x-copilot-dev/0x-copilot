"""Source and startup guard for a transport-neutral operation adapter.

The decisive boundary is structural: ``OperationContext.require()`` returns an
execution-only object, while the gateway alone can reach a distinct presentation
context. This guard is defense in depth for Python's intentionally dynamic
module system. It rejects reflection and dynamic-import primitives outright and
uses a source-owned capability marker—not a growing import denylist—to prevent
an adapter from directly importing a presentation owner.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path


class PresentationBoundaryViolation(RuntimeError):
    """Raised when a provider adapter attempts to acquire presentation authority."""


_PRESENTATION_CAPABILITY_MARKER = "__operation_boundary__"
_PRESENTATION_CAPABILITY_VALUE = "presentation"
_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_DYNAMIC_MODULES = frozenset({"builtins", "importlib", "sys"})
_REFLECTION_PRIMITIVES = frozenset(
    {
        "__import__",
        "compile",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "locals",
        "vars",
    }
)


def assert_transport_adapter_presentation_boundary(path: Path) -> None:
    """Fail closed when a provider adapter can acquire presentation authority.

    Called before the MCP adapter receives registry/client authority. The cached
    source check guards normal execution; architecture tests exercise planted
    dynamic-import and reflection bypasses directly.
    """

    violations = _violations_for_path(path.resolve())
    if violations:
        raise PresentationBoundaryViolation(
            "transport adapter cannot acquire presentation authority: "
            + "; ".join(violations)
        )


@cache
def _violations_for_path(path: Path) -> tuple[str, ...]:
    return presentation_boundary_violations(path.read_text(encoding="utf-8"))


def presentation_boundary_violations(source: str) -> tuple[str, ...]:
    """Return structural and dynamic capability violations in adapter source."""

    visitor = _TransportAdapterBoundaryPolicy()
    visitor.visit(ast.parse(source))
    return tuple(visitor.violations)


class _TransportAdapterBoundaryPolicy(ast.NodeVisitor):
    """Reject presentation-owned imports and Python capability recovery tools."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self._module_aliases: dict[str, str] = {}
        self._restricted_aliases: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name
            local = alias.asname or module.split(".", maxsplit=1)[0]
            self._module_aliases[local] = module
            if _module_owns_presentation(module):
                self._add(f"presentation-owned import {module}")
            if _is_dynamic_module(module):
                self._restricted_aliases.add(local)
                self._add(f"dynamic module import {module}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            # Relative imports lack a stable target in a standalone source
            # audit and can conceal a parent-package presentation owner.
            self._add("relative import in transport adapter")
        if _module_owns_presentation(module):
            self._add(f"presentation-owned import {module}")
        if _is_dynamic_module(module):
            self._add(f"dynamic module import {module}")
        for alias in node.names:
            local = alias.asname or alias.name
            imported_module = f"{module}.{alias.name}" if module else alias.name
            self._module_aliases[local] = imported_module
            if _module_owns_presentation(imported_module):
                self._add(f"presentation-owned import {imported_module}")
            if alias.name.startswith("_"):
                self._add("private implementation import in transport adapter")
            if alias.name == "*":
                self._add("star import in transport adapter")
            if _is_dynamic_module(module) or alias.name in _REFLECTION_PRIMITIVES:
                self._restricted_aliases.add(local)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Name):
            imported_module = self._module_aliases.get(node.value.id)
            if imported_module is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._module_aliases[target.id] = imported_module
        if _restricted_name(node.value, self._restricted_aliases):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._restricted_aliases.add(target.id)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _restricted_name(node.value, self._restricted_aliases) or (
            isinstance(node.value, ast.Name) and node.value.id == "__builtins__"
        ):
            self._add("dynamic import or reflection primitive")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            node.value is not None
            and _restricted_name(node.value, self._restricted_aliases)
            and isinstance(node.target, ast.Name)
        ):
            self._restricted_aliases.add(node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._restricted_call(node.func):
            self._add("dynamic import or reflection primitive")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _is_restricted_attribute(node, self._module_aliases) or (
            _is_private_application_module_attribute(node, self._module_aliases)
        ):
            self._add("dynamic import or reflection primitive")
        self.generic_visit(node)

    def _restricted_call(self, func: ast.expr) -> bool:
        if _restricted_name(func, self._restricted_aliases):
            return True
        if not isinstance(func, ast.Attribute):
            return False
        return _is_restricted_attribute(func, self._module_aliases)

    def _add(self, violation: str) -> None:
        if violation not in self.violations:
            self.violations.append(violation)


def _restricted_name(node: ast.expr, aliases: set[str]) -> bool:
    return isinstance(node, ast.Name) and (
        node.id in aliases or node.id in _REFLECTION_PRIMITIVES
    )


def _attribute_root(node: ast.expr) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _is_restricted_attribute(
    node: ast.Attribute, module_aliases: dict[str, str]
) -> bool:
    """Whether a dynamic module/builtins object exposes a recovery primitive."""

    if node.attr not in _REFLECTION_PRIMITIVES | {"import_module"}:
        return False
    root = _attribute_root(node.value)
    return root == "__builtins__" or module_aliases.get(root, root) in _DYNAMIC_MODULES


def _is_private_application_module_attribute(
    node: ast.Attribute, module_aliases: dict[str, str]
) -> bool:
    """Reject reaching a private capability through an imported app module."""

    root = _attribute_root(node.value)
    module = module_aliases.get(root, "")
    return module.startswith("agent_runtime.") and node.attr.startswith("_")


def _is_dynamic_module(module: str) -> bool:
    return module in _DYNAMIC_MODULES or any(
        module.startswith(f"{root}.") for root in _DYNAMIC_MODULES
    )


@cache
def _module_owns_presentation(module: str) -> bool:
    """Read a module's declared ownership without importing it.

    Presentation packages opt in by declaring ``__operation_boundary__`` in
    their own source. This lets a new owner protect itself without editing an
    adapter-side import denylist.
    """

    if not module.startswith("agent_runtime."):
        return False
    module_path = _SOURCE_ROOT / Path(*module.split("."))
    candidates = (module_path.with_suffix(".py"), module_path / "__init__.py")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if not any(
                isinstance(target, ast.Name)
                and target.id == _PRESENTATION_CAPABILITY_MARKER
                for target in targets
            ):
                continue
            value = node.value
            return isinstance(value, ast.Constant) and (
                value.value == _PRESENTATION_CAPABILITY_VALUE
            )
    return False


__all__ = (
    "PresentationBoundaryViolation",
    "assert_transport_adapter_presentation_boundary",
    "presentation_boundary_violations",
)
