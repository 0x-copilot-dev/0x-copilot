"""Tests for the public /v1/me/* facade routes (PR 2.2)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import backend_facade.me_routes as me_routes_module
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


class TestListMyWorkspacesProxy:
    def test_proxies_to_backend_with_identity(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        upstream_body = {
            "workspaces": [
                {
                    "org_id": "org_acme",
                    "display_name": "Acme",
                    "slug": "acme",
                    "role": "Admin",
                    "member_count": 47,
                    "last_active_at": "2026-05-05T15:51:00+00:00",
                    "is_current": True,
                }
            ]
        }

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                # touch endpoint — return canonical session for verify_with_touch
                captured.append(
                    {"method": "POST", "url": url, "headers": dict(headers)}
                )
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

            async def get(self, url, *, params, headers, timeout=None):
                captured.append(
                    {
                        "method": "GET",
                        "url": url,
                        "params": dict(params),
                        "headers": dict(headers),
                    }
                )
                return httpx.Response(200, json=upstream_body)

        monkeypatch.setattr(me_routes_module.httpx, "AsyncClient", _FakeAsyncClient)
        # auth_routes.verify_with_touch uses the auth_routes httpx client by
        # default — but verify_with_touch is itself called via the helper that
        # uses whatever httpx client is passed in. The patch on me_routes is
        # what _our_ proxy uses.
        import backend_facade.auth_routes as auth_routes_module

        monkeypatch.setattr(auth_routes_module.httpx, "AsyncClient", _FakeAsyncClient)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get("/v1/me/workspaces", headers=_bearer_headers(monkeypatch))

        assert response.status_code == 200
        assert response.json() == upstream_body
        # Exactly one GET to the backend's /me/workspaces endpoint. The
        # bearer is HMAC-only (no `sid`) so verify_with_touch short-circuits
        # without a /touch call — the canonical path for back-compat tokens.
        get_call = next(c for c in captured if c["method"] == "GET")
        assert get_call["url"].endswith("/internal/v1/me/workspaces")
        assert get_call["params"] == {
            "org_id": "org_acme",
            "user_id": "usr_sarah",
        }
        # Service-token + identity headers passed downstream.
        downstream_headers = {k.lower(): v for k, v in get_call["headers"].items()}
        assert downstream_headers["x-enterprise-service-token"] == (
            "test-service-token"
        )
        assert downstream_headers["x-enterprise-org-id"] == "org_acme"
        assert downstream_headers["x-enterprise-user-id"] == "usr_sarah"

    def test_unauthenticated_request_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get("/v1/me/workspaces")
        # No bearer header → 401 from FacadeAuthenticator before any upstream call.
        assert response.status_code == 401

    def test_upstream_4xx_propagates(self, monkeypatch) -> None:
        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
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

            async def get(self, url, *, params, headers, timeout=None):
                return httpx.Response(404, json={"detail": "workspace_not_found"})

        monkeypatch.setattr(me_routes_module.httpx, "AsyncClient", _FakeAsyncClient)
        import backend_facade.auth_routes as auth_routes_module

        monkeypatch.setattr(auth_routes_module.httpx, "AsyncClient", _FakeAsyncClient)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get("/v1/me/workspaces", headers=_bearer_headers(monkeypatch))
        assert response.status_code == 404
