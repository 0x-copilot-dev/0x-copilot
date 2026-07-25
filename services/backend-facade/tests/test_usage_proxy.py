"""Contract tests for the identity-scoped usage call-detail facade route."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings

_SECRET = "usage-proxy-test-secret"


def _headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "usage-proxy-service-token")
    payload = {
        "org_id": "org_usage",
        "user_id": "user_usage",
        "roles": ["employee"],
        "permission_scopes": ["runtime:use"],
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    signature = hmac.new(_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return {"authorization": f"Bearer {encoded}.{signed}"}


def _touch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "session_id": "session_usage",
            "org_id": "org_usage",
            "user_id": "user_usage",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
            "connector_scopes": {},
            "mfa_satisfied": False,
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


def test_usage_run_calls_forwards_only_verified_identity(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url, *, json=None, headers=None, timeout=None):
            return _touch_response()

        async def request(
            self, method, url, *, params=None, json=None, headers=None, timeout=None
        ):
            captured.append(
                {
                    "method": method,
                    "url": url,
                    "params": list(params.items()),
                    "headers": {key.lower(): value for key, value in headers.items()},
                }
            )
            return httpx.Response(200, json={"run_id": "run-1", "calls": []})

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _FakeClient)
    client = TestClient(create_app(FacadeSettings(backend_url="http://backend.local")))

    response = client.get("/v1/usage/runs/run-1/calls", headers=_headers(monkeypatch))

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "calls": []}
    assert len(captured) == 1
    call = captured[0]
    assert call["method"] == "GET"
    assert str(call["url"]).endswith("/v1/usage/runs/run-1/calls")
    assert dict(call["params"]) == {
        "org_id": "org_usage",
        "user_id": "user_usage",
    }
    headers = call["headers"]
    assert isinstance(headers, dict)
    assert headers["x-enterprise-service-token"] == "usage-proxy-service-token"
    assert headers["x-enterprise-org-id"] == "org_usage"
    assert headers["x-enterprise-user-id"] == "user_usage"


def test_usage_run_calls_forwards_upstream_404(monkeypatch) -> None:
    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def post(self, url, *, json=None, headers=None, timeout=None):
            return _touch_response()

        async def request(
            self, method, url, *, params=None, json=None, headers=None, timeout=None
        ):
            return httpx.Response(404, json={"detail": "run not found"})

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _FakeClient)
    client = TestClient(create_app(FacadeSettings(backend_url="http://backend.local")))

    response = client.get("/v1/usage/runs/foreign/calls", headers=_headers(monkeypatch))

    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}
