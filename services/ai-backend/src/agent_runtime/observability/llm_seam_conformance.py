"""Read-only AST proof for the canonical model and agent-graph funnels."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final


CANONICAL_MODEL_FUNNEL = Path("agent_runtime/execution/deep_agent_builder.py")
CANONICAL_RUNTIME_FACTORY = Path("agent_runtime/execution/factory.py")
_INIT_FUNCTIONS = frozenset({"init_chat_model", "init_embeddings"})
_CANONICAL_CHAT_BUILDERS = frozenset({"build_chat_model", "build_chat_model_from_id"})
_AGENT_GRAPH_BUILDERS = frozenset({"create_agent", "create_deep_agent"})
_BANNED_MODULES = frozenset(
    {
        "langchain_openai",
        "langchain_anthropic",
        "langchain_google_genai",
        "anthropic",
        "openai",
    }
)


def canonical_chat_model_call_sites(source_root: Path) -> tuple[str, ...]:
    """Inventory reviewed consumers of the canonical chat-model builders.

    Entries use lexical scope names rather than source line numbers so harmless
    formatting changes do not churn the Step 0 inventory. A newly added
    consumer still changes the inventory and therefore requires an explicit
    conformance review.
    """

    call_sites: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callable_name = _callable_name(node.func)
            if callable_name not in _CANONICAL_CHAT_BUILDERS:
                continue
            scope = _lexical_scope(node, parents)
            call_sites.append(f"{relative}:{scope} -> {callable_name}")
    return tuple(sorted(call_sites))


def llm_seam_violations(source_root: Path) -> tuple[str, ...]:
    """Return construction/import paths that bypass reviewed runtime funnels."""

    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if relative != CANONICAL_MODEL_FUNNEL.as_posix():
            violations.extend(_init_reference_violations(tree, relative))
            violations.extend(
                _call_reference_violations(
                    tree,
                    relative,
                    forbidden=_AGENT_GRAPH_BUILDERS,
                    seam="canonical Deep Agents builder",
                )
            )
        if relative != CANONICAL_RUNTIME_FACTORY.as_posix():
            violations.extend(
                _call_reference_violations(
                    tree,
                    relative,
                    forbidden=frozenset({"build_deep_agent"}),
                    seam="canonical runtime factory",
                )
            )
        violations.extend(_provider_import_violations(tree, relative))
    return tuple(sorted(violations))


def canonical_model_funnel_present(source_root: Path) -> bool:
    """Return whether the declared funnel uses both guarded constructors."""

    path = source_root / CANONICAL_MODEL_FUNNEL
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id in _INIT_FUNCTIONS
    }
    return names == _INIT_FUNCTIONS


class _ReviewedMiddleware:
    """The middleware sequence the runtime factory is allowed to compose.

    The gate names the members explicitly, in order, rather than counting them:
    a graph-wide control that is silently dropped is a policy and
    result-admission bypass, and one that is silently ADDED is an unreviewed
    interception point on every tool call.

    ``HostPathToolMiddleware`` is conditional — it is installed only when the
    desktop host filesystem rules are, so it appears in the source as a starred
    call to the factory helper that returns it or an empty tuple. That helper's
    NAME is what is pinned here, so the condition stays visible in this gate
    instead of hiding behind a runtime branch.
    """

    #: Unconditional root middleware, in composition order.
    ROOT: Final[tuple[str, ...]] = (
        "RuntimeControlMiddleware",
        "ModelInvocationMiddleware",
        # Ours to declare: 0.7.1 ships `TodoListMiddleware` only via the
        # `_openai_codex` harness profile, which matches none of our models.
        "TodoListMiddleware",
    )
    #: Unconditional child factories, in composition order.
    CHILD: Final[tuple[str, ...]] = ROOT
    #: Factory helpers whose starred result may follow the unconditional members.
    ROOT_OPTIONAL: Final[tuple[str, ...]] = ("_host_path_tool_middleware",)
    CHILD_OPTIONAL: Final[tuple[str, ...]] = ("_host_path_tool_middleware_factories",)


def _sequence_matches(
    elements: list[ast.expr],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    constructed: bool,
) -> bool:
    """Whether ``elements`` is ``required`` followed by a prefix of ``optional``.

    ``constructed`` selects how a required member is spelled: the root sequence
    instantiates (``RuntimeControlMiddleware()``) while the child sequence names
    the class itself. An optional member is always a starred call to the named
    factory helper, so an unreviewed value cannot ride in behind one.
    """

    if len(elements) < len(required) or len(elements) > len(required) + len(optional):
        return False
    for element, name in zip(elements[: len(required)], required, strict=True):
        if constructed:
            if (
                not isinstance(element, ast.Call)
                or _callable_name(element.func) != name
            ):
                return False
        elif _callable_name(element) != name:
            return False
    for element, name in zip(elements[len(required) :], optional, strict=False):
        if not isinstance(element, ast.Starred):
            return False
        value = element.value
        if not isinstance(value, ast.Call) or _callable_name(value.func) != name:
            return False
    return True


def canonical_agent_topology_present(source_root: Path) -> bool:
    """Return whether the graph funnel installs the reviewed root/child seam."""

    builder_path = source_root / CANONICAL_MODEL_FUNNEL
    factory_path = source_root / CANONICAL_RUNTIME_FACTORY
    if not builder_path.is_file() or not factory_path.is_file():
        return False

    builder_tree = ast.parse(
        builder_path.read_text(encoding="utf-8"),
        filename=str(builder_path),
    )
    builder_calls = [
        _callable_name(node.func)
        for node in ast.walk(builder_tree)
        if isinstance(node, ast.Call)
    ]
    deep_agent_function = next(
        (
            node
            for node in builder_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "build_deep_agent"
        ),
        None,
    )
    if deep_agent_function is None:
        return False
    deep_agent_calls = [
        _callable_name(node.func)
        for node in ast.walk(deep_agent_function)
        if isinstance(node, ast.Call)
    ]
    if (
        builder_calls.count("create_deep_agent") != 1
        or any(name == "create_agent" for name in builder_calls)
        or deep_agent_calls.count("build_chat_model") != 1
        or deep_agent_calls.count("create_deep_agent") != 1
    ):
        return False

    factory_tree = ast.parse(
        factory_path.read_text(encoding="utf-8"),
        filename=str(factory_path),
    )
    reviewed_requests = [
        node
        for node in ast.walk(factory_tree)
        if isinstance(node, ast.Call)
        and _callable_name(node.func) == "DeepAgentBuildRequest"
    ]
    if len(reviewed_requests) != 1:
        return False
    root_middleware_keyword = next(
        (
            keyword.value
            for keyword in reviewed_requests[0].keywords
            if keyword.arg == "middleware"
        ),
        None,
    )
    child_middleware_keyword = next(
        (
            keyword.value
            for keyword in reviewed_requests[0].keywords
            if keyword.arg == "universal_middleware_factories"
        ),
        None,
    )
    if not isinstance(root_middleware_keyword, (ast.Tuple, ast.List)):
        return False
    if not isinstance(child_middleware_keyword, (ast.Tuple, ast.List)):
        return False
    return _sequence_matches(
        root_middleware_keyword.elts,
        required=_ReviewedMiddleware.ROOT,
        optional=_ReviewedMiddleware.ROOT_OPTIONAL,
        constructed=True,
    ) and _sequence_matches(
        child_middleware_keyword.elts,
        required=_ReviewedMiddleware.CHILD,
        optional=_ReviewedMiddleware.CHILD_OPTIONAL,
        constructed=False,
    )


def _init_reference_violations(tree: ast.AST, rel_path: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _INIT_FUNCTIONS:
            found.append(f"{rel_path}: references {node.id}()")
        elif isinstance(node, ast.Attribute) and node.attr in _INIT_FUNCTIONS:
            found.append(f"{rel_path}: references .{node.attr}()")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name in _INIT_FUNCTIONS:
                    found.append(f"{rel_path}: imports {alias.name}")
    return found


def _call_reference_violations(
    tree: ast.AST,
    rel_path: str,
    *,
    forbidden: frozenset[str],
    seam: str,
) -> list[str]:
    found: list[str] = []
    aliases = _imported_callable_aliases(tree, forbidden)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callable_name = _callable_name(node.func)
        canonical_name = aliases.get(callable_name, callable_name)
        if canonical_name in forbidden:
            found.append(f"{rel_path}: calls {canonical_name}() outside {seam}")
    return found


def _imported_callable_aliases(
    tree: ast.AST,
    forbidden: frozenset[str],
) -> dict[str | None, str]:
    """Resolve direct aliases so renaming an import cannot evade the gate."""

    aliases: dict[str | None, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for imported in node.names:
            if imported.name in forbidden:
                aliases[imported.asname or imported.name] = imported.name
    return aliases


def _provider_import_violations(tree: ast.AST, rel_path: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _top_module(alias.name) in _BANNED_MODULES:
                    found.append(f"{rel_path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and _top_module(node.module) in _BANNED_MODULES:
                found.append(f"{rel_path}: from {node.module} import ...")
    return found


def _top_module(module: str) -> str:
    return module.split(".", 1)[0]


def _callable_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _lexical_scope(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    names: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(current.name)
    return ".".join(reversed(names)) or "<module>"


__all__ = (
    "CANONICAL_MODEL_FUNNEL",
    "CANONICAL_RUNTIME_FACTORY",
    "canonical_agent_topology_present",
    "canonical_chat_model_call_sites",
    "canonical_model_funnel_present",
    "llm_seam_violations",
)
