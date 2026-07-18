"""AC9 — facade proxy for the desktop-only connector OAuth transport.

Mirrors ``test_tool_routes_proxy.py`` setup: HMAC bearer + a fake
``httpx.AsyncClient`` that captures outbound calls. Asserts the three desktop
routes reach the backend with the verified identity (never client-supplied),
that the facade forwards the desktop body verbatim and never holds a token, and
— the web-compat regression — that the shipped web ``start-oauth`` /
``oauth-callback`` routes still forward to their unchanged backend paths.
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


class _Capture:
    """Fake AsyncClient recording GET/POST and returning a canned body."""

    calls: list[dict[str, object]] = []
    get_body: dict[str, object] = {}
    post_body: dict[str, object] = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args, **kwargs):
        return None

    async def post(self, url, *, json=None, params=None, headers=None, timeout=None):
        # The touch verification call also POSTs; distinguish by URL.
        if "touch" in url:
            return _touch_response()
        type(self).calls.append(
            {
                "method": "POST",
                "url": url,
                "json": json,
                "params": list(params or []),
                "headers": dict(headers or {}),
            }
        )
        return httpx.Response(200, json=type(self).post_body)

    async def get(self, url, *, params, headers, timeout=None):
        pairs = list(params.items()) if isinstance(params, dict) else list(params)
        type(self).calls.append(
            {
                "method": "GET",
                "url": url,
                "params": pairs,
                "headers": dict(headers),
            }
        )
        return httpx.Response(200, json=type(self).get_body)


def _install(monkeypatch, *, get_body=None, post_body=None) -> type[_Capture]:
    cls = type(
        "_Cap",
        (_Capture,),
        {"calls": [], "get_body": get_body or {}, "post_body": post_body or {}},
    )
    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", cls)
    return cls


def _client() -> TestClient:
    return TestClient(create_app(FacadeSettings(backend_url="http://backend.local")))


class TestDesktopCatalogProxy:
    def test_catalog_forwards_identity(self, monkeypatch) -> None:
        cap = _install(monkeypatch, get_body={"entries": []})
        resp = _client().get(
            "/v1/connectors/desktop/catalog", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200
        assert resp.json() == {"entries": []}
        call = next(c for c in cap.calls if c["method"] == "GET")
        assert call["url"].endswith("/v1/connectors/desktop/catalog")
        assert ("org_id", "org_acme") in call["params"]
        assert ("user_id", "usr_sarah") in call["params"]
        headers = {k.lower(): v for k, v in call["headers"].items()}
        assert headers["x-enterprise-service-token"] == "test-service-token"


class TestDesktopStartOAuthProxy:
    def test_start_forwards_body_and_slug(self, monkeypatch) -> None:
        body = {
            "oauth_session_id": "state-abc",
            "authorization_url": "https://idp.example/authorize?state=state-abc",
            "state": "state-abc",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "requested_permissions": ["read:jira-work"],
        }
        cap = _install(monkeypatch, post_body=body)
        req_body = {
            "callback": {
                "kind": "desktop_loopback",
                "port": 53123,
                "path": "/connectors/oauth/cb",
            },
            "requested_product_scope": "read",
        }
        resp = _client().post(
            "/v1/connectors/atlassian/desktop/start-oauth",
            headers=_bearer_headers(monkeypatch),
            json=req_body,
        )
        assert resp.status_code == 200
        assert resp.json() == body
        call = next(c for c in cap.calls if c["method"] == "POST")
        assert call["url"].endswith("/v1/connectors/atlassian/desktop/start-oauth")
        # Facade forwards the desktop body verbatim (no token minted or held).
        assert call["json"] == req_body


class TestDesktopCallbackProxy:
    def test_callback_forwards_code_state(self, monkeypatch) -> None:
        result = {
            "server_id": "seed:atlassian",
            "connector_slug": "atlassian",
            "display_group": "Atlassian/Jira",
            "auth_state": "authenticated",
        }
        cap = _install(monkeypatch, post_body=result)
        req_body = {
            "oauth_session_id": "state-abc",
            "state": "state-abc",
            "code": "auth-code-123",
        }
        resp = _client().post(
            "/v1/connectors/desktop/oauth-callback",
            headers=_bearer_headers(monkeypatch),
            json=req_body,
        )
        assert resp.status_code == 200
        assert resp.json() == result
        # No provider token in the response the facade returns.
        assert "access_token" not in resp.text
        call = next(c for c in cap.calls if c["method"] == "POST")
        assert call["url"].endswith("/v1/connectors/desktop/oauth-callback")
        assert call["json"] == req_body


class TestWebFlowUnchanged:
    """Regression: the shipped web OAuth routes still forward unchanged."""

    def test_web_start_oauth_untouched(self, monkeypatch) -> None:
        cap = _install(
            monkeypatch,
            post_body={"authorization_url": "https://idp/auth", "state": "s"},
        )
        resp = _client().post(
            "/v1/connectors/gmail/start-oauth",
            headers=_bearer_headers(monkeypatch),
            json={},
        )
        assert resp.status_code == 200
        call = next(c for c in cap.calls if c["method"] == "POST")
        # Still the ORIGINAL web path — no "desktop" segment.
        assert call["url"].endswith("/v1/connectors/gmail/start-oauth")
        assert "/desktop/" not in call["url"]

    def test_web_oauth_callback_untouched(self, monkeypatch) -> None:
        cap = _install(monkeypatch, post_body={"id": "conn_1", "slug": "gmail"})
        resp = _client().post(
            "/v1/connectors/oauth-callback",
            headers=_bearer_headers(monkeypatch),
            json={"code": "c", "state": "s"},
        )
        assert resp.status_code == 200
        call = next(c for c in cap.calls if c["method"] == "POST")
        assert call["url"].endswith("/v1/connectors/oauth-callback")
        assert "/desktop/" not in call["url"]
