"""Tests for the ``/v1/palette/search`` facade proxy (Phase 12 P12-A7).

Single route, no SSE. The facade authenticates the caller, forwards
the verified identity in query params + service-token headers, and
preserves multi-value ``filter[*]`` semantics.
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


class TestPaletteRoutesProxy:
    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        assert client.get("/v1/palette/search?q=x").status_code == 401

    def test_search_preserves_query_and_context(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
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
                    {"url": url, "params": list(params), "headers": dict(headers)}
                )
                return httpx.Response(
                    200,
                    json={
                        "groups": [
                            {"kind": "person", "items": []},
                            {"kind": "memory", "items": []},
                        ]
                    },
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/palette/search?q=bob&context=chat&filter[kind]=person&filter[kind]=memory",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["groups"]) == 2

        call = captured[0]
        assert call["url"].endswith("/v1/palette/search")
        pairs = call["params"]
        assert ("org_id", "org_acme") in pairs
        assert ("user_id", "usr_sarah") in pairs
        assert ("q", "bob") in pairs
        assert ("context", "chat") in pairs
        assert pairs.count(("filter[kind]", "person")) == 1
        assert pairs.count(("filter[kind]", "memory")) == 1

        downstream = {k.lower(): v for k, v in call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"

    def test_upstream_error_propagates(self, monkeypatch) -> None:
        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params, headers, timeout=None):
                return httpx.Response(400, json={"detail": "query_too_short"})

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get("/v1/palette/search?q=", headers=_bearer_headers(monkeypatch))
        assert resp.status_code == 400
        assert resp.json()["detail"] == "query_too_short"
