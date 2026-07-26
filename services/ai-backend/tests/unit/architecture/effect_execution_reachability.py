"""Static architecture gate for the sole A5 execution boundary.

This intentionally lives in ``tests`` rather than production because it is a
repository-structure assertion.  The graph follows *referenced imported
symbols*, rather than treating every import in a module as reachable.  That is
important for the worker's material-store module: it exposes harmless stores
and the privileged coordinator factory from the same file; importing a store
must not be mistaken for constructing the factory.

The gate is deliberately conservative about direct dispatch calls and narrow
about its explicit exceptions.  A source may reach a protected execution
surface only through one of the checked-in composition paths below.  Dynamic
imports, reflection, and runtime dependency injection are outside an AST
gate's proof and remain forbidden by the surrounding architecture tests and
code review.
"""

from __future__ import annotations

import ast
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


_MODULE_NODE = "<module>"
_EXECUTION_METHODS = frozenset({"apply", "prepare", "reconcile", "resolve"})


@dataclass(frozen=True)
class SymbolNode:
    """A module-level class, function, or synthetic module body."""

    module: str
    symbol: str

    @property
    def key(self) -> str:
        return f"{self.module}:{self.symbol}"


@dataclass(frozen=True)
class ImportBinding:
    """The internal target bound to one import alias."""

    module: str
    symbol: str | None


@dataclass(frozen=True)
class ApprovedExecutionPath:
    """One intentionally privileged composition route, with its reason."""

    nodes: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class HandlerDelegation:
    """One worker handler may invoke this narrow coordinator protocol method."""

    module: str
    class_name: str
    method_name: str
    coordinator_method: str
    reason: str


# Model-visible tools/adapters are entries into the LLM-facing graph.  The
# browser effect adapter is intentionally absent: it is a protected executor,
# not a model-facing operation adapter.
MODEL_VISIBLE_PREFIXES = (
    "agent_runtime.capabilities.browser.operation_adapter",
    "agent_runtime.capabilities.interpreter",
    "agent_runtime.capabilities.mcp.operation_adapter",
    "agent_runtime.capabilities.operations",
    "agent_runtime.capabilities.sandbox.execute_tool",
    "agent_runtime.capabilities.tools",
    "agent_runtime.capabilities.workspace.deep_backend",
    "agent_runtime.capabilities.workspace.effects",
    "agent_runtime.capabilities.workspace.merged_backend",
)

# Staging is deliberately broader than EffectStager alone.  New staging
# helpers under ``effects`` cannot acquire an executor by hiding in a sibling
# module.  The protected execution implementation is excluded below.
EFFECT_STAGING_PREFIX = "agent_runtime.effects"
EFFECT_STAGING_EXCLUDED_MODULES = frozenset(
    {
        "agent_runtime.effects.claims",
        "agent_runtime.effects.coordinator",
        "agent_runtime.effects.executor",
        "agent_runtime.effects.executor_registry",
    }
)

# The worker owns execution, but only the coordinator factory and the two thin
# command handlers may cross this boundary.  Concrete executors are protected
# sinks rather than sources.
WORKER_PREFIX = "runtime_worker"
WORKER_EXECUTOR_MODULES = frozenset(
    {
        "runtime_worker.builtin_effect_executor",
        "runtime_worker.legacy_mcp_effect_executor",
        "runtime_worker.mcp_effect_executor",
        "runtime_worker.workspace_effect_storage",
    }
)

PROTECTED_EXECUTION_NODES = frozenset(
    {
        "agent_runtime.capabilities.browser.effect_adapter:BrowserEffectExecutor",
        "agent_runtime.capabilities.desktop.workspace_authority:WorkspaceEffectExecutor",
        "agent_runtime.effects.coordinator:EffectCoordinator",
        "agent_runtime.effects.executor_registry:EffectExecutorRegistry",
        "runtime_worker.builtin_effect_executor:BuiltinRowSetEffectExecutor",
        "runtime_worker.mcp_effect_executor:McpEffectExecutor",
        "runtime_worker.workspace_effect_storage:workspace_executor",
    }
)

