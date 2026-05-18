"""Tests for the ``/v1/settings/*`` facade proxy (Phase 12 P12-A6+A7).

Six endpoints across three namespaces. Mirrors ``test_tool_routes_proxy.py``
setup. Covers GET + PATCH on each namespace + identity forwarding +
upstream-error propagation.
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


_USER_NOTIF_BODY = {
    "user_id": "usr_sarah",
    "destinations_enabled": {"inbox": True},
    "quiet_hours": {
        "enabled": False,
        "from_local": "20:00",
        "to_local": "08:00",
        "tz": "UTC",
    },
    "updated_at": "2026-05-18T00:00:00+00:00",
}


_WS_NOTIF_BODY = {
    "destinations_enabled": {"inbox": True},
    "quiet_hours": {
        "enabled": False,
        "from_local": "20:00",
        "to_local": "08:00",
        "tz": "UTC",
    },
    "updated_at": "2026-05-18T00:00:00+00:00",
    "updated_by_user_id": "usr_admin",
}


_WEBHOOK_SEC_BODY = {
    "default_hmac_on": True,
    "require_ip_allowlist": False,
    "max_secret_age_days": 0,
    "updated_at": "2026-05-18T00:00:00+00:00",
    "updated_by_user_id": "usr_admin",
}


class TestUserNotificationsProxy:
    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        assert client.get("/v1/settings/notifications").status_code == 401

    def test_get_proxies(self, monkeypatch) -> None:
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
                    {"url": url, "params": dict(params), "headers": dict(headers)}
                )
                return httpx.Response(200, json=_USER_NOTIF_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/settings/notifications", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200
        assert resp.json()["destinations_enabled"]["inbox"] is True

        call = captured[0]
        assert call["url"].endswith("/v1/settings/notifications")
        assert call["params"]["org_id"] == "org_acme"
        assert call["params"]["user_id"] == "usr_sarah"

        downstream = {k.lower(): v for k, v in call["headers"].items()}
        assert downstream["x-enterprise-service-token"] == "test-service-token"
        assert downstream["x-enterprise-org-id"] == "org_acme"
        assert downstream["x-enterprise-user-id"] == "usr_sarah"

    def test_patch_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                captured.append({"url": url, "json": json, "params": dict(params)})
                return httpx.Response(200, json=_USER_NOTIF_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/settings/notifications",
            json={"destinations_enabled": {"inbox": True}},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        call = captured[0]
        assert call["url"].endswith("/v1/settings/notifications")
        assert call["json"]["destinations_enabled"] == {"inbox": True}
        assert call["params"]["org_id"] == "org_acme"


class TestWorkspaceNotificationsProxy:
    def test_get_proxies(self, monkeypatch) -> None:
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
                captured.append({"url": url, "params": dict(params)})
                return httpx.Response(200, json=_WS_NOTIF_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/settings/workspace/notifications",
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json()["updated_by_user_id"] == "usr_admin"
        assert captured[0]["url"].endswith("/v1/settings/workspace/notifications")

    def test_patch_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                captured.append({"url": url, "json": json})
                return httpx.Response(200, json=_WS_NOTIF_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/settings/workspace/notifications",
            json={"destinations_enabled": {"inbox": True}},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert captured[0]["url"].endswith("/v1/settings/workspace/notifications")


class TestWebhookSecurityProxy:
    def test_get_proxies(self, monkeypatch) -> None:
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
                captured.append({"url": url})
                return httpx.Response(200, json=_WEBHOOK_SEC_BODY)

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/settings/security/webhooks", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 200
        assert resp.json()["default_hmac_on"] is True
        assert captured[0]["url"].endswith("/v1/settings/security/webhooks")

    def test_patch_proxies_body(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []

        class _Fake:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params, json=None, headers=None, timeout=None
            ):
                captured.append({"url": url, "json": json})
                return httpx.Response(
                    200, json={**_WEBHOOK_SEC_BODY, "max_secret_age_days": 90}
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/settings/security/webhooks",
            json={"max_secret_age_days": 90},
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200, resp.text
        assert captured[0]["url"].endswith("/v1/settings/security/webhooks")
        assert captured[0]["json"]["max_secret_age_days"] == 90

    def test_upstream_403_propagates(self, monkeypatch) -> None:
        """Backend ``SettingsAccessDenied`` -> 403, must propagate untouched."""

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
                return httpx.Response(
                    403, json={"detail": "Workspace-scoped settings require admin."}
                )

        monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _Fake)

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.get(
            "/v1/settings/security/webhooks", headers=_bearer_headers(monkeypatch)
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"]
