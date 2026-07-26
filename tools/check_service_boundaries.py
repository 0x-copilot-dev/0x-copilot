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


_TS_IMPORT = re.compile(
    r"(?:import(?:\s+type)?|export)\s+(?:[^;]*?\s+from\s+)?[\"']([^\"']+)[\"']"
)


def desktop_ipc_boundary_violations(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Reject Electron/IPC authority leakage across the desktop trust boundary.

    Renderer and shared packages consume the narrow, typed WindowBridge/IPC
    contracts.  Only preload may import ``ipcRenderer``/``contextBridge`` and
    only Electron main may import ``ipcMain`` or broker implementation code.
    This scanner is intentionally lexical because TypeScript is not imported by
    a Python release gate; it recognizes module specifiers rather than broad
    comment/text keywords and is covered by planted canaries.
    """

    checks = (
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
        (
            "chat-transport",
            repo_root / "packages/chat-transport/src",
            frozenset({"electron"}),
        ),
        (
            "chat-surface",
            repo_root / "packages/chat-surface/src",
            frozenset({"electron"}),
        ),
        (
            "api-types",
            repo_root / "packages/api-types/src",
            frozenset({"electron"}),
        ),
    )
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
                if _imports_desktop_main_or_broker(module):
                    relative = path.relative_to(root).as_posix()
                    violations.append(
                        f"{name}:{relative}:desktop-main-broker-import:{module}"
                    )
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
        for extension in ("*.ts", "*.tsx", "*.mts", "*.cts")
        for path in root.rglob(extension)
        if not path.name.endswith((".test.ts", ".test.tsx", ".type-test.ts"))
    )


def _typescript_imports(path: Path) -> tuple[str, ...]:
    return tuple(_TS_IMPORT.findall(path.read_text(encoding="utf-8")))


def _imports_desktop_main_or_broker(module: str) -> bool:
    """Keep Electron-main/broker authority out of renderer and shared code."""

    normalized = module.replace("\\", "/")
    if normalized.startswith("@0x-copilot/desktop/main"):
        return True
    # Channel-name modules are an intentionally shared typed RPC vocabulary;
    # importing those constants grants neither Electron nor broker authority.
    private_suffixes = (
        "/main/capabilities/broker",
        "/main/capabilities/host-fs",
        "/main/capabilities/workspace-authority",
        "/main/browser/private-effect-bridge",
        "/main/services/local-service-identity",
        "/main/services/service-env",
    )
    return any(normalized.endswith(suffix) for suffix in private_suffixes)


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
