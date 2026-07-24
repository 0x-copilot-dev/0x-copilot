"""Facade must proxy the PRD-B4 "Suggest a shape" route to ai-backend.

Passthrough only: method/path/target, the slash-bearing ``surface_id`` re-encoded
for the downstream path, the UNTYPED body forwarded with org/user stamped via
``scoped_payload`` (so the client's ``run_id`` is never dropped), a bearer
required, and an upstream 409/422 surfaced verbatim. Mirrors the capture-
``forward_json`` pattern from ``test_surface_view_proxy``.
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

_ORG_ID = "org_shape_facade"
_USER_ID = "user_shape_facade"
_RUN_ID = "run_shape_1"
_SURFACE_ID = "record://customsrv/custom_tool/x"
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


def _capture(
    monkeypatch: pytest.MonkeyPatch, *, status_code: int = 202
) -> list[dict[str, Any]]:
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
        return {"surface_id": _SURFACE_ID, "status": "requested"}

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def test_facade_proxies_shape_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/shape-request",
        headers={"authorization": _bearer()},
        json={"run_id": _RUN_ID},
    )

    assert response.status_code == 202, response.text
    assert len(captured) == 1
    call = captured[0]
    assert call["method"] == "POST"
    assert call["target"] == "ai_backend"
    # Slash-bearing surface_id is re-encoded so ai-backend's path router matches.
    assert call["path"] == f"/v1/agent/surfaces/{_SURFACE_ID}/shape-request"
    # Untyped body passthrough with org/user stamped — run_id is never dropped.
    assert call["json"]["run_id"] == _RUN_ID
    assert call["json"]["org_id"] == _ORG_ID
    assert call["json"]["user_id"] == _USER_ID


def test_shape_request_requires_bearer() -> None:
    client = TestClient(create_app(FacadeSettings()))
    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/shape-request",
        json={"run_id": _RUN_ID},
    )
    assert response.status_code == 401


def test_shape_request_client_org_user_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A client-sent org/user in the body is overwritten by the verified identity.
    captured = _capture(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/shape-request",
        headers={"authorization": _bearer()},
        json={"run_id": _RUN_ID, "org_id": "attacker", "user_id": "attacker"},
    )

    assert response.status_code == 202, response.text
    call = captured[0]
    assert call["json"]["org_id"] == _ORG_ID
    assert call["json"]["user_id"] == _USER_ID


@pytest.mark.parametrize(
    "status_code,detail",
    [(409, "surface_already_shaped"), (422, "shaping_unavailable")],
)
def test_shape_request_upstream_error_surfaced(
    monkeypatch: pytest.MonkeyPatch, status_code: int, detail: str
) -> None:
    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    client = TestClient(create_app(FacadeSettings()))

    response = client.post(
        f"/v1/agent/surfaces/{_ENCODED}/shape-request",
        headers={"authorization": _bearer()},
        json={"run_id": _RUN_ID},
    )
    assert response.status_code == status_code
    assert response.json()["detail"] == detail