# There are no broad prefix exemptions.  Each path names every privileged hop
# that worker composition is permitted to take.  If a source gains a second
# path to a protected node, the graph reports it even when an allowed path also
# exists.
APPROVED_EXECUTION_PATHS = (
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.effects.coordinator:EffectCoordinator",
        ),
        reason="The worker-only factory constructs the claim-before-effect coordinator.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.effects.executor_registry:EffectExecutorRegistry",
        ),
        reason="The same worker-only factory closes the executor registry per verified run.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.mcp_effect_executor:McpEffectExecutor",
        ),
        reason="The factory may construct the MCP executor only for the closed registry.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.builtin_effect_executor:BuiltinRowSetEffectExecutor",
        ),
        reason="The factory may construct the typed builtin executor only for the closed registry.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.workspace_effect_storage:workspace_executor",
        ),
        reason="The factory may add the host-approved workspace executor to the closed registry.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.capabilities.browser.effect_adapter:BrowserEffectExecutor",
        ),
        reason="The factory may add the approved browser bridge executor to the closed registry.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.effects.coordinator:EffectCoordinator",
        ),
        reason="The worker composition root may install the closed coordinator factory.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.effects.executor_registry:EffectExecutorRegistry",
        ),
        reason="The worker composition root reaches the registry only through its factory.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.mcp_effect_executor:McpEffectExecutor",
        ),
        reason="The worker composition root reaches the MCP executor only through its factory.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.builtin_effect_executor:BuiltinRowSetEffectExecutor",
        ),
        reason="The worker composition root reaches the builtin executor only through its factory.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.workspace_effect_storage:workspace_executor",
        ),
        reason="The worker composition root reaches workspace execution only through its factory.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.capabilities.browser.effect_adapter:BrowserEffectExecutor",
        ),
        reason="The worker composition root reaches browser execution only through its factory.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.__main__:RuntimeWorkerEntrypoint",
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.effects.coordinator:EffectCoordinator",
        ),
        reason="The process entrypoint reaches the coordinator only through the worker composition root.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.__main__:RuntimeWorkerEntrypoint",
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.effects.executor_registry:EffectExecutorRegistry",
        ),
        reason="The process entrypoint reaches the registry only through the worker composition root.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.__main__:RuntimeWorkerEntrypoint",
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.mcp_effect_executor:McpEffectExecutor",
        ),
        reason="The process entrypoint reaches the MCP executor only through the worker composition root.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.__main__:RuntimeWorkerEntrypoint",
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.builtin_effect_executor:BuiltinRowSetEffectExecutor",
        ),
        reason="The process entrypoint reaches the builtin executor only through the worker composition root.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.__main__:RuntimeWorkerEntrypoint",
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "runtime_worker.workspace_effect_storage:workspace_executor",
        ),
        reason="The process entrypoint reaches workspace execution only through the worker composition root.",
    ),
    ApprovedExecutionPath(
        nodes=(
            "runtime_worker.__main__:RuntimeWorkerEntrypoint",
            "runtime_worker.loop:RuntimeWorker",
            "runtime_worker.mcp_operation_storage:RuntimeMcpEffectCoordinatorFactory",
            "agent_runtime.capabilities.browser.effect_adapter:BrowserEffectExecutor",
        ),
        reason="The process entrypoint reaches browser execution only through the worker composition root.",
    ),
)

# These are semantic call paths: the handlers receive a protocol and never
# import a concrete registry or executor.  The explicit checks below make that
# indirection auditable instead of creating a broad worker exemption.
APPROVED_HANDLER_DELEGATIONS = (
    HandlerDelegation(
        module="runtime_worker.handlers.effect_commit",
        class_name="RuntimeEffectCommitHandler",
        method_name="handle",
        coordinator_method="handle",
        reason="The verified commit handler may delegate one body-free command to its coordinator protocol.",
    ),
    HandlerDelegation(
        module="runtime_worker.handlers.effect_reconcile",
        class_name="RuntimeEffectReconcileHandler",
        method_name="handle",
        coordinator_method="reconcile",
        reason="The verified recovery handler may delegate one claim-only command to its coordinator protocol.",
    ),
)


