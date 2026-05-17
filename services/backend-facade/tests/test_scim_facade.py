"""Facade-level smoke tests for /scim/v2/* (A7).

The facade is a thin proxy — it pulls the SCIM bearer from the
``Authorization`` header and forwards it as ``x-scim-bearer-token`` to
the backend. These tests pin that wire contract.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


_TEST_SERVICE_TOKEN = "test-service-token"


@pytest.fixture
def env(monkeypatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "x" * 48)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)


def _install_fake_backend(monkeypatch, *, response_factory) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args, **kwargs) -> None:
            return None

        async def request(
            self, *, method, url, params=None, content=None, headers=None, timeout=None
        ):
            captured.append(
                {
                    "method": method,
                    "url": url,
                    "params": params,
                    "content": content,
                    "headers": dict(headers or {}),
                }
            )
            return response_factory(
                method=method, url=url, params=params, content=content
            )

    monkeypatch.setattr(
        "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
    )
    return captured


class TestScimProxyForwarding:
    def test_get_users_forwards_bearer_in_internal_header(
        self, env, monkeypatch
    ) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                    "totalResults": 0,
                    "Resources": [],
                },
                headers={"content-type": "application/scim+json"},
            )

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.get(
            "/scim/v2/Users",
            headers={"authorization": "Bearer scim-secret-token-xyz"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/scim+json")
        assert len(captured) == 1
        forwarded = captured[0]
        assert forwarded["method"] == "GET"
        assert forwarded["url"].endswith("/internal/v1/auth/scim/resource/Users")
        assert forwarded["headers"]["x-scim-bearer-token"] == "scim-secret-token-xyz"
        assert forwarded["headers"]["x-enterprise-service-token"] == _TEST_SERVICE_TOKEN

    def test_post_user_forwards_body(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                201,
                json={"id": "usr_x", "userName": "alice@acme.example"},
                headers={"content-type": "application/scim+json"},
            )

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.post(
            "/scim/v2/Users",
            headers={
                "authorization": "Bearer scim-secret",
                "content-type": "application/scim+json",
            },
            content=b'{"userName":"alice@acme.example"}',
        )
        assert response.status_code == 201
        assert b"alice@acme.example" in response.content
        forwarded = captured[0]
        assert forwarded["content"] == b'{"userName":"alice@acme.example"}'

    def test_missing_bearer_returns_401(self, env, monkeypatch) -> None:
        del env, monkeypatch
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.get("/scim/v2/Users")
        assert response.status_code == 401

    def test_discovery_endpoint_forwards_with_bearer(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "schemas": [
                        "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
                    ]
                },
                headers={"content-type": "application/scim+json"},
            )

        _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.get(
            "/scim/v2/ServiceProviderConfig",
            headers={"authorization": "Bearer scim-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "ServiceProviderConfig" in body["schemas"][0]
