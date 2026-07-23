"""Facade must proxy ``GET /v1/agent/pending-work`` to ai-backend (PRD-E2).

Passthrough only: method/path/target, (org, user) scoping on params, 401 without
a bearer, and upstream errors surfaced verbatim. Mirrors the capture-``forward_json``
pattern from ``test_run_surfaces_proxy``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend_facade.app as facade_app
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings

_ORG_ID = "org_pending_facade"
_USER_ID = "user_pending_facade"
_PATH = "/v1/agent/pending-work"


def _bearer(
    *,
    org_id: str = _ORG_ID,
    user_id: str = _USER_ID,
    secret: str = "test-auth-secret",
) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "org_id": org_id,
                    "user_id": user_id,
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


def _install_capturing_forwarder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if target == "ai_backend" and method == "GET" and path == _PATH:
            captured.append(
                {"method": method, "path": path, "params": kwargs.get("params")}
            )
            return {"v": 1, "items": [], "agents": []}
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def test_facade_proxies_pending_work_with_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(_PATH, headers={"authorization": _bearer()})

    assert response.status_code == 200, response.text
    assert response.json() == {"v": 1, "items": [], "agents": []}
    assert len(captured) == 1
    assert captured[0]["method"] == "GET"
    assert captured[0]["path"] == _PATH
    # The verified session's identity is injected — the caller's own queue only.
    assert captured[0]["params"] == {"org_id": _ORG_ID, "user_id": _USER_ID}


def test_facade_requires_bearer() -> None:
    client = TestClient(create_app(FacadeSettings()))
    response = client.get(_PATH)
    assert response.status_code == 401


def test_facade_surfaces_upstream_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        raise HTTPException(status_code=404, detail="not found")

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.get(_PATH, headers={"authorization": _bearer()})

    assert response.status_code == 404
    assert response.json()["detail"] == "not found"
