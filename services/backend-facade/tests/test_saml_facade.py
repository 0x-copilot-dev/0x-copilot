"""Facade-level smoke tests for /v1/auth/saml/* (A5).

These pin the public contract of the three SAML facade endpoints: start
(redirect + json), ACS (form parsing + JSON forwarding), metadata (XML
content-type pass-through). The backend's response is mocked because the
facade has no SAML state of its own — its job is to be a thin proxy.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from backend_facade import auth_routes as auth_routes_module
from backend_facade.app import create_app
from backend_facade.settings import FacadeSettings


_TEST_SERVICE_TOKEN = "test-service-token"


@pytest.fixture
def env(monkeypatch) -> None:
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "x" * 48)
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)


def _install_fake_backend(
    monkeypatch,
    *,
    response_factory,
) -> list[dict[str, object]]:
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

    monkeypatch.setattr(auth_routes_module.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


class TestSamlStart:
    def test_redirect_format_returns_302(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "auth_id": "sac_x",
                    "request_id": "req_x",
                    "sso_url": "https://idp.example/sso?SAMLRequest=...",
                    "request_xml": "<x/>",
                    "binding": "HTTP-Redirect",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            )

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.get(
            "/v1/auth/saml/prv_acme/start",
            params={"org_id": "org_acme", "relay_state": "/dashboard"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith("https://idp.example/sso")
        # Backend received the SAML authorize request.
        assert captured[0]["url"].endswith("/internal/v1/auth/saml/prv_acme/authorize")
        assert captured[0]["json"]["org_id"] == "org_acme"
        assert captured[0]["json"]["relay_state"] == "/dashboard"

    def test_json_format_returns_payload(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "auth_id": "sac_x",
                    "request_id": "req_x",
                    "sso_url": "https://idp.example/sso?SAMLRequest=...",
                    "request_xml": '<samlp:AuthnRequest ID="req_x"/>',
                    "binding": "HTTP-Redirect",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            )

        _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.get(
            "/v1/auth/saml/prv_acme/start",
            params={"org_id": "org_acme", "format": "json"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == "req_x"
        assert body["request_xml"].startswith("<samlp:AuthnRequest")


class TestSamlAcs:
    def test_form_post_forwards_to_backend(self, env, monkeypatch) -> None:
        del env

        def _respond(*, verb, url, json=None, params=None) -> httpx.Response:
            del verb, url, params
            assert json is not None
            assert json["saml_response"] == "SAMLResponse-base64-blob"
            assert json["relay_state"] == "/return"
            return httpx.Response(
                200,
                json={
                    "user_id": "usr_alice",
                    "session_id": "sid_x",
                    "bearer_token": "tok.sig",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "relay_state": "/return",
                    "requires_mfa": False,
                },
            )

        _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.post(
            "/v1/auth/saml/prv_acme/acs",
            data={
                "SAMLResponse": "SAMLResponse-base64-blob",
                "RelayState": "/return",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["bearer_token"] == "tok.sig"

    def test_acs_missing_form_returns_400(self, env, monkeypatch) -> None:
        del env, monkeypatch
        # The 400 fires before any backend call, so we don't need to mock
        # the HTTP transport.
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.post("/v1/auth/saml/prv_acme/acs", data={})
        assert response.status_code == 400


class TestSamlMetadata:
    def test_metadata_returns_xml(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                content=b'<?xml version="1.0"?><EntityDescriptor entityID="x"/>',
                headers={"content-type": "application/xml"},
            )

        _install_fake_backend(monkeypatch, response_factory=_respond)
        client = TestClient(
            create_app(FacadeSettings(backend_url="http://backend.local")),
        )
        response = client.get("/v1/auth/saml/prv_acme/metadata")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        assert b"EntityDescriptor" in response.content
