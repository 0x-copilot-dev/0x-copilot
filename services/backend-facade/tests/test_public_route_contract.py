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
        # PRD-H.4 — pin / unpin route proxied to ai-backend.
        "/v1/agent/conversations/{conversation_id}/pin",
        "/v1/agent/runs",
        # PRD-12 — the rail Run-badge count; must be registered above
        # ``/v1/agent/runs/{run_id}`` so the literal is not shadowed.
        "/v1/agent/runs/active_count",
        "/v1/agent/models",
        "/v1/skills",
        "/v1/agent/history",
        "/v1/settings/provider-keys",
        "/v1/settings/provider-keys/{provider}",
        # Generative Surfaces v2 (PRD-A3) — the folded SurfaceStore for a run.
        "/v1/agent/runs/{run_id}/surfaces",
        # Generative Surfaces v2 (PRD-B4) — user-invited "Suggest a shape".
        "/v1/agent/surfaces/{surface_id}/shape-request",
        # Generative Surfaces v2 (PRD-E3) — the tamper-evident receipt export.
        "/v1/agent/runs/{run_id}/receipt/export",
        # Usage family (B4 + E3) — the UI-less rollup endpoints stay registered.
        "/v1/usage/me",
        "/v1/usage/runs/{run_id}",
        "/v1/usage/conversations/{conversation_id}",
        "/v1/usage/org/purpose",
    )
    for route in required:
        assert route in paths, f"missing route {route}"


def test_agent_runs_exposes_both_get_and_post() -> None:
    """PRD-05 — ``/v1/agent/runs`` carries BOTH the run-history collection GET and
    the create-run POST. The GET must be registered above ``/v1/agent/runs/{run_id}``
    so the unconstrained-``run_id`` detail route does not shadow the literal."""

    app = create_app()
    methods = set(app.openapi()["paths"]["/v1/agent/runs"])
    assert methods >= {"get", "post"}, methods
