"""Static contract: critical facade paths remain registered (no network)."""

from __future__ import annotations

from backend_facade.app import create_app


def test_openapi_includes_core_product_paths() -> None:
    """Guards against accidental removal of primary /v1 routes from the facade."""

    app = create_app()
    paths = app.openapi()["paths"]
    required = (
        "/v1/session",
        "/v1/mcp/servers",
        "/v1/mcp/tools",
        "/v1/agent/conversations",
        "/v1/agent/runs",
        "/v1/agent/models",
        "/v1/skills",
        "/v1/agent/history",
    )
    for route in required:
        assert route in paths, f"missing route {route}"
