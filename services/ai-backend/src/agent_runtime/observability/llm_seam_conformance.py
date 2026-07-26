"""Read-only AST proof that provider calls cannot bypass usage metering."""

from __future__ import annotations

import ast
from pathlib import Path


CANONICAL_MODEL_FUNNEL = Path("agent_runtime/execution/deep_agent_builder.py")
_INIT_FUNCTIONS = frozenset({"init_chat_model", "init_embeddings"})
_BANNED_MODULES = frozenset(
    {
        "langchain_openai",
        "langchain_anthropic",
        "langchain_google_genai",
        "anthropic",
        "openai",
    }
)


def llm_seam_violations(source_root: Path) -> tuple[str, ...]:
    """Return direct construction/import paths that bypass the UsageMeter seam."""

    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if relative != CANONICAL_MODEL_FUNNEL.as_posix():
            violations.extend(_init_reference_violations(tree, relative))
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


__all__ = (
    "CANONICAL_MODEL_FUNNEL",
    "canonical_model_funnel_present",
    "llm_seam_violations",
)
