"""Facade registration and identity-boundary guard for every active E1 route."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings
from copilot_service_contracts.e1_authorization import (
    E1_SENSITIVE_ROUTE_KEYS,
    E1_SENSITIVE_ROUTES,
    E1SensitiveRoute,
    is_e1_sensitive_path,
)


_ORG = "org_e1_facade"
_USER = "user_e1_facade"
_SECRET = "e1-facade-auth-secret"
_PATH_VALUES = {
    "{run_id}": "run_owner",
    "{artifact_id}": "art_owner",
    "{revision}": "1",
    "{stage_id}": "stage_owner",
    "{conversation_id}": "conv_owner",
    "{hold_id}": "lh_owner",
}


def _bearer(
    *,
    org_id: str = _ORG,
    user_id: str = _USER,
    scopes: tuple[str, ...] = ("runtime:use",),
) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": org_id,
                    "user_id": user_id,
                    "roles": ["employee"],
                    "permission_scopes": list(scopes),
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                _SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
            ).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"Bearer {payload}.{signature}"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "e1-facade-service-token")


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(FacadeSettings(artifact_effects_v2=True)))


def _materialize(path: str) -> str:
    for template, value in _PATH_VALUES.items():
        path = path.replace(template, value)
    return path


def _request_kwargs(spec: E1SensitiveRoute) -> dict[str, object]:
    params: dict[str, object] = {
        "org_id": "query_attacker_org",
        "user_id": "query_attacker_user",
    }
    if spec.family in {"stage", "effect"}:
        params["run_id"] = "run_owner"
    kwargs: dict[str, object] = {"params": params}
    if spec.method == "POST":
        kwargs["json"] = {}
    return kwargs


def test_facade_inventory_exactly_covers_every_active_e1_route() -> None:
    """Every active E1 public path must be reachable only through the facade."""

    app = create_app(FacadeSettings(artifact_effects_v2=True))
    actual = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and is_e1_sensitive_path(route.path)
        for method in route.methods
    }
    assert actual == E1_SENSITIVE_ROUTE_KEYS
    assert all(
        route.facade_path == route.runtime_path
        and route.facade_path.startswith("/v1/")
        and not route.facade_path.startswith("/internal/")
        for route in E1_SENSITIVE_ROUTES
    )


@pytest.mark.parametrize("spec", E1_SENSITIVE_ROUTES, ids=lambda spec: spec.route_id)
def test_every_e1_facade_path_requires_a_bearer_before_forwarding(
    client: TestClient,
    spec: E1SensitiveRoute,
) -> None:
    """A client cannot bypass facade identity enforcement on any matrix row."""

    response = client.request(
        spec.method,
        _materialize(spec.facade_path),
        **_request_kwargs(spec),
    )
    assert response.status_code == 401, (spec.route_id, response.text)


def test_compatibility_sources_facade_forwards_only_verified_identity(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """Facade query values cannot overwrite the identity sent to ai-backend."""

    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ANN001
        captured.append(
            {
                "method": method,
                "path": path,
                "target": target,
                "params": kwargs["params"],
                "identity": kwargs["identity"],
            }
        )
        return {
            "conversation_id": "conv_owner",
            "run_id": "run_owner",
            "sources": [],
            "truncated": False,
        }

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    response = client.get(
        "/v1/agent/conversations/conv_owner/sources"
        "?run_id=run_owner&limit=7&org_id=query_attacker&user_id=query_attacker",
        headers={"authorization": _bearer()},
    )

    assert response.status_code == 200, response.text
    assert captured[0]["method"] == "GET"
    assert captured[0]["path"] == "/v1/agent/conversations/conv_owner/sources"
    assert captured[0]["target"] == "ai_backend"
    assert captured[0]["params"] == {
        "org_id": _ORG,
        "user_id": _USER,
        "run_id": "run_owner",
        "limit": 7,
    }
    identity = captured[0]["identity"]
    assert (identity.org_id, identity.user_id) == (_ORG, _USER)
