"""Facade must proxy ``GET /v1/agent/runs/{run_id}/receipt/export`` verbatim (PRD-E3).

Pure passthrough: the facade authenticates, stamps the caller's scoped
``org_id``/``user_id`` params, forwards to ai-backend with ``target="ai_backend"``,
and returns the upstream body. Upstream 404 / 409 / 503 ride the standard
``forward_json`` error passthrough. No bearer ⇒ 401 (never reaches upstream).
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

_ORG_ID = "org_receipt_facade"
_USER_ID = "user_receipt_facade"
_RUN_ID = "run_abc123"
_EXPECTED_PATH = f"/v1/agent/runs/{_RUN_ID}/receipt/export"


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
    """Intercept ``forward_json`` and capture the outbound request shape."""

    captured: list[dict[str, Any]] = []

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        if target == "ai_backend" and method == "GET" and path == _EXPECTED_PATH:
            captured.append(
                {"method": method, "path": path, "params": kwargs.get("params")}
            )
            return {
                "export_version": 1,
                "run_id": _RUN_ID,
                "generated_at": "2026-01-01T00:00:00+00:00",
                "receipt": {},
                "rows": [],
                "head_hash": "deadbeef",
            }
        raise AssertionError(
            f"unexpected forward: target={target} method={method} path={path}"
        )

    monkeypatch.setattr(facade_app, "forward_json", _forward)
    return captured


def _install_upstream_error(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    """Intercept ``forward_json`` to raise the upstream status verbatim."""

    async def _forward(_app, method, path, *, target, **kwargs):  # noqa: ARG001
        raise HTTPException(status_code, "upstream detail")

    monkeypatch.setattr(facade_app, "forward_json", _forward)


def _get(client: TestClient, *, with_bearer: bool = True):
    headers = {"authorization": _bearer()} if with_bearer else {}
    return client.get(_EXPECTED_PATH, headers=headers)


def test_facade_proxies_receipt_export_with_scoped_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = _get(client)

    assert response.status_code == 200
    assert response.json()["run_id"] == _RUN_ID
    assert len(captured) == 1
    call = captured[0]
    assert call["method"] == "GET"
    assert call["path"] == _EXPECTED_PATH
    # Scoped identity is stamped onto the outbound params.
    assert call["params"]["org_id"] == _ORG_ID
    assert call["params"]["user_id"] == _USER_ID


def test_missing_bearer_is_401_and_never_forwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_capturing_forwarder(monkeypatch)
    client = TestClient(create_app(FacadeSettings()))

    response = _get(client, with_bearer=False)

    assert response.status_code == 401
    assert captured == []


@pytest.mark.parametrize("status_code", [404, 409, 503])
def test_upstream_error_passthrough(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _install_upstream_error(monkeypatch, status_code)
    client = TestClient(create_app(FacadeSettings()))

    response = _get(client)

    assert response.status_code == status_code
