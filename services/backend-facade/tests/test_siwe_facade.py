"""Facade-level tests for the SIWE (Sign-In-With-Ethereum) surfaces.

Pins the public contract for the two wallet-login proxies:

* ``POST /v1/auth/siwe/nonce``  — forwards ``{address, chain_id}`` (plus
  server-derived ip / user_agent) to the backend internal route with the
  anonymous service headers; body comes back verbatim.
* ``POST /v1/auth/siwe/verify`` — forwards ``{message, signature}`` the
  same way; the successful body is the same session-establishing shape
  the OIDC callback returns, and backend detail codes (nonce_invalid,
  signature_invalid, self_signup_disabled, ...) surface untouched.

The backend is mocked (same pattern as ``test_google_auth_facade.py``);
the facade's only job here is to be a thin, unauthenticated proxy.
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


_ADDRESS = "0x8ba1f109551bD432803012645Ac136ddd64DBA72"

_VERIFY_OK_BODY = {
    "user_id": "usr_wallet1",
    "session_id": "sid_wallet1",
    "bearer_token": "sid_wallet1.signature",
    "expires_at": "2030-01-01T00:00:00Z",
    "return_to": None,
    "requires_mfa": False,
}


class TestSiweNonceProxy:
    def test_forwards_body_with_anonymous_service_headers(
        self, env, monkeypatch
    ) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(
                200,
                json={"nonce": "ab12" * 8, "expires_at": "2030-01-01T00:00:00Z"},
            )

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().post(
            "/v1/auth/siwe/nonce",
            json={"address": _ADDRESS, "chain_id": 8453},
        )

        assert response.status_code == 200
        assert set(response.json()) == {"nonce", "expires_at"}
        call = captured[-1]
        assert call["url"].endswith("/internal/v1/auth/siwe/nonce")
        assert call["json"]["address"] == _ADDRESS
        assert call["json"]["chain_id"] == 8453
        # Server-derived context rides along; no client override possible.
        assert "ip" in call["json"] and "user_agent" in call["json"]
        # Anonymous service headers — no bearer required for the ramp.
        assert call["headers"]["x-enterprise-service-token"] == _TEST_SERVICE_TOKEN
        assert call["headers"]["x-enterprise-org-id"] == "-"
        assert call["headers"]["x-enterprise-user-id"] == "anonymous"

    def test_backend_error_codes_surface_verbatim(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(400, json={"detail": "chain_not_allowed"})

        _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().post(
            "/v1/auth/siwe/nonce",
            json={"address": _ADDRESS, "chain_id": 999},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "chain_not_allowed"

    def test_invalid_address_422_surfaces(self, env, monkeypatch) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(422, json={"detail": "invalid_address"})

        _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().post(
            "/v1/auth/siwe/nonce",
            json={"address": "0xzz", "chain_id": 1},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid_address"


class TestSiweVerifyProxy:
    def test_success_returns_oidc_callback_shaped_session(
        self, env, monkeypatch
    ) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(200, json=_VERIFY_OK_BODY)

        captured = _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().post(
            "/v1/auth/siwe/verify",
            json={"message": "localhost:5173 wants ...", "signature": "0xabc"},
        )

        assert response.status_code == 200
        body = response.json()
        # Exact session-handoff shape the OIDC callback returns.
        assert set(body) == {
            "user_id",
            "session_id",
            "bearer_token",
            "expires_at",
            "return_to",
            "requires_mfa",
        }
        assert body["bearer_token"] == "sid_wallet1.signature"
        call = captured[-1]
        assert call["url"].endswith("/internal/v1/auth/siwe/verify")
        assert call["json"]["message"] == "localhost:5173 wants ..."
        assert call["json"]["signature"] == "0xabc"
        assert call["headers"]["x-enterprise-org-id"] == "-"
        assert call["headers"]["x-enterprise-service-token"] == _TEST_SERVICE_TOKEN

    @pytest.mark.parametrize(
        ("status_code", "detail"),
        [
            (400, "nonce_invalid"),
            (400, "nonce_expired"),
            (400, "signature_invalid"),
            (400, "domain_mismatch"),
            (400, "chain_not_allowed"),
            (400, "expired_message"),
            (403, "self_signup_disabled"),
        ],
    )
    def test_error_detail_codes_surface_verbatim(
        self, env, monkeypatch, status_code: int, detail: str
    ) -> None:
        del env

        def _respond(**_kwargs) -> httpx.Response:
            return httpx.Response(status_code, json={"detail": detail})

        _install_fake_backend(monkeypatch, response_factory=_respond)
        response = _client().post(
            "/v1/auth/siwe/verify",
            json={"message": "m", "signature": "0x00"},
        )
        assert response.status_code == status_code
        assert response.json()["detail"] == detail


class TestSiweRoutesRegistered:
    def test_paths_present_without_bearer_requirement(self, env) -> None:
        del env
        app = create_app(settings=FacadeSettings())
        paths = app.openapi()["paths"]
        assert "/v1/auth/siwe/nonce" in paths
        assert "/v1/auth/siwe/verify" in paths
