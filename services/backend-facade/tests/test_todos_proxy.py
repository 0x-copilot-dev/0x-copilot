"""Tests for the public ``/v1/todos`` facade proxy (Phase 3 P3-A1).

Mirrors ``test_home_proxy.py`` setup. Asserts:

* Unauthenticated request rejected (401).
* Authenticated request proxies to backend with the verified identity.
* Multi-value ``filter[status]`` query params survive the proxy
  (cross-audit §1.5 OR semantics).
* Upstream 4xx propagates through (cross-tenant 404, etc.).
"""

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


_TEST_SECRET = "test-auth-secret"


def _hmac_token(payload: dict[str, object], secret: str) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret.encode(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def _bearer_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _TEST_SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    token = _hmac_token(
        {
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
        },
        _TEST_SECRET,
    )
    return {"authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


_TODO_LIST_BODY = {
    "items": [
        {
            "id": "todo_1",
            "tenant_id": "org_acme",
            "owner_user_id": "usr_sarah",
            "project_id": None,
            "text": "ship it",
            "status": "open",
            "priority": "med",
            "source": {"kind": "user"},
            "created_at": "2026-05-18T00:00:00+00:00",
            "updated_at": "2026-05-18T00:00:00+00:00",
        }
    ],
}


def _touch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "session_id": "sid_test",
            "org_id": "org_acme",
            "user_id": "usr_sarah",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use"],
            "connector_scopes": {},
            "mfa_satisfied": False,
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )


class TestTodosProxy:
    def test_get_list_proxies_to_backend(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                captured.append(
                    {
                        "method": "GET",
                        "url": url,
                        "params": list(params),
                        "headers": dict(headers),
                    }
                )
                return httpx.Response(200, json=_TODO_LIST_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        # Multi-value filter[status] survives the proxy.
        resp = client.get(
            "/v1/todos?filter[status]=open&filter[status]=done",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json() == _TODO_LIST_BODY

        # Look for the proxied GET. Identity params come first; filter
        # params are forwarded as repeats (preserving OR semantics).
        get_call = next(c for c in captured if c["method"] == "GET")
        assert get_call["url"].endswith("/v1/todos")
        pairs = get_call["params"]
        assert ("org_id", "org_acme") in pairs
        assert ("user_id", "usr_sarah") in pairs
        # Multi-value preserved.
        assert pairs.count(("filter[status]", "open")) == 1
        assert pairs.count(("filter[status]", "done")) == 1

        downstream_headers = {k.lower(): v for k, v in get_call["headers"].items()}
        assert downstream_headers["x-enterprise-service-token"] == (
            "test-service-token"
        )
        assert downstream_headers["x-enterprise-org-id"] == "org_acme"
        assert downstream_headers["x-enterprise-user-id"] == "usr_sarah"

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/todos")
        assert resp.status_code == 401

    def test_upstream_404_propagates(self, monkeypatch) -> None:
        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                if "/touch" in url or "/v1/touch" in url or "session" in url:
                    return _touch_response()
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                if url.endswith("/touch") or "session/touch" in url:
                    return _touch_response()
                return httpx.Response(404, json={"detail": "todo_not_found"})

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                return httpx.Response(404, json={"detail": "todo_not_found"})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/todos/todo_x",
            json={"status": "done"},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 404