class EffectExecutionReachabilityGate:
    """AST-only, symbol-level graph of internal Python dependencies."""

    def __init__(self, source_root: Path) -> None:
        self._source_root = source_root
        self._module_paths = _module_paths(source_root)
        self._known_nodes = _known_node_keys(self._module_paths)
        self._imports = {
            module: _import_bindings(
                path.read_text(encoding="utf-8"),
                module=module,
                module_paths=self._module_paths,
            )
            for module, path in self._module_paths.items()
        }
        self._definitions = {
            module: _definition_nodes(
                path.read_text(encoding="utf-8"),
                module=module,
                bindings=self._imports[module],
                module_paths=self._module_paths,
                known_nodes=self._known_nodes,
            )
            for module, path in self._module_paths.items()
        }
        self._edges = _symbol_edges(self._definitions)

    def violations(self) -> tuple[str, ...]:
        """Return deterministic unauthorised source-to-execution paths."""

        allowed = {path.nodes for path in APPROVED_EXECUTION_PATHS}
        violations: set[str] = set()
        for start in self._guarded_source_nodes():
            path = _first_unapproved_path(
                start=start,
                edges=self._edges,
                protected=PROTECTED_EXECUTION_NODES,
                allowed=allowed,
            )
            if path is not None:
                violations.add(" -> ".join(path))
        return tuple(sorted(violations))

    def direct_dispatch_violations(self) -> tuple[str, ...]:
        """Return guarded definitions that instantiate then dispatch directly."""

        violations: list[str] = []
        for key in self._guarded_source_nodes():
            definition = self._definitions_for_key().get(key)
            if definition is None:
                continue
            for line in definition.direct_dispatch_lines:
                violations.append(f"{key}:{line}")
        return tuple(sorted(violations))

    def allowlist_violations(self) -> tuple[str, ...]:
        """Fail closed if a documented exception no longer names a real edge."""

        violations: list[str] = []
        for approved in APPROVED_EXECUTION_PATHS:
            if not approved.reason:
                violations.append("missing reason: " + " -> ".join(approved.nodes))
            for left, right in zip(approved.nodes, approved.nodes[1:]):
                if right not in self._edges.get(left, frozenset()):
                    violations.append("missing edge: " + " -> ".join(approved.nodes))
                    break
        return tuple(sorted(violations))

    def handler_delegation_violations(self) -> tuple[str, ...]:
        """Check the two protocol-based handler exceptions remain narrow."""

        violations: list[str] = []
        protected_symbols = {
            node.rsplit(":", maxsplit=1)[0]: node.rsplit(":", maxsplit=1)[1]
            for node in PROTECTED_EXECUTION_NODES
        }
        for approved in APPROVED_HANDLER_DELEGATIONS:
            path = self._module_paths.get(approved.module)
            if path is None:
                violations.append(f"missing handler module: {approved.module}")
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _handler_imports_protected_surface(tree, protected_symbols):
                violations.append(f"direct protected import: {approved.module}")
                continue
            handler = _top_level_definition(tree, approved.class_name)
            if not isinstance(handler, ast.ClassDef):
                violations.append(
                    f"missing handler class: {approved.module}:{approved.class_name}"
                )
                continue
            method = _class_method(handler, approved.method_name)
            if method is None or not _delegates_only_to_coordinator(
                method, approved.coordinator_method
            ):
                violations.append(
                    "unexpected handler delegation: "
                    f"{approved.module}:{approved.class_name}.{approved.method_name}"
                )
            if not approved.reason:
                violations.append(
                    "missing handler reason: "
                    f"{approved.module}:{approved.class_name}.{approved.method_name}"
                )
        return tuple(sorted(violations))

    def _guarded_source_nodes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                key
                for key, definition in self._definitions_for_key().items()
                if definition.node.symbol != _MODULE_NODE
                and _is_guarded_source_module(definition.node.module)
            )
        )

    def _definitions_for_key(self) -> Mapping[str, _DefinitionNode]:
        return {
            definition.node.key: definition
            for definitions in self._definitions.values()
            for definition in definitions
        }


@dataclass(frozen=True)
class _DefinitionNode:
    node: SymbolNode
    dependencies: frozenset[str]
    direct_dispatch_lines: tuple[int, ...]


