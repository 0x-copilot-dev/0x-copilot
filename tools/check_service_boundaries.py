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
import re


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


_TS_STATIC_IMPORT = re.compile(
    r"(?:import(?:\s+type)?|export)\s+(?:[^;]*?\s+from\s+)?[\"']([^\"']+)[\"']"
)
_TS_DYNAMIC_IMPORT = re.compile(r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_TS_REQUIRE = re.compile(r"(?<![\w$.])require\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_TYPESCRIPT_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
_DESKTOP_MAIN_IMPORT_PREFIX = "@0x-copilot/desktop/main"
_DESKTOP_MAIN_IPC_CONTRACTS = (
    "apps/desktop/main/capabilities/channels.ts",
    "apps/desktop/main/connectors/channels.ts",
    "apps/desktop/main/services/first-run-channels.ts",
    "apps/desktop/main/services/secure-storage-channels.ts",
)


def desktop_ipc_boundary_violations(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Reject Electron/IPC authority leakage across the desktop trust boundary.

    Renderer and shared packages consume the narrow, typed WindowBridge/IPC
    contracts.  Only preload may import ``ipcRenderer``/``contextBridge`` and
    only Electron main may import ``ipcMain`` or broker implementation code.
    This scanner is intentionally lexical because TypeScript is not imported by
    a Python release gate; it recognizes only static import/export,
    ``import(\"literal\")``, and ``require(\"literal\")`` module specifiers
    rather than attempting broader JavaScript parsing.  The forms are covered
    by planted canaries.
    """

    checks: list[tuple[str, Path, frozenset[str]]] = [
        (
            "desktop-renderer",
            repo_root / "apps/desktop/renderer",
            frozenset({"electron"}),
        ),
        (
            "desktop-preload",
            repo_root / "apps/desktop/preload",
            frozenset({"electron/main", "electron/renderer"}),
        ),
    ]
    packages_root = repo_root / "packages"
    if packages_root.is_dir():
        checks.extend(
            (package.name, package / "src", frozenset({"electron"}))
            for package in sorted(packages_root.iterdir())
            if (package / "src").is_dir()
        )
    allowed_contracts = _desktop_main_ipc_contracts(repo_root)
    desktop_main_root = (repo_root / "apps/desktop/main").resolve()
    violations: list[str] = []
    for name, root, forbidden_modules in checks:
        if not root.is_dir():
            violations.append(f"{name}:missing-source-root")
            continue
        for path in sorted(_typescript_sources(root)):
            for module in _typescript_imports(path):
                if module in forbidden_modules:
                    relative = path.relative_to(root).as_posix()
                    violations.append(f"{name}:{relative}:electron-import:{module}")
                resolved_main_import = _resolve_desktop_main_import(
                    source=path,
                    module=module,
                    repo_root=repo_root,
                )
                if (
                    resolved_main_import is not None
                    and _is_within(resolved_main_import, desktop_main_root)
                    and resolved_main_import not in allowed_contracts
                ):
                    relative = path.relative_to(root).as_posix()
                    violations.append(f"{name}:{relative}:desktop-main-import:{module}")
    return tuple(sorted(violations))


def _imported_modules(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
    return tuple(found)


def _typescript_sources(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for extension in tuple(f"*{suffix}" for suffix in _TYPESCRIPT_EXTENSIONS)
        for path in root.rglob(extension)
        if not path.name.endswith((".test.ts", ".test.tsx", ".type-test.ts"))
    )


def _typescript_imports(path: Path) -> tuple[str, ...]:
    source = path.read_text(encoding="utf-8")
    return tuple(
        module
        for pattern in (_TS_STATIC_IMPORT, _TS_DYNAMIC_IMPORT, _TS_REQUIRE)
        for module in pattern.findall(source)
    )


def _desktop_main_ipc_contracts(repo_root: Path) -> frozenset[Path]:
    """Return the only Electron-main modules renderer/shared code may import.

    These modules expose typed IPC channel names only.  Adding a contract is a
    deliberate trust-boundary decision rather than a suffix-pattern exception.
    """

    return frozenset(
        (repo_root / contract).resolve() for contract in _DESKTOP_MAIN_IPC_CONTRACTS
    )


def _resolve_desktop_main_import(
    *,
    source: Path,
    module: str,
    repo_root: Path,
) -> Path | None:
    """Resolve relative/desktop-main TypeScript imports to their canonical path.

    TypeScript commonly omits extensions, so checking the raw specifier would
    let ``../main/capabilities/broker`` bypass a ``.ts`` rule.  Resolution is
    intentionally filesystem-light and deterministic: existing candidates win;
    an unresolved extensionless path canonicalizes to ``.ts`` so planted
    canaries and future imports remain fail-closed.
    """

    normalized = module.replace("\\", "/")
    if normalized.startswith("."):
        candidate = source.parent / normalized
    elif normalized == _DESKTOP_MAIN_IMPORT_PREFIX:
        candidate = repo_root / "apps/desktop/main"
    elif normalized.startswith(f"{_DESKTOP_MAIN_IMPORT_PREFIX}/"):
        suffix = normalized.removeprefix(_DESKTOP_MAIN_IMPORT_PREFIX).lstrip("/")
        candidate = repo_root / "apps/desktop/main" / suffix
    else:
        return None
    return _resolve_typescript_path(candidate)


def _resolve_typescript_path(candidate: Path) -> Path:
    """Normalize an explicit or extensionless TypeScript module path."""

    if candidate.suffix in _TYPESCRIPT_EXTENSIONS:
        return candidate.resolve()

    for suffix in _TYPESCRIPT_EXTENSIONS:
        typed_candidate = candidate.with_suffix(suffix)
        if typed_candidate.is_file():
            return typed_candidate.resolve()
    if candidate.is_dir():
        for suffix in _TYPESCRIPT_EXTENSIONS:
            index_candidate = candidate / f"index{suffix}"
            if index_candidate.is_file():
                return index_candidate.resolve()
    return candidate.with_suffix(".ts").resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_service_boundaries")
    parser.parse_args(argv)
    violations = (*boundary_violations(), *desktop_ipc_boundary_violations())
    if not violations:
        print("OK: deployable Python service boundaries passed")
        return 0
    print("FAIL: deployable Python service boundary violations")
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
