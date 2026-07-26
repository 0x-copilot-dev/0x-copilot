"""Executable inventory helpers for model-visible operation architecture tests."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

from agent_runtime.capabilities.operations.builtin_catalog import (
    BuiltinOperationCatalog,
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)


_CANONICAL_SURFACE_PATHS = frozenset(
    {
        Path("agent_runtime/capabilities/surfaces"),
        Path("agent_runtime/presentation"),
        Path("agent_runtime/surfaces_v2"),
    }
)
_BESPOKE_SURFACE_RESULT_KEYS = frozenset({"surface", "surface_uri", "surface_spec"})


def model_tool_descriptor_violations(
    tools: Sequence[object],
    *,
    catalog: BuiltinOperationCatalog = DEFAULT_BUILTIN_OPERATION_CATALOG,
    descriptors: OperationDescriptorRegistry = DEFAULT_OPERATION_DESCRIPTORS,
) -> tuple[str, ...]:
    """Return model tools lacking one reviewed catalog and descriptor identity."""

    violations: list[str] = []
    for tool in tools:
        name = str(getattr(tool, "name", "")).strip()
        if not name:
            violations.append("model-visible callable has no name")
            continue
        entry = catalog.resolve_model_tool_name(name)
        if entry is None or not entry.model_visible:
            violations.append(f"unregistered model-visible capability: {name}")
            continue
        if descriptors.resolve_entry(entry.capability, entry.op) is None:
            violations.append(
                "model-visible capability has no approved operation descriptor: "
                f"{name} -> {entry.key}"
            )
    return tuple(violations)


def direct_bespoke_surface_violations(source_root: Path) -> tuple[str, ...]:
    """Find direct presentation-result construction outside the shared paths.

    This intentionally examines Python syntax, rather than matching source text:
    model-capability code may only return an operation/artifact reference and
    must not construct a ``SurfaceEnvelope`` or a result mapping carrying a
    surface payload.  Shared presentation modules are the explicit producers.
    """

    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        if any(
            relative.is_relative_to(allowed) for allowed in _CANONICAL_SURFACE_PATHS
        ):
            continue
        visitor = _BespokeSurfaceVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        violations.extend(
            f"{relative.as_posix()}:{line}:{kind}" for line, kind in visitor.violations
        )
    return tuple(sorted(violations))


class _BespokeSurfaceVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._surface_envelope_modules: set[str] = set()
        self._surface_envelope_names = {"SurfaceEnvelope"}
        self.violations: list[tuple[int, str]] = []

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module and node.module.endswith("capabilities.surfaces.spec_models"):
            self._surface_envelope_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "SurfaceEnvelope"
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self._surface_envelope_modules.update(
            alias.asname or alias.name.rsplit(".", maxsplit=1)[-1]
            for alias in node.names
            if alias.name.endswith("capabilities.surfaces.spec_models")
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._is_surface_envelope_constructor(node.func):
            self.violations.append((node.lineno, "direct-surface-envelope"))
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        is_surface_envelope = isinstance(
            node.value, ast.Call
        ) and self._is_surface_envelope_constructor(node.value.func)
        if self._returns_surface_payload(node.value) and not is_surface_envelope:
            self.violations.append((node.lineno, "direct-surface-result"))
        self.generic_visit(node)

    def _is_surface_envelope_constructor(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Name):
            return func.id in self._surface_envelope_names
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "SurfaceEnvelope"
            and isinstance(func.value, ast.Name)
            and func.value.id in self._surface_envelope_modules
        )

    @staticmethod
    def _returns_surface_payload(value: ast.expr | None) -> bool:
        if isinstance(value, ast.Dict):
            return any(
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value in _BESPOKE_SURFACE_RESULT_KEYS
                for key in value.keys
            )
        return isinstance(value, ast.Call) and any(
            keyword.arg in _BESPOKE_SURFACE_RESULT_KEYS
            for keyword in value.keywords
            if keyword.arg is not None
        )
