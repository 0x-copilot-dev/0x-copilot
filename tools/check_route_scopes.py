"""A10 — CI static check that every FastAPI route declares its RBAC policy.

Walks both ``services/backend`` and ``services/ai-backend`` source trees,
loads each FastAPI app, enumerates every registered route, and asserts:

  - the route has at least one ``Depends(...)`` whose dependency function
    is one of ``RequireScopes`` / ``RequireRoles`` / ``RequireAnyScope``
    / ``public_route``;
  - or the route is in the explicit allow-list below (boilerplate
    surfaces like FastAPI's auto-generated ``/openapi.json`` and the
    OAuth callback endpoints whose trust comes from the OAuth state).

The dependency check looks at both route-level and router-level
``dependencies=`` so a router with router-level RBAC covers every route
registered on it. Functions decorated with ``RequireScopes(...)`` etc.
expose a ``__rbac_required_scopes__`` (or ``_roles``, ``_any_scopes``)
attribute the catalog uses; ``public_route()`` callables are detected by
their function name.

Exit codes:
    0  — every route is annotated.
    1  — at least one route is missing an annotation. The script lists
         the offenders.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from pathlib import Path

_LOGGER = logging.getLogger("tools.check_route_scopes")


_REPO_ROOT = Path(__file__).resolve().parents[1]


# Allow-list — routes the static check intentionally skips. Every entry
# needs a one-line justification: an auditor reading this list should
# understand WHY the route is exempt rather than guessing.
_ALLOW_LIST: frozenset[str] = frozenset(
    {
        # FastAPI auto-generated OpenAPI surfaces — never gated.
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        # backend ``/v1/health`` is annotated via ``public_route`` but
        # also surfaces under nginx /health for cluster probes; explicit
        # safety in case ingress strips the prefix.
        "/health",
    }
)


def _service_apps() -> list[tuple[str, str]]:
    """Return [(label, dotted-app-path), ...] for every FastAPI app to scan."""

    return [
        ("backend", "backend_app.app:create_app"),
        ("ai-backend", "runtime_api.app:RuntimeApiAppFactory.create_app"),
    ]


def _is_rbac_dependency(dep: object) -> bool:
    """Return True if ``dep`` was produced by RequireScopes / RequireRoles /
    RequireAnyScope / public_route.

    ``dep`` is a FastAPI ``Dependant`` whose ``.call`` attribute is the
    actual dependency function. The ``RequireScopes(...)``, etc., factories
    stamp ``__rbac_required_*__`` attributes on the closure they return;
    ``public_route()`` returns a closure named ``_public``.
    """

    func = getattr(dep, "call", None)
    if func is None:
        # Some Dependant nodes hold sub-dependants only; the leaf is what
        # carries our marker.
        return False
    if any(
        hasattr(func, attr)
        for attr in (
            "__rbac_required_scopes__",
            "__rbac_required_roles__",
            "__rbac_required_any_scopes__",
        )
    ):
        return True
    qualname = getattr(func, "__qualname__", "") or ""
    if "public_route" in qualname or qualname.endswith("._public"):
        return True
    return False


def _walk_app_routes(app) -> list[tuple[str, list, str]]:
    """Yield ``(path, route_dependencies, router_dependencies_summary)`` per route.

    ``route_dependencies`` flattens both router-level dependencies (set on
    the APIRouter at construction) and route-level dependencies (set on
    the individual ``add_api_route`` call). FastAPI's ``Mount`` / static
    surfaces are skipped.
    """

    rows: list[tuple[str, list, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        dependant = getattr(route, "dependant", None)
        deps = list(getattr(dependant, "dependencies", []) or [])
        rows.append((path, deps, ""))
    return rows


def _load_app(label: str, dotted: str):
    module_path, _, factory_path = dotted.partition(":")
    src_path = _REPO_ROOT / "services" / label / "src"
    contracts_src = _REPO_ROOT / "packages" / "service-contracts" / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    if str(contracts_src) not in sys.path:
        sys.path.insert(0, str(contracts_src))
    module = importlib.import_module(module_path)
    # Support both module-level functions and class.method paths.
    target: object = module
    for attr in factory_path.split("."):
        target = getattr(target, attr)
    return target()


def _check_app(label: str, dotted: str) -> list[str]:
    """Return a list of offending route paths for one app."""

    failures: list[str] = []
    try:
        app = _load_app(label, dotted)
    except Exception as exc:
        return [f"{label}: failed to load app ({exc})"]

    for path, deps, _summary in _walk_app_routes(app):
        if path in _ALLOW_LIST:
            continue
        # Recurse: each Dependant node has nested ``dependencies`` for
        # nested Depends. Walk them all.
        if _has_rbac(deps):
            continue
        failures.append(f"{label}: {path} has no RequireScopes/Roles/Any/public_route")
    return failures


def _has_rbac(deps: list) -> bool:
    """True if any dependency in the (possibly nested) tree is an RBAC dep."""

    for dep in deps:
        if _is_rbac_dependency(dep):
            return True
        nested = list(getattr(dep, "dependencies", []) or [])
        if nested and _has_rbac(nested):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="check_route_scopes")
    parser.add_argument(
        "--service",
        choices=("backend", "ai-backend", "all"),
        default="all",
        help="Limit to one service.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    apps = _service_apps()
    if args.service != "all":
        apps = [(label, dotted) for label, dotted in apps if label == args.service]

    # Suppress the deployment-profile resolver crash for envs that don't
    # have ENTERPRISE_AUTH_SECRET set (CI for this static check doesn't).
    os.environ.setdefault("BACKEND_ENVIRONMENT", "development")
    os.environ.setdefault("RUNTIME_ENVIRONMENT", "development")

    all_failures: list[str] = []
    for label, dotted in apps:
        all_failures.extend(_check_app(label, dotted))

    if all_failures:
        sys.stdout.write("FAIL: routes missing RBAC annotation\n")
        for failure in all_failures:
            sys.stdout.write(f"  - {failure}\n")
        sys.stdout.write(
            "\nFix: add `dependencies=[Depends(RequireScopes(...))]` (or "
            "RequireRoles / RequireAnyScope / public_route) to the route, "
            "OR set `dependencies=` on the APIRouter so every route on it "
            "is covered.\n"
        )
        return 1

    sys.stdout.write("OK: every route declares its RBAC policy.\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