def _module_paths(source_root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        parts = relative.with_suffix("").parts
        module_parts = parts[:-1] if parts[-1] == "__init__" else parts
        if module_parts:
            modules[".".join(module_parts)] = path
    return modules


def _definition_nodes(
    source: str,
    *,
    module: str,
    bindings: Mapping[str, ImportBinding],
    module_paths: Mapping[str, Path],
    known_nodes: frozenset[str],
) -> tuple[_DefinitionNode, ...]:
    tree = ast.parse(source, filename=module)
    definitions: list[_DefinitionNode] = []
    module_body = [
        node
        for node in tree.body
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    definitions.append(
        _definition_from_nodes(
            module=module,
            symbol=_MODULE_NODE,
            body=module_body,
            bindings=bindings,
            module_paths=module_paths,
            known_nodes=known_nodes,
        )
    )
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.append(
                _definition_from_nodes(
                    module=module,
                    symbol=node.name,
                    body=[node],
                    bindings=bindings,
                    module_paths=module_paths,
                    known_nodes=known_nodes,
                )
            )
    return tuple(definitions)


def _definition_from_nodes(
    *,
    module: str,
    symbol: str,
    body: Iterable[ast.AST],
    bindings: Mapping[str, ImportBinding],
    module_paths: Mapping[str, Path],
    known_nodes: frozenset[str],
) -> _DefinitionNode:
    visitor = _ReferencedImportVisitor(
        bindings=bindings,
        module_paths=module_paths,
        known_nodes=known_nodes,
    )
    direct_dispatch = _DirectDispatchVisitor(
        bindings=bindings,
        module_paths=module_paths,
        known_nodes=known_nodes,
    )
    for item in body:
        visitor.visit(item)
        direct_dispatch.visit(item)
    return _DefinitionNode(
        node=SymbolNode(module=module, symbol=symbol),
        dependencies=frozenset(visitor.dependencies),
        direct_dispatch_lines=tuple(sorted(direct_dispatch.lines)),
    )


def _import_bindings(
    source: str,
    *,
    module: str,
    module_paths: Mapping[str, Path],
) -> dict[str, ImportBinding]:
    tree = ast.parse(source, filename=module)
    bindings: dict[str, ImportBinding] = {}
    # Function-local imports are executable imports too.  Binding them here is
    # deliberately an over-approximation across a module, which is safer than
    # allowing a model-facing tool to hide an executor import inside a method.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                target_module = alias.name
                if target_module in module_paths:
                    bindings[bound_name] = ImportBinding(target_module, None)
        elif isinstance(node, ast.ImportFrom):
            base_module = _resolve_from_module(module, node.level, node.module)
            if base_module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound_name = alias.asname or alias.name
                child_module = f"{base_module}.{alias.name}"
                if child_module in module_paths:
                    bindings[bound_name] = ImportBinding(child_module, None)
                elif base_module in module_paths:
                    bindings[bound_name] = ImportBinding(base_module, alias.name)
    return bindings


def _resolve_from_module(module: str, level: int, imported: str | None) -> str | None:
    if level == 0:
        return imported
    package = module.split(".")[:-1]
    if level > len(package) + 1:
        return None
    base = package[: len(package) - level + 1]
    if imported:
        base.extend(imported.split("."))
    return ".".join(base) or None


class _ReferencedImportVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        bindings: Mapping[str, ImportBinding],
        module_paths: Mapping[str, Path],
        known_nodes: frozenset[str],
    ) -> None:
        self._bindings = bindings
        self._module_paths = module_paths
        self._known_nodes = known_nodes
        self.dependencies: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            binding = self._bindings.get(node.id)
            if binding is not None:
                self.dependencies.add(
                    _binding_key(binding, self._module_paths, self._known_nodes)
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if isinstance(node.value, ast.Name):
            binding = self._bindings.get(node.value.id)
            if binding is not None and binding.symbol is None:
                candidate = SymbolNode(binding.module, node.attr).key
                if candidate in self._known_nodes:
                    self.dependencies.add(candidate)
                    return
        self.generic_visit(node)


class _DirectDispatchVisitor(ast.NodeVisitor):
    """Detect a locally constructed protected executor receiving a dispatch call."""

    def __init__(
        self,
        *,
        bindings: Mapping[str, ImportBinding],
        module_paths: Mapping[str, Path],
        known_nodes: frozenset[str],
    ) -> None:
        self._bindings = bindings
        self._module_paths = module_paths
        self._known_nodes = known_nodes
        self._protected_locals: set[str] = set()
        self.lines: set[int] = set()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self._record_protected_assignment(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self._record_protected_assignment([node.target], node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self._protected_locals
            and node.func.attr in _EXECUTION_METHODS
        ):
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def _record_protected_assignment(
        self, targets: Iterable[ast.expr], value: ast.expr
    ) -> None:
        if not isinstance(value, ast.Call):
            return
        target = _called_binding_key(
            value.func,
            self._bindings,
            self._module_paths,
            self._known_nodes,
        )
        if target not in PROTECTED_EXECUTION_NODES:
            return
        for assignment in targets:
            if isinstance(assignment, ast.Name):
                self._protected_locals.add(assignment.id)


def _called_binding_key(
    node: ast.expr,
    bindings: Mapping[str, ImportBinding],
    module_paths: Mapping[str, Path],
    known_nodes: frozenset[str],
) -> str | None:
    if isinstance(node, ast.Name) and (binding := bindings.get(node.id)) is not None:
        return _binding_key(binding, module_paths, known_nodes)
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and (binding := bindings.get(node.value.id)) is not None
        and binding.symbol is None
    ):
        candidate = SymbolNode(binding.module, node.attr).key
        if candidate in known_nodes:
            return candidate
    return None


def _binding_key(
    binding: ImportBinding,
    module_paths: Mapping[str, Path],
    known_nodes: frozenset[str],
) -> str:
    if binding.symbol is not None:
        candidate = SymbolNode(binding.module, binding.symbol).key
        if candidate in known_nodes:
            return candidate
    return SymbolNode(binding.module, _MODULE_NODE).key


def _known_node_keys(module_paths: Mapping[str, Path]) -> frozenset[str]:
    # The service has a modest module count and parsing is performed once per
    # gate, so this small cached-by-call helper keeps the implementation simple.
    keys: set[str] = set()
    for module, path in module_paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        keys.add(SymbolNode(module, _MODULE_NODE).key)
        keys.update(
            SymbolNode(module, node.name).key
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )
    return frozenset(keys)


def _symbol_edges(
    definitions: Mapping[str, tuple[_DefinitionNode, ...]],
) -> dict[str, frozenset[str]]:
    return {
        definition.node.key: definition.dependencies
        for module_definitions in definitions.values()
        for definition in module_definitions
    }


def _first_unapproved_path(
    *,
    start: str,
    edges: Mapping[str, frozenset[str]],
    protected: frozenset[str],
    allowed: set[tuple[str, ...]],
) -> tuple[str, ...] | None:
    """Find a shortest path that is not exactly a documented exception.

    Search state retains which allowlist paths still match the current prefix.
    That prevents an allowed shortest route from masking a longer rogue route.
    """

    candidates = tuple(path for path in allowed if path[0] == start)
    queue: deque[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...]]] = deque(
        [(start, (start,), candidates)]
    )
    seen: set[tuple[str, tuple[tuple[str, ...], ...]]] = {(start, candidates)}
    while queue:
        node, path, matching = queue.popleft()
        if node in protected:
            if path not in allowed:
                return path
            continue
        for next_node in sorted(edges.get(node, frozenset())):
            next_path = (*path, next_node)
            next_matching = tuple(
                candidate
                for candidate in matching
                if len(candidate) >= len(next_path)
                and candidate[: len(next_path)] == next_path
            )
            state = (next_node, next_matching)
            if state not in seen:
                seen.add(state)
                queue.append((next_node, next_path, next_matching))
    return None


