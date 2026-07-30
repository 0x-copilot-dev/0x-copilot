"""Tests for the public ``/v1/connectors`` facade proxy — access-mode PATCH.

PRD-06 DoD 7. Mirrors ``test_todos_proxy.py`` setup. Asserts the facade
forwards ``PATCH /v1/connectors/{id}/access-mode`` to
``{backend}/v1/connectors/{id}/access-mode`` with the verified identity in
``org_id``/``user_id`` query params + ``FacadeAuthenticator.service_headers``,
and that the client-supplied body is forwarded UNMODIFIED.
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


_CONNECTOR_BODY = {
    "connector": {
        "id": "conn_1",
        "tenant_id": "org_acme",
        "slug": "gmail",
        "display_name": "Gmail",
        "description": "",
        "status": "connected",
        "status_reason": None,
        "access_mode": "off",
        "owner_user_id": "usr_sarah",
        "scopes": [],
        "last_sync_at": None,
        "last_error_at": None,
        "created_at": "2026-07-18T00:00:00+00:00",
        "updated_at": "2026-07-18T00:00:00+00:00",
    }
}


class TestAccessModeProxy:
    def test_patch_access_mode_proxies_to_backend(self, monkeypatch) -> None:
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

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def patch(
                self, url, *, params=None, json=None, headers=None, timeout=None
            ):
                captured.append(
                    {
                        "url": url,
                        "params": dict(params or {}),
                        "json": json,
                        "headers": dict(headers or {}),
                    }
                )
                return httpx.Response(200, json=_CONNECTOR_BODY)

        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        body = {"access_mode": "off"}
        resp = client.patch(
            "/v1/connectors/conn_1/access-mode",
            json=body,
            headers=_bearer_headers(monkeypatch),
        )
        assert resp.status_code == 200
        assert resp.json() == _CONNECTOR_BODY

        call = captured[-1]
        assert call["url"] == ("http://backend.local/v1/connectors/conn_1/access-mode")
        assert call["params"]["org_id"] == "org_acme"
        assert call["params"]["user_id"] == "usr_sarah"
        # Body forwarded UNMODIFIED.
        assert call["json"] == body
        headers = {k.lower(): v for k, v in call["headers"].items()}
        assert headers["x-enterprise-service-token"] == "test-service-token"
        assert headers["x-enterprise-org-id"] == "org_acme"
        assert headers["x-enterprise-user-id"] == "usr_sarah"

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.patch(
            "/v1/connectors/conn_1/access-mode", json={"access_mode": "off"}
        )
        assert resp.status_code == 401


class TestRemoveProxy:
    """``DELETE /v1/connectors/{id}`` — 204 passthrough, errors preserved."""

    @staticmethod
    def _fake_client(delete_response: httpx.Response, captured: list):
        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return None

            async def post(self, url, *, json=None, headers=None, timeout=None):
                return _touch_response()

            async def get(self, url, *, params=None, headers=None, timeout=None):
                return _touch_response()

            async def delete(self, url, *, params=None, headers=None, timeout=None):
                captured.append(
                    {
                        "url": url,
                        "params": dict(params or {}),
                        "headers": dict(headers or {}),
                    }
                )
                return delete_response

        return _FakeAsyncClient

    def test_delete_proxies_to_backend_and_returns_204(self, monkeypatch) -> None:
        captured: list[dict[str, object]] = []
        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient",
            self._fake_client(httpx.Response(204), captured),
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete(
            "/v1/connectors/conn_1", headers=_bearer_headers(monkeypatch)
        )

        assert resp.status_code == 204
        assert resp.content == b""
        call = captured[-1]
        assert call["url"] == "http://backend.local/v1/connectors/conn_1"
        assert call["params"]["org_id"] == "org_acme"
        assert call["params"]["user_id"] == "usr_sarah"
        headers = {k.lower(): v for k, v in call["headers"].items()}
        assert headers["x-enterprise-service-token"] == "test-service-token"
        assert headers["x-enterprise-org-id"] == "org_acme"
        assert headers["x-enterprise-user-id"] == "usr_sarah"

    def test_upstream_403_is_not_flattened_into_a_204(self, monkeypatch) -> None:
        # A silent 204 on a refused delete is the worst outcome: the UI would
        # drop the row and the tool would still be connected.
        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient",
            self._fake_client(
                httpx.Response(403, json={"detail": "owner_or_admin_only"}), []
            ),
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete(
            "/v1/connectors/conn_1", headers=_bearer_headers(monkeypatch)
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "owner_or_admin_only"

    def test_upstream_404_is_preserved(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "backend_facade.http_client.httpx.AsyncClient",
            self._fake_client(
                httpx.Response(404, json={"detail": "connector_not_found"}), []
            ),
        )

        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        resp = client.delete(
            "/v1/connectors/conn_1", headers=_bearer_headers(monkeypatch)
        )

        assert resp.status_code == 404

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local"))
        )
        assert client.delete("/v1/connectors/conn_1").status_code == 401
