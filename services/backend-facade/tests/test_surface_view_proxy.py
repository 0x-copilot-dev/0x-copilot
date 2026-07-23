"""Facade must proxy the PRD-B3 view-lifecycle routes to ai-backend.

Passthrough only: method/path/target, org+user scoping on params, the ``run_id``
query param threaded through, the slash-bearing ``surface_id`` re-encoded for the
downstream path, a bearer required, and an upstream error surfaced verbatim.
Mirrors the capture-``forward_json`` pattern from ``test_run_surfaces_proxy``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

_ORG_ID = "org_surf_facade"
_USER_ID = "user_surf_facade"
_RUN_ID = "run_surf_1"
_SURFACE_ID = "record://linear/get_issue/issue-1"
_ENCODED = quote(_SURFACE_ID, safe="")


def _bearer(*, secret: str = "test-auth-secret") -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": _ORG_ID,
                    "user_id": _USER_ID,
                    "roles": ["employee"],
                    "permission_scopes": ["runtime:use"],
                }
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(
                secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
            ).digest()
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"Bearer {payload}.{signature}"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "test-auth-secret")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        captured.append(
            {
                "method": method,
                "path": path,
                "target": target,
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
            }
        )
        return {"ok": True}

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def test_facade_proxies_regenerate(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/regenerate?run_id={_RUN_ID}",
        headers={"authorization": _bearer()},
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    call = captured[0]
    assert call["method"] == "POST"
    assert call["target"] == "ai_backend"
    # Slash-bearing surface_id is re-encoded so ai-backend's path router matches.
    assert call["path"] == f"/v1/agent/surfaces/{_SURFACE_ID}/regenerate"
    assert call["json"] == {}
    assert call["params"]["org_id"] == _ORG_ID
    assert call["params"]["user_id"] == _USER_ID
    assert call["params"]["run_id"] == _RUN_ID


def test_facade_proxies_view_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/view-preference?run_id={_RUN_ID}",
        headers={"authorization": _bearer()},
        json={"keep": "generic"},
    )

    assert response.status_code == 200, response.text
    call = captured[0]
    assert call["method"] == "POST"
    assert call["path"] == f"/v1/agent/surfaces/{_SURFACE_ID}/view-preference"
    assert call["json"] == {"keep": "generic"}
    assert call["params"]["run_id"] == _RUN_ID


def test_regenerate_requires_bearer() -> None:
    client = TestClient(create_app(FacadeSettings()))
    response = client.post(f"/v1/agent/surfaces/{_ENCODED}/regenerate?run_id={_RUN_ID}")
    assert response.status_code == 401


def test_view_preference_requires_run_id() -> None:
    client = TestClient(create_app(FacadeSettings()))
    # run_id is a required query param — omitting it is a 422 before any forward.
    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/view-preference",
        headers={"authorization": _bearer()},
        json={"keep": "shaped"},
    )
    assert response.status_code == 422


def test_regenerate_upstream_error_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        raise HTTPException(status_code=409, detail="regenerate_limit_reached")

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/regenerate?run_id={_RUN_ID}",
        headers={"authorization": _bearer()},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "regenerate_limit_reached"
