"""Closed identity/scope matrix for every E1-sensitive runtime route.

This is deliberately a route-table guard, not a hand-maintained sample of a
few happy paths.  The shared inventory describes every public E1 route, while
``is_e1_sensitive_path`` independently recognises the route families.  A new
artifact, stage, receipt, usage, source, pending-work, or legal-hold route
therefore fails this suite until its identity and opacity contract is recorded.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from copilot_service_contracts.e1_authorization import (
    E1_SENSITIVE_ROUTE_COUNT,
    E1_SENSITIVE_ROUTE_KEYS,
    E1_SENSITIVE_ROUTES,
    E1SensitiveRoute,
    is_e1_sensitive_path,
)
from runtime_api.http.legal_hold_routes import LegalHoldRouter
from runtime_api.http.routes import RuntimeApiRouter, UsageApiRouter
from runtime_api.identity import get_identity


_PATH_VALUES = {
    "{run_id}": "run_owner",
    "{source_id}": "source_v2_owner",
    "{artifact_id}": "art_owner",
    "{revision}": "1",
    "{stage_id}": "stage_owner",
    "{conversation_id}": "conv_owner",
    "{hold_id}": "lh_owner",
}


def _runtime_routers(monkeypatch: pytest.MonkeyPatch) -> tuple[APIRouter, ...]:
    """Build the public routers with every E1 cohort enabled."""

    monkeypatch.setenv("SURFACES_V2", "true")
    return (
        RuntimeApiRouter.create_router(
            artifact_effects_v2=True,
            workspace_approval_enabled=True,
        ),
        UsageApiRouter.create_router(),
        LegalHoldRouter.create_router(),
    )


def _runtime_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build only public routers; auth fails before any app service is read."""

    app = FastAPI()
    for router in _runtime_routers(monkeypatch):
        app.include_router(router)
    return app


def _materialize(path: str) -> str:
    for template, value in _PATH_VALUES.items():
        path = path.replace(template, value)
    return path


def _request_kwargs(spec: E1SensitiveRoute) -> dict[str, object]:
    """Supply only routing inputs so auth is evaluated before a handler body."""

    # Query identities are intentionally hostile inputs: no E1 handler may use
    # them as a substitute for the verified ``Identity`` dependency.
    params: dict[str, object] = {
        "org_id": "query_attacker_org",
        "user_id": "query_attacker_user",
    }
    if spec.family in {"stage", "effect"}:
        params["run_id"] = "run_owner"

    kwargs: dict[str, object] = {"params": params}
    if spec.method == "POST":
        # Authentication dependencies run before request-model validation.  An
        # empty body makes this property visible without coupling this guard to
        # each independent mutation schema.
        kwargs["json"] = {}
    return kwargs


def _sensitive_routes(routers: tuple[APIRouter, ...]) -> tuple[APIRoute, ...]:
    return tuple(
        route
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute) and is_e1_sensitive_path(route.path)
    )


def _dependency_calls(dependant: object) -> Iterable[object]:
    """Yield every FastAPI dependency callable recursively."""

    call = getattr(dependant, "call", None)
    if call is not None:
        yield call
    for child in getattr(dependant, "dependencies", ()):
        yield from _dependency_calls(child)


def test_runtime_inventory_exactly_covers_e1_route_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registered E1-family route can silently omit an inventory row."""

    actual = {
        (method, route.path)
        for route in _sensitive_routes(_runtime_routers(monkeypatch))
        for method in route.methods
    }
    assert actual == E1_SENSITIVE_ROUTE_KEYS


def test_every_e1_route_declares_the_strict_identity_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure a future handler cannot fall back to query-derived identity."""

    by_key = {
        (method, route.path): route
        for route in _sensitive_routes(_runtime_routers(monkeypatch))
        for method in route.methods
    }
    missing = [
        route_id
        for route_id, method, path in (
            (spec.route_id, spec.method, spec.runtime_path)
            for spec in E1_SENSITIVE_ROUTES
        )
        if get_identity not in set(_dependency_calls(by_key[(method, path)].dependant))
    ]
    assert missing == []


@pytest.mark.parametrize("spec", E1_SENSITIVE_ROUTES, ids=lambda spec: spec.route_id)
def test_every_e1_route_rejects_missing_identity_even_with_spoofed_query_values(
    monkeypatch: pytest.MonkeyPatch,
    spec: E1SensitiveRoute,
) -> None:
    """Anonymous callers cannot turn ``org_id`` / ``user_id`` query values into identity."""

    client = TestClient(_runtime_app(monkeypatch))
    response = client.request(
        spec.method,
        _materialize(spec.runtime_path),
        **_request_kwargs(spec),
    )
    assert response.status_code == 401, (spec.route_id, response.text)


@pytest.mark.parametrize("spec", E1_SENSITIVE_ROUTES, ids=lambda spec: spec.route_id)
def test_every_e1_route_rejects_a_verified_identity_without_required_scope(
    monkeypatch: pytest.MonkeyPatch,
    spec: E1SensitiveRoute,
) -> None:
    """The matrix exercises the production enforcement mode for every row."""

    monkeypatch.setenv("RBAC_MODE", "enforce")
    client = TestClient(_runtime_app(monkeypatch))
    response = client.request(
        spec.method,
        _materialize(spec.runtime_path),
        headers={
            "x-enterprise-org-id": "org_owner",
            "x-enterprise-user-id": "user_owner",
        },
        **_request_kwargs(spec),
    )
    assert response.status_code == 403, (spec.route_id, response.text)


def test_inventory_records_the_expected_identity_and_opacity_classes() -> None:
    """Keep the policy vocabulary closed and reviewable in one place."""

    assert {route.identity_class for route in E1_SENSITIVE_ROUTES} == {
        "member",
        "audit_or_admin",
        "retention_admin",
    }
    assert {
        route.foreign_status
        for route in E1_SENSITIVE_ROUTES
        if route.foreign_status is not None
    } == {404}


def test_d4_d5_source_open_is_an_active_artifact_revision_boundary() -> None:
    """The D8 reservation is promoted into the 29→30 active inventory."""

    assert len(E1_SENSITIVE_ROUTES) == E1_SENSITIVE_ROUTE_COUNT
    source_routes = [
        route for route in E1_SENSITIVE_ROUTES if route.route_id == "source_open"
    ]
    assert len(source_routes) == 1
    source_open = source_routes[0]
    assert source_open.method == "POST"
    assert source_open.runtime_path == (
        "/v1/agent/runs/{run_id}/sources/{source_id}/open"
    )
    assert source_open.facade_path == source_open.runtime_path
    assert source_open.family == "source"
    assert source_open.identity_class == "member"
    assert source_open.parent_scope == "artifact_revision"
    assert source_open.foreign_status == 404
