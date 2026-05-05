"""A10 — pytest harness for ``tools/check_route_scopes.py``.

Run from each service's venv. The test asserts that the relevant
service-side check is green: a route added without ``RequireScopes`` /
``RequireRoles`` / ``RequireAnyScope`` / ``public_route`` will fail this
test before it can ship to prod.

CI integration: this test is collected by both ``ci-backend.yml`` and
``ci-ai-backend.yml`` (each from its own venv). The check loads the
FastAPI app for that service and walks the route tree.
"""

from __future__ import annotations

import importlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().with_name("check_route_scopes.py")


def _load_module():
    spec = importlib.util.spec_from_file_location(  # type: ignore[attr-defined]
        "check_route_scopes",
        _SCRIPT_PATH,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)  # type: ignore[attr-defined]
    sys.modules["check_route_scopes"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _can_load(label: str) -> bool:
    """Return True when this venv can import the named service's app."""

    apps = dict(_MODULE._service_apps())
    dotted = apps.get(label)
    if dotted is None:
        return False
    try:
        _MODULE._load_app(label, dotted)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _can_load("backend"), reason="backend deps not installed")
def test_backend_routes_all_have_rbac() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _MODULE.main(["--service", "backend"])
    assert rc == 0, buf.getvalue()


@pytest.mark.skipif(not _can_load("ai-backend"), reason="ai-backend deps not installed")
def test_ai_backend_routes_all_have_rbac() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _MODULE.main(["--service", "ai-backend"])
    assert rc == 0, buf.getvalue()
