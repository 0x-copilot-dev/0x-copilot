"""Facade-level tests for the global "Continue with Google" surfaces.

Pins the public contract changes that landed with the env-configured
global Google OIDC provider:

* ``GET /v1/auth/providers`` works WITHOUT ``org_id`` (pre-workspace login
  screen) — the facade forwards the ``"-"`` placeholder and the backend
  appends the global provider entry (``provider_id == "google"``).
* ``GET /v1/auth/oidc/google/start`` works WITHOUT ``org_id`` — same
  placeholder forwarding; the backend pins the state to its sentinel org.

The backend is mocked (same pattern as ``test_saml_facade.py``); the
facade's only job here is to be a thin, org-optional proxy.
"""

from __future__ import annotations

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


def _install_fake_backend(monkeypatch, *, response_factory) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args, **kwargs) -> None:
            return None

        async def post(self, url, *, json=None, headers=None, timeout=None):
            captured.append(
                {
                    "verb": "POST",
                    "url": url,
                    "json": json,
                    "headers": dict(headers or {}),
                }
            )
            return response_factory(verb="POST", url=url, json=json)

        async def get(self, url, *, params=None, headers=None, timeout=None):
            captured.append(
                {
                    "verb": "GET",
                    "url": url,
                    "params": params,
                    "headers": dict(headers or {}),
                }
            )
            return response_factory(verb="GET", url=url, params=params)

    monkeypatch.setattr(
        "backend_facade.http_client.httpx.AsyncClient", _FakeAsyncClient
    )
    return captured


def _client() -> TestClient:
    return TestClient(create_app(settings=FacadeSettings()))


_PROVIDERS_BODY = {
    "providers": [
        {
            "provider_id": "google",
            "kind": "oidc",
            "display_name": "Google",
            "enabled": True,
        }
    ]
}


class TestProvidersWithoutOrg:
    def test_org_id_defaults_to_placeholder_and_google_listed(
        self, env, monkeypatch
    ) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(200, json=_PROVIDERS_BODY)

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().get("/v1/auth/providers")

        assert response.status_code == 200
        ids = [p["provider_id"] for p in response.json()["providers"]]
        assert "google" in ids
        # The backend saw the org-less placeholder, with the service token.
        call = captured[-1]
        assert call["params"] == {"org_id": "-"}
        assert call["headers"]["x-enterprise-org-id"] == "-"
        assert call["headers"]["x-enterprise-service-token"] == _TEST_SERVICE_TOKEN

    def test_explicit_org_id_still_forwarded(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(200, json=_PROVIDERS_BODY)

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().get("/v1/auth/providers", params={"org_id": "org_acme"})

        assert response.status_code == 200
        call = captured[-1]
        assert call["params"] == {"org_id": "org_acme"}
        assert call["headers"]["x-enterprise-org-id"] == "org_acme"


class TestGoogleStartWithoutOrg:
    def test_redirects_to_idp_with_placeholder_org(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?x=1",
                    "state": "state-123",
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            )

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().get(
            "/v1/auth/oidc/google/start",
            params={
                "redirect_uri": "https://app.example/v1/auth/oidc/callback",
                "return_to": "/chat",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"].startswith(
            "https://accounts.google.com/o/oauth2/v2/auth"
        )
        call = captured[-1]
        assert call["url"].endswith("/internal/v1/auth/oidc/google/authorize")
        assert call["json"]["org_id"] == "-"
        assert call["json"]["provider_id"] == "google"
        assert call["json"]["return_to"] == "/chat"
        assert call["headers"]["x-enterprise-org-id"] == "-"

    def test_json_format_returns_auth_url_payload(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?x=1",
                    "state": "state-123",
                    "expires_at": "2030-01-01T00:00:00Z",
                },
            )

        _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().get(
            "/v1/auth/oidc/google/start",
            params={
                "redirect_uri": "http://127.0.0.1:8931/callback",
                "format": "json",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["auth_url"].startswith("https://accounts.google.com/")
        assert body["state"] == "state-123"
