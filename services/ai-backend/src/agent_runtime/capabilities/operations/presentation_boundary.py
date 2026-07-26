"""Enforce that transport adapters cannot own operation presentation.

Python does not offer package-private imports.  The boundary is therefore
enforced twice: adapters receive only the transport-neutral outcome contract at
runtime, and this source policy rejects every Python import form that could
recover the ledger/surface implementation from a provider adapter.  The latter
is deliberately a runtime startup check as well as an architecture test, so a
local import added after review fails before the adapter can dispatch a tool.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path


class PresentationBoundaryViolation(RuntimeError):
    """Raised when a provider adapter attempts to own presentation internals."""


_PRESENTATION_MODULE_ROOTS = frozenset(
    {
        "agent_runtime.surfaces_v2.emitter",
        "agent_runtime.capabilities.surfaces.generator",
        "agent_runtime.capabilities.surfaces.projector",
    }
)
_PRESENTATION_SYMBOLS = frozenset(
    {
        "WorkLedgerEmitter",
        "SurfaceGenerationScheduler",
        "SurfaceProjector",
    }
)
_DYNAMIC_IMPORT_ROOT = "importlib"


def assert_transport_adapter_presentation_boundary(path: Path) -> None:
    """Fail closed when ``path`` can import a presentation implementation.

    This is intentionally invoked by the adapter's constructor.  It protects
    normal Python execution (including function-local and dynamic imports),
    while the architecture test exercises the scanner directly with planted
    bypasses.  The cache keeps the guard effectively free after the first
    trusted construction for a source file.
    """

    violations = _violations_for_path(path.resolve())
    if violations:
        rendered = "; ".join(violations)
        raise PresentationBoundaryViolation(
            f"transport adapter cannot own operation presentation: {rendered}"
        )


@cache
def _violations_for_path(path: Path) -> tuple[str, ...]:
    return presentation_boundary_violations(path.read_text(encoding="utf-8"))


def presentation_boundary_violations(source: str) -> tuple[str, ...]:
    """Return policy violations for provider-adapter source.

    The visitor reasons about Python syntax rather than matching a list of
    source strings.  In particular, imports at every lexical depth are visible
    in the AST, aliases resolve to their imported module, and any dynamic import
    primitive is prohibited in an adapter.  Prohibiting the primitive—not just
    a spelling of a forbidden target—closes computed-string and alias bypasses.
    """

    tree = ast.parse(source)
    visitor = _PresentationImportPolicy()
    visitor.visit(tree)
    return tuple(visitor.violations)


class _PresentationImportPolicy(ast.NodeVisitor):
    """AST policy for a transport-neutral provider adapter."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self._dynamic_import_aliases: set[str] = set()
        self._module_aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name
            local = alias.asname or module.split(".", maxsplit=1)[0]
            self._module_aliases[local] = module
            if _is_presentation_module(module):
                self._add(f"import of presentation module {module}")
            if module == _DYNAMIC_IMPORT_ROOT or module.startswith(
                f"{_DYNAMIC_IMPORT_ROOT}."
            ):
                self._add("dynamic importer importlib")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            # This adapter is deliberately absolute-import only.  A relative
            # import has no standalone semantic target in a source scan and is
            # otherwise a way to hide a parent-package presentation import.
            self._add("relative import in transport adapter")
        if _is_presentation_module(module):
            self._add(f"import from presentation module {module}")
        for alias in node.names:
            imported_module = f"{module}.{alias.name}" if module else alias.name
            if _is_presentation_module(imported_module):
                self._add(f"import of presentation module {imported_module}")
            if alias.name in _PRESENTATION_SYMBOLS:
                self._add(f"import of presentation symbol {alias.name}")
            if module == _DYNAMIC_IMPORT_ROOT or module.startswith(
                f"{_DYNAMIC_IMPORT_ROOT}."
            ):
                self._add("dynamic importer importlib")
                self._dynamic_import_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_dynamic_import(node.func):
            self._add("dynamic import primitive")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # ``builtins.__import__`` and an alias to it are both dynamic import
        # primitives.  Attribute use of a presentation symbol is also a useful
        # defense in depth when an object was supplied through an unrelated
        # dependency rather than imported locally.
        if node.attr == "__import__":
            self._add("dynamic import primitive")
        if node.attr in _PRESENTATION_SYMBOLS or node.attr == "on_tool_result":
            self._add(f"presentation attribute {node.attr}")
        self.generic_visit(node)

    def _is_dynamic_import(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Name):
            return func.id == "__import__" or func.id in self._dynamic_import_aliases
        if isinstance(func, ast.Attribute):
            if func.attr not in {"import_module", "__import__"}:
                return False
            root = _attribute_root(func.value)
            resolved = self._module_aliases.get(root, root)
            return resolved == _DYNAMIC_IMPORT_ROOT or resolved.startswith(
                f"{_DYNAMIC_IMPORT_ROOT}."
            )
        return False

    def _add(self, violation: str) -> None:
        if violation not in self.violations:
            self.violations.append(violation)


def _attribute_root(node: ast.expr) -> str:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _is_presentation_module(module: str) -> bool:
    return any(
        module == root or module.startswith(f"{root}.")
        for root in _PRESENTATION_MODULE_ROOTS
    )


__all__ = (
    "PresentationBoundaryViolation",
    "assert_transport_adapter_presentation_boundary",
    "presentation_boundary_violations",
)
