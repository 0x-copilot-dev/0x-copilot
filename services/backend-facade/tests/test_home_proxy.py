"""Tests for the public ``GET /v1/home`` facade proxy (Phase 2).

Mirrors ``test_me_routes.py`` setup: HMAC-signed bearer token, fake
httpx.AsyncClient that captures outbound calls. Asserts:

* Unauthenticated request rejected (401).
* Authenticated request proxies to backend with the verified identity
  in query params + service-token headers.
* Upstream 4xx propagates through.
* Response body passes through verbatim.
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


_HOME_BODY = {
    # Phase 9 HomePayload shape from packages/api-types/src/home.ts.
    # The facade is a pass-through proxy — this body is whatever the
    # backend serves; the test asserts it survives the proxy verbatim.
    "greeting": {
        "display_name": "Sarah",
        "time_segment": "morning",
        "tenant_local_date": "2026-05-18",
        "tenant_local_iso": "2026-05-18T09:00:00+00:00",
    },
    "triage": {
        "approvals_waiting": 0,
        "runs_failed_24h": 0,
        "todos_overdue": 0,
        "todos_due_today": 0,
    },
    "today_timeline": {"status": "ok", "data": []},
    "whats_new": {
        "status": "ok",
        "since_iso": "2026-05-17T09:00:00+00:00",
        "data": [],
    },
    "in_flight_projects": {"status": "ok", "data": []},
    "live_activity": {"status": "ok", "data": []},
    "quick_actions": [],
    "cached_at": "2026-05-18T09:00:00+00:00",
    "is_first_run": True,
}


class TestGetHomeProxy:
    def test_proxies_to_backend_with_identity(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_FakeAsyncClient":
                return self

            async def __aexit__(self, *args, **kwargs) -> None:
                return None

            async def post(self, url, *, json, headers, timeout=None):
                # /touch endpoint — returns canonical session.
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
                return httpx.Response(200, json=_HOME_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get("/v1/home", headers=_bearer_headers(monkeypatch))

        assert response.status_code == 200
        assert response.json() == _HOME_BODY

        get_call = next(c for c in captured if c["method"] == "GET")
        assert get_call["url"].endswith("/v1/home")
        assert get_call["params"] == {
            "org_id": "org_acme",
            "user_id": "usr_sarah",
        }
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
        response = client.get("/v1/home")
        assert response.status_code == 401

    def test_stream_route_is_registered(self) -> None:
        """``GET /v1/home/stream`` must be mounted on the facade.

        Pass-through SSE proxies are sensitive to wire framing — the
        canonical drive-the-stream test lives at the backend layer; we
        smoke that the facade has the route at all so a regression in
        wiring is caught here.
        """

        from starlette.routing import Route

        app = create_app(FacadeSettings(backend_url="http://backend.local"))
        match = next(
            (
                route
                for route in app.routes
                if isinstance(route, Route) and route.path == "/v1/home/stream"
            ),
            None,
        )
        assert match is not None, "/v1/home/stream not registered on facade"
        assert "GET" in match.methods

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
                return httpx.Response(404, json={"detail": "user_not_found"})

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        response = client.get("/v1/home", headers=_bearer_headers(monkeypatch))
        assert response.status_code == 404