def _is_guarded_source_module(module: str) -> bool:
    if module in WORKER_EXECUTOR_MODULES:
        return False
    if module == EFFECT_STAGING_PREFIX or module.startswith(
        EFFECT_STAGING_PREFIX + "."
    ):
        return module not in EFFECT_STAGING_EXCLUDED_MODULES
    if module == WORKER_PREFIX or module.startswith(WORKER_PREFIX + "."):
        return True
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in MODEL_VISIBLE_PREFIXES
    )


def _top_level_definition(tree: ast.Module, name: str) -> ast.AST | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def _class_method(
    class_node: ast.ClassDef, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    return next(
        (
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ),
        None,
    )


def _handler_imports_protected_surface(
    tree: ast.Module, protected_symbols: Mapping[str, str]
) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(alias.name in protected_symbols for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module in protected_symbols:
            protected_symbol = protected_symbols[node.module]
            if any(alias.name in {"*", protected_symbol} for alias in node.names):
                return True
    return False


def _delegates_only_to_coordinator(
    method: ast.FunctionDef | ast.AsyncFunctionDef, expected_method: str
) -> bool:
    """Require exactly one ``coordinator.<expected_method>(...)`` call.

    The local is intentionally named ``coordinator`` in both approved handlers;
    keeping that name stable makes the capability boundary obvious in review.
    """

    calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "coordinator"
    ]
    return len(calls) == 1 and calls[0].func.attr == expected_method


__all__ = (
    "APPROVED_EXECUTION_PATHS",
    "APPROVED_HANDLER_DELEGATIONS",
    "EffectExecutionReachabilityGate",
    "PROTECTED_EXECUTION_NODES",
)
