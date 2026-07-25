"""Facade boundary tests for D11 legal-hold management."""

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

_SECRET = "legal-hold-proxy-test-secret"


def _headers(monkeypatch, *, idempotency_key: str | None = None) -> dict[str, str]:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "legal-hold-service-token")
    payload = {
        "org_id": "org_legal_hold_proxy",
        "user_id": "user_retention_admin",
        "roles": ["employee"],
        "permission_scopes": ["runtime:use", "admin:retention"],
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    signature = hmac.new(_SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    headers = {"authorization": f"Bearer {encoded}.{signed}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _touch_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "session_id": "session_legal_hold",
            "org_id": "org_legal_hold_proxy",
            "user_id": "user_retention_admin",
            "roles": ["employee"],
            "permission_scopes": ["runtime:use", "admin:retention"],
            "connector_scopes": {},
            "mfa_satisfied": False,
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )


@pytest.fixture(autouse=True)
def _clear_touch_cache() -> None:
    FacadeAuthenticator.touch_cache().clear()


def test_legal_hold_create_forwards_verified_identity_and_idempotency_only(
    monkeypatch,
) -> None:
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
                    "params": dict(params),
                    "json": json,
                    "headers": {key.lower(): value for key, value in headers.items()},
                }
            )
            return httpx.Response(
                201,
                json={
                    "id": "lh_proxy",
                    "scope": "org",
                    "target_user_id": None,
                    "target_conversation_id": None,
                    "reason_code": "legal_request",
                    "created_by_user_id": "user_retention_admin",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "released_by_user_id": None,
                    "released_at": None,
                    "revision": 1,
                    "replayed": False,
                },
            )

    monkeypatch.setattr("backend_facade.http_client.httpx.AsyncClient", _FakeClient)
    client = TestClient(create_app(FacadeSettings(backend_url="http://backend.local")))

    response = client.post(
        "/v1/retention/legal-holds?org_id=attacker-org",
        headers=_headers(monkeypatch, idempotency_key="facade-hold-001"),
        json={"scope": "org", "reason_code": "legal_request"},
    )

    assert response.status_code == 201
    assert len(captured) == 1
    forwarded = captured[0]
    assert forwarded["method"] == "POST"
    assert str(forwarded["url"]).endswith("/v1/retention/legal-holds")
    assert forwarded["params"] == {
        "org_id": "org_legal_hold_proxy",
        "user_id": "user_retention_admin",
    }
    assert forwarded["json"] == {"scope": "org", "reason_code": "legal_request"}
    headers = forwarded["headers"]
    assert isinstance(headers, dict)
    assert headers["idempotency-key"] == "facade-hold-001"
    assert headers["x-enterprise-org-id"] == "org_legal_hold_proxy"
    assert headers["x-enterprise-user-id"] == "user_retention_admin"
