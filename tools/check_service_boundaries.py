"""Read-only guard for deployable Python service import boundaries.

Deployable services communicate by HTTP/contracts, never by importing a
sibling service's ``src`` package.  This deliberately small AST guard catches
the unambiguous form of that violation: importing a sibling service's top-level
module.  It is parameterised for canaries and is also consumed by E2 D9.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ServiceBoundary:
    name: str
    source_root: Path
    forbidden_top_levels: frozenset[str]


def default_boundaries(repo_root: Path = REPO_ROOT) -> tuple[ServiceBoundary, ...]:
    return (
        ServiceBoundary(
            "ai-backend",
            repo_root / "services/ai-backend/src",
            frozenset({"backend_app", "backend_facade"}),
        ),
        ServiceBoundary(
            "backend",
            repo_root / "services/backend/src",
            frozenset(
                {"agent_runtime", "runtime_api", "runtime_adapters", "backend_facade"}
            ),
        ),
        ServiceBoundary(
            "backend-facade",
            repo_root / "services/backend-facade/src",
            frozenset(
                {"backend_app", "agent_runtime", "runtime_api", "runtime_adapters"}
            ),
        ),
    )


def boundary_violations(
    boundaries: tuple[ServiceBoundary, ...] | None = None,
) -> tuple[str, ...]:
    """Return sorted `service:path:line:module` violations without importing code."""

    violations: list[str] = []
    for boundary in boundaries or default_boundaries():
        if not boundary.source_root.is_dir():
            violations.append(f"{boundary.name}:missing-source-root")
            continue
        for path in sorted(boundary.source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module, line in _imported_modules(tree):
                if module.split(".", 1)[0] in boundary.forbidden_top_levels:
                    relative = path.relative_to(boundary.source_root).as_posix()
                    violations.append(f"{boundary.name}:{relative}:{line}:{module}")
    return tuple(sorted(violations))


def _imported_modules(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_service_boundaries")
    parser.parse_args(argv)
    violations = boundary_violations()
    if not violations:
        print("OK: deployable Python service boundaries passed")
        return 0
    print("FAIL: deployable Python service boundary violations")
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
